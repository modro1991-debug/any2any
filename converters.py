# converters.py
# ------------------------------------------------------------
# Utilities for Any2Any backend used by app.py
# - TMP_DIR (shared temp)
# - Document conversion via LibreOffice (robust wrapper)
# - PDF→DOCX via pdf2docx (LibreOffice cannot do this)
# - Image conversion via Pillow
# - Audio/Video via ffmpeg (optional; raises clear error if missing)
# - Data conversions: SRT⇄VTT, JSON⇄CSV, JSON⇄YAML
# - Contacts helpers: VCF⇄CSV, phone number cleaning (phonenumbers)
#
# REQUIREMENTS (requirements.txt):
#   fastapi, uvicorn, jinja2, python-multipart
#   Pillow==10.4.0
#   PyYAML==6.x
#   pdf2docx==0.5.8
#   pandas==2.x, openpyxl==3.x  (optional: not used here)
#   phonenumbers==8.x
#   vobject==0.9.x
# ------------------------------------------------------------

import os
import csv
import json
import shutil
import tempfile
import subprocess
import secrets
from pathlib import Path
from uuid import uuid4

# ---------- Shared temp dir ----------
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/any2any"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

def _rand_name(ext: str) -> Path:
    ext = ext.lstrip(".").lower()
    return TMP_DIR / f"{secrets.token_hex(16)}.{ext}"

# ============================================================
#                          DOCUMENTS
# ============================================================

# Explicit PDF export filters by app family (Writer/Calc/Impress)
_PDF_FILTERS = {
    # Writer
    "doc":  "pdf:writer_pdf_Export",
    "docx": "pdf:writer_pdf_Export",
    "odt":  "pdf:writer_pdf_Export",
    "rtf":  "pdf:writer_pdf_Export",
    "txt":  "pdf:writer_pdf_Export",
    "pdf":  "pdf:writer_pdf_Export",
    # Calc
    "xls":  "pdf:calc_pdf_Export",
    "xlsx": "pdf:calc_pdf_Export",
    "ods":  "pdf:calc_pdf_Export",
    "csv":  "pdf:calc_pdf_Export",  # best-effort
    # Impress
    "ppt":  "pdf:impress_pdf_Export",
    "pptx": "pdf:impress_pdf_Export",
    "odp":  "pdf:impress_pdf_Export",
}

def _lo_convert(input_path: Path, target: str, out_dir: Path) -> Path:
    """
    Convert using headless LibreOffice.
    Ensures a writable profile, uses absolute paths, and verifies output exists.
    Returns the produced file path (inside out_dir).
    """
    input_path = input_path.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    src_ext = input_path.suffix.lower().lstrip(".")
    want_ext = "pdf" if target.lower() == "pdf" else target.lower()

    # Choose explicit filter for PDF; otherwise let LO pick automatically
    conv = _PDF_FILTERS.get(src_ext, "pdf:writer_pdf_Export") if want_ext == "pdf" else want_ext

    # Writable temporary LO profile (containers/hosts may have read-only HOME)
    lo_profile = Path(tempfile.gettempdir()) / f"lo-profile-{uuid4().hex}"
    lo_profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        "soffice",
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
        f"-env:UserInstallation=file://{lo_profile}",
        "--convert-to", conv,
        "--outdir", str(out_dir),
        str(input_path),
    ]

    env = os.environ.copy()
    env["HOME"] = env.get("HOME", tempfile.gettempdir())
    env["TMPDIR"] = env.get("TMPDIR", tempfile.gettempdir())

    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env, timeout=180)

    # LibreOffice sometimes returns 0 but writes nothing; verify output exists.
    base = input_path.stem
    candidates = sorted(out_dir.glob(f"{base}*.{want_ext}"), key=lambda p: p.stat().st_mtime, reverse=True)

    if proc.returncode != 0 or not candidates:
        raise RuntimeError(
            "LibreOffice did not produce an output file.\n"
            f"CMD: {' '.join(cmd)}\n"
            f"EXIT: {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout.decode(errors='ignore')}\n"
            f"STDERR:\n{proc.stderr.decode(errors='ignore')}"
        )

    # Clean up LO profile (best-effort)
    try:
        shutil.rmtree(lo_profile, ignore_errors=True)
    except Exception:
        pass

    return candidates[0]

# PDF → DOCX needs a different tool (LibreOffice cannot do it)
from pdf2docx import Converter as _Pdf2DocxConverter

def _pdf_to_docx(src_path: Path) -> Path:
    out = _rand_name("docx")
    cv = _Pdf2DocxConverter(str(src_path))
    try:
        cv.convert(str(out))
    finally:
        cv.close()
    if not out.exists() or out.stat().st_size == 0:
        raise RuntimeError("pdf2docx failed to produce output.")
    return out

def convert_doc(src_path: Path, target: str) -> Path:
    """
    Public API for document conversions used by app.py.
    Returns a file in TMP_DIR.
    """
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")

    # Handle PDF -> DOCX via pdf2docx
    if src_ext == "pdf" and target == "docx":
        return _pdf_to_docx(src_path)

    # All other office conversions (incl. -> PDF) via LibreOffice
    job_out = TMP_DIR / f"job-{uuid4().hex[:10]}"
    produced = _lo_convert(src_path, target, job_out)

    final = _rand_name(produced.suffix)
    shutil.move(str(produced), str(final))
    shutil.rmtree(job_out, ignore_errors=True)
    return final

# ============================================================
#                            IMAGES
# ============================================================

from PIL import Image, ImageOps  # pillow

def convert_image(src_path: Path, target: str) -> Path:
    """
    Convert images within raster formats (jpg/png/webp/ico).
    (PDF→images is handled client-side in your app; server path excludes PDF.)
    """
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")
    if src_ext == "pdf":
        raise RuntimeError("Server-side PDF→image not supported. Use local (browser) mode or convert to DOCX/PDF.")
    if target not in {"jpg", "jpeg", "png", "webp", "ico"}:
        raise RuntimeError(f"Unsupported image target: {target}")

    with Image.open(src_path) as im:
        if target in {"jpg", "jpeg"}:
            if im.mode in ("RGBA", "P"):
                im = im.convert("RGB")
        elif im.mode == "P":
            im = im.convert("RGBA")

        out_ext = "jpg" if target == "jpeg" else target
        out = _rand_name(out_ext)

        save_kwargs = {}
        if out_ext == "jpg":
            save_kwargs.update(dict(quality=92, optimize=True, progressive=True))
        if out_ext == "webp":
            save_kwargs.update(dict(quality=90, method=4))

        if out_ext == "ico":
            icon = ImageOps.contain(im, (256, 256))
            icon.save(out, format="ICO")
        else:
            fmt = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}[out_ext]
            im.save(out, format=fmt, **save_kwargs)

    return out

# ============================================================
#                      AUDIO / VIDEO (ffmpeg)
# ============================================================

def convert_av(src_path: Path, target: str) -> Path:
    """
    Convert using ffmpeg if available. Simple container/codec copy—let ffmpeg decide.
    """
    import shutil as _sh
    if _sh.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed in this image. Install it or disable AV conversions.")
    target = target.lower().lstrip(".")
    out = _rand_name(target)
    cmd = ["ffmpeg", "-y", "-i", str(src_path), str(out)]
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}).\nSTDERR:\n{proc.stderr.decode(errors='ignore')}")
    return out

# ============================================================
#                           DATA I/O
# ============================================================

# Expose these so app.py can decide category and validate pairs
DATA_IN  = {"csv", "vcf", "srt", "vtt", "json", "yaml", "yml"}
DATA_OUT = {"phonecsv", "csv", "vcf", "srt", "vtt",
            "csv_from_json", "json_from_csv", "json_from_yaml", "yaml_from_json"}

# -------- SRT <-> VTT --------

def data_srt_to_vtt(src_path: Path) -> Path:
    dst = _rand_name("vtt")
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f_in, \
         open(dst, "w", encoding="utf-8") as f_out:
        f_out.write("WEBVTT\n\n")
        for line in f_in:
            s = line.rstrip("\n")
            # drop numeric indices; VTT doesn’t require them
            if s.strip().isdigit():
                continue
            if "-->" in s:
                s = s.replace(",", ".")  # 00:00:01,000 -> 00:00:01.000
            f_out.write(s + "\n")
    return dst

def data_vtt_to_srt(src_path: Path) -> Path:
    dst = _rand_name("srt")
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f_in:
        lines = [l.rstrip("\n") for l in f_in]
    # Drop WEBVTT header if present
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        lines = lines[1:]
        if lines and lines[0] == "":
            lines = lines[1:]

    with open(dst, "w", encoding="utf-8") as f_out:
        idx = 1
        i = 0
        while i < len(lines):
            line = lines[i]
            if "-->" in line:
                f_out.write(f"{idx}\n")
                f_out.write(line.replace(".", ",") + "\n")
                i += 1
                while i < len(lines) and lines[i].strip() != "":
                    f_out.write(lines[i] + "\n")
                    i += 1
                f_out.write("\n")
                idx += 1
            i += 1
    return dst

# -------- JSON <-> CSV --------

def data_json_to_csv(src_path: Path) -> Path:
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, list) or not data:
        raise RuntimeError("JSON must be a non-empty list of objects to convert to CSV.")
    headers = sorted({k for row in data if isinstance(row, dict) for k in row.keys()})
    dst = _rand_name("csv")
    with open(dst, "w", newline="", encoding="utf-8") as f_out:
        w = csv.DictWriter(f_out, fieldnames=headers)
        w.writeheader()
        for row in data:
            if isinstance(row, dict):
                w.writerow({k: row.get(k, "") for k in headers})
    return dst

def data_csv_to_json(src_path: Path) -> Path:
    dst = _rand_name("json")
    with open(src_path, "r", encoding="utf-8") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        reader = csv.DictReader(f_in)
        json.dump(list(reader), f_out, ensure_ascii=False, indent=2)
    return dst

# -------- JSON <-> YAML --------

import yaml

def data_yaml_to_json(src_path: Path) -> Path:
    with open(src_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    dst = _rand_name("json")
    with open(dst, "w", encoding="utf-8") as f_out:
        json.dump(data, f_out, ensure_ascii=False, indent=2)
    return dst

def data_json_to_yaml(src_path: Path) -> Path:
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dst = _rand_name("yaml")
    with open(dst, "w", encoding="utf-8") as f_out:
        yaml.safe_dump(data, f_out, sort_keys=False, allow_unicode=True)
    return dst

# -------- Contacts: VCF <-> CSV + phone cleaning --------

import phonenumbers
import vobject

def data_vcf_to_csv(src_path: Path) -> Path:
    """
    Parse a .vcf (vCard) file and export a simple CSV with columns:
    name, phone, email
    Multiple values become semicolon-separated.
    """
    names, phones, emails = [], [], []
    rows = []

    with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()

    # vobject can iterate multiple vCards in one file
    for card in vobject.readComponents(data):
        name = ""
        phs, ems = [], []
        try:
            # FN is full name if present
            if hasattr(card, "fn") and card.fn.value:
                name = str(card.fn.value)
            elif hasattr(card, "n"):
                name = " ".join([x for x in card.n.value if x])
        except Exception:
            pass

        # TELEPHONE
        for t in getattr(card, "tel_list", []):
            try:
                phs.append(str(t.value))
            except Exception:
                pass

        # EMAIL
        for e in getattr(card, "email_list", []):
            try:
                ems.append(str(e.value))
            except Exception:
                pass

        rows.append({
            "name": name.strip(),
            "phone": ";".join(p.strip() for p in phs if p),
            "email": ";".join(e.strip() for e in ems if e),
        })

    dst = _rand_name("csv")
    with open(dst, "w", newline="", encoding="utf-8") as f_out:
        w = csv.DictWriter(f_out, fieldnames=["name", "phone", "email"])
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return dst

def data_csv_to_vcf(src_path: Path) -> Path:
    """
    Read CSV with columns (name, phone, email) and output a .vcf file.
    Multiple values may be semicolon-separated.
    """
    dst = _rand_name("vcf")
    with open(src_path, "r", encoding="utf-8") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        reader = csv.DictReader(f_in)
        for row in reader:
            name  = (row.get("name") or "").strip()
            phones = [p.strip() for p in (row.get("phone") or "").split(";") if p.strip()]
            emails = [e.strip() for e in (row.get("email") or "").split(";") if e.strip()]

            v = vobject.vCard()
            if name:
                v.add("fn").value = name

            for p in phones:
                tel = v.add("tel")
                tel.value = p
                tel.type_param = "CELL"

            for e in emails:
                em = v.add("email")
                em.value = e
                em.type_param = "INTERNET"

            f_out.write(v.serialize())
    return dst

def data_phone_clean(src_path: Path, default_region=None) -> Path:
    """
    Normalize phone numbers in a CSV file to E.164 format.
    Input CSV must have a 'phone' column. Other columns are preserved.
    - default_region: e.g., 'US', 'GB', 'MA', etc. If None, try to infer.
    """
    dst = _rand_name("csv")

    with open(src_path, "r", encoding="utf-8") as f_in:
        reader = csv.DictReader(f_in)
        fieldnames = list(reader.fieldnames or [])
        if "phone" not in fieldnames:
            raise RuntimeError("CSV must contain a 'phone' column for phonecsv cleaning.")

        rows_out = []
        for row in reader:
            raw = (row.get("phone") or "").strip()
            cleaned = []
            # Allow multiple semicolon-separated numbers
            for piece in [p.strip() for p in raw.split(";") if p.strip()]:
                try:
                    num = phonenumbers.parse(piece, default_region)
                    if phonenumbers.is_valid_number(num):
                        cleaned.append(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164))
                except Exception:
                    # leave invalid numbers out (or keep raw if you prefer)
                    pass
            row["phone"] = ";".join(cleaned)
            rows_out.append(row)

    with open(dst, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)

    return dst
