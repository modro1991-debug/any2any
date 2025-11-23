import os
import io
import re
import csv
import json
import yaml
import shutil
import subprocess
from pathlib import Path
from typing import Literal

import pandas as pd
import phonenumbers
import vobject

# ---------- temp workspace ----------
BASE_DIR = Path(__file__).parent.resolve()
TMP_DIR = BASE_DIR / "tmp"
TMP_DIR.mkdir(exist_ok=True)

# ---------- external tools ----------
IM_CMD = os.getenv("IMAGEMAGICK_CONVERT", "convert")   # ImageMagick
FFMPEG = os.getenv("FFMPEG", "ffmpeg")                 # FFmpeg
LIBRE = os.getenv("LIBREOFFICE", "libreoffice")        # LibreOffice

# ---------- small helpers ----------
def run(cmd: list[str]) -> None:
    """Run a command and raise a readable error on failure."""
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{e.stderr.decode(errors='ignore')}") from None

def change_ext(p: Path, new_ext: str) -> Path:
    return p.with_suffix("." + new_ext.lstrip("."))

# ============================================================================
#                            BINARY CONVERTERS
# ============================================================================

# ---- Images (and PDF -> image via Ghostscript under ImageMagick) ----
ImageFormat = Literal["jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"]

def convert_image(inp: Path, target_ext: ImageFormat) -> Path:
    outp = change_ext(inp, target_ext)
    # If source is PDF and target is raster, render with a nicer density
    if inp.suffix.lower() == ".pdf" and target_ext.lower() not in (".pdf","pdf"):
        run([IM_CMD, "-density", "150", str(inp), "-strip", str(outp)])
    else:
        run([IM_CMD, str(inp), "-strip", str(outp)])
    return outp

# ---- Audio/Video via FFmpeg ----
AVFormat = Literal["mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"]

def convert_av(inp: Path, target_ext: AVFormat) -> Path:
    outp = change_ext(inp, target_ext)
    # stream copy for some common container-only changes; otherwise re-encode defaults
    # Keep it simple and robust:
    run([FFMPEG, "-y", "-i", str(inp), str(outp)])
    return outp

# ---- Documents via LibreOffice headless ----
DocOut = Literal["pdf","docx","xlsx","pptx","odt","ods","odp"]

def convert_doc(inp: Path, target_ext: DocOut) -> Path:
    # LibreOffice exports to a dir; we then rename/move to our tmp name.
    out_dir = TMP_DIR
    # Map to LO filter names when necessary (mostly not needed, LO infers)
    run([
        LIBRE, "--headless", "--convert-to", target_ext,
        "--outdir", str(out_dir), str(inp)
    ])
    produced = change_ext(out_dir / inp.name, target_ext)
    if not produced.exists():
        # LO sometimes uses uppercase/lowercase or different base naming; search
        candidates = list(out_dir.glob(f"*{'.'+target_ext}"))
        if not candidates:
            raise RuntimeError("LibreOffice did not produce an output file.")
        produced = candidates[0]
    final = change_ext(inp, target_ext)
    if produced != final:
        produced.replace(final)
    return final

# ============================================================================
#                           DATA CONVERTERS (UNIQUE)
# ============================================================================

DATA_IN = {"csv","xlsx","txt","vcf","srt","vtt","json","yaml","yml"}
DATA_OUT = {
    "phonecsv",        # phone number cleaning (CSV output)
    "vcf","csv",       # contacts
    "srt","vtt",       # subtitles
    "json","csv_from_json","json_from_csv",
    "yaml","json_from_yaml","yaml_from_json"
}

def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def _write_text(p: Path, s: str):
    p.write_text(s, encoding="utf-8")

def _change_to(p: Path, new_ext: str) -> Path:
    return p.with_suffix("." + new_ext)

# ---- 1) Phone list cleaner: CSV/XLSX/TXT -> cleaned CSV ----
def data_phone_clean(inp: Path, default_region: str | None = None) -> Path:
    # Load into DataFrame best-effort
    if inp.suffix.lower() == ".xlsx":
        df = pd.read_excel(inp, dtype=str)
    elif inp.suffix.lower() == ".csv":
        df = pd.read_csv(inp, dtype=str, keep_default_na=False, na_filter=False)
    elif inp.suffix.lower() == ".txt":
        df = pd.DataFrame({"value": _read_text(inp).splitlines()})
    else:
        raise RuntimeError("Phone cleaner expects CSV/XLSX/TXT")

    numbers = []
    rows = df.fillna("").astype(str).to_dict("records")
    for row in rows:
        for val in row.values():
            for token in _extract_phone_like_tokens(val):
                parsed = _try_parse_phone(token, default_region)
                numbers.append({
                    "original": token,
                    "valid": bool(parsed),
                    "e164": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164) if parsed else "",
                    "national": phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.NATIONAL) if parsed else "",
                    "country": phonenumbers.region_code_for_number(parsed) if parsed else "",
                    "type": _phone_type(parsed) if parsed else "",
                })

    # dedupe by e164 (or original if parsing failed)
    seen, cleaned = set(), []
    for r in numbers:
        key = r["e164"] or r["original"]
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(r)

    outp = _change_to(inp, "csv")
    pd.DataFrame(cleaned).to_csv(outp, index=False)
    return outp

def _extract_phone_like_tokens(s: str):
    # sequences of digits/+/( )/- of length≥6
    candidates = re.findall(r"[+()\- \d]{6,}", s or "")
    return [c.strip() for c in candidates if len(re.sub(r"\D", "", c)) >= 6]

def _try_parse_phone(s: str, default_region: str | None):
    try:
        num = phonenumbers.parse(s, default_region if not s.strip().startswith("+") else None)
        if phonenumbers.is_possible_number(num) and phonenumbers.is_valid_number(num):
            return num
    except Exception:
        return None
    return None

def _phone_type(num) -> str:
    from phonenumbers.phonenumberutil import number_type, PhoneNumberType
    t = number_type(num)
    return {
        PhoneNumberType.MOBILE: "mobile",
        PhoneNumberType.FIXED_LINE: "fixed_line",
        PhoneNumberType.FIXED_LINE_OR_MOBILE: "fixed_or_mobile",
        PhoneNumberType.VOIP: "voip",
        PhoneNumberType.TOLL_FREE: "toll_free",
        PhoneNumberType.PREMIUM_RATE: "premium",
    }.get(t, "other")

# ---- 2) Contacts: VCF <-> CSV ----
def data_vcf_to_csv(inp: Path) -> Path:
    text = _read_text(inp)
    cards = list(vobject.readComponents(text))
    rows = []
    for card in cards:
        name = getattr(card, "fn", None).value if hasattr(card, "fn") else ""
        phones, emails = [], []
        for c in getattr(card, "contents", {}).values():
            for item in c:
                try:
                    if item.name.upper() == "TEL":
                        phones.append(str(item.value))
                    if item.name.upper() == "EMAIL":
                        emails.append(str(item.value))
                except Exception:
                    pass
        rows.append({"name": name, "phones": "; ".join(phones), "emails": "; ".join(emails)})
    outp = _change_to(inp, "csv")
    pd.DataFrame(rows).to_csv(outp, index=False)
    return outp

def data_csv_to_vcf(inp: Path) -> Path:
    df = pd.read_csv(inp, dtype=str, keep_default_na=False, na_filter=False)
    parts = []
    for _, r in df.fillna("").iterrows():
        card = vobject.vCard()
        if r.get("name"):
            card.add("fn").value = r["name"]
        # split by ; or ,
        for col, kind in (("phones","TEL"), ("phone","TEL"), ("emails","EMAIL"), ("email","EMAIL")):
            val = (r.get(col) or "").strip()
            if not val:
                continue
            for piece in re.split(r"[;,]", val):
                piece = piece.strip()
                if not piece:
                    continue
                try:
                    card.add(kind.lower()).value = piece
                except Exception:
                    pass
        parts.append(card.serialize())
    outp = _change_to(inp, "vcf")
    _write_text(outp, "".join(parts))
    return outp

# ---- 3) Subtitles: SRT <-> VTT ----
def data_srt_to_vtt(inp: Path) -> Path:
    s = _read_text(inp)
    s = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", s)
    outp = _change_to(inp, "vtt")
    _write_text(outp, s)
    return outp

def data_vtt_to_srt(inp: Path) -> Path:
    s = _read_text(inp)
    s = re.sub(r"^WEBVTT[^\n]*\n+\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", s)
    lines, seq = [], 1
    for block in s.strip().split("\n\n"):
        if "-->" in block and not block.strip().splitlines()[0].isdigit():
            block = f"{seq}\n{block}"
            seq += 1
        lines.append(block)
    out = "\n\n".join(lines) + "\n"
    outp = _change_to(inp, "srt")
    _write_text(outp, out)
    return outp

# ---- 4) JSON/CSV/YAML bridges ----
def data_json_to_csv(inp: Path) -> Path:
    data = json.loads(_read_text(inp) or "[]")
    outp = _change_to(inp, "csv")
    if isinstance(data, dict):
        data = [data]
    pd.DataFrame(data).to_csv(outp, index=False)
    return outp

def data_csv_to_json(inp: Path) -> Path:
    df = pd.read_csv(inp, dtype=str, keep_default_na=False, na_filter=False)
    outp = _change_to(inp, "json")
    _write_text(outp, df.to_json(orient="records", force_ascii=False))
    return outp

def data_yaml_to_json(inp: Path) -> Path:
    obj = yaml.safe_load(_read_text(inp)) or {}
    outp = _change_to(inp, "json")
    _write_text(outp, json.dumps(obj, ensure_ascii=False, indent=2))
    return outp

def data_json_to_yaml(inp: Path) -> Path:
    obj = json.loads(_read_text(inp) or "{}")
    outp = _change_to(inp, "yaml")
    _write_text(outp, yaml.safe_dump(obj, sort_keys=False, allow_unicode=True))
    return outp
