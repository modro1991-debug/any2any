# --- ADD near the top with the other imports ---
import io
import csv
import json
import yaml
import phonenumbers
import pandas as pd
import vobject

# ------------- DATA CONVERTERS (unique features) -----------------

DATA_IN = {"csv","xlsx","txt","vcf","srt","vtt","json","yaml","yml"}
DATA_OUT = {
    # phone list cleaner (outputs CSV)
    "phonecsv",
    # contacts
    "vcf","csv",
    # subtitles
    "srt","vtt",
    # structured data
    "json","csv_from_json","json_from_csv","yaml","json_from_yaml","yaml_from_json"
}

def _read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8", errors="replace")

def _write_text(p: Path, s: str):
    p.write_text(s, encoding="utf-8")

def _change_to(p: Path, new_ext: str) -> Path:
    return p.with_suffix("." + new_ext)

# ---- 2.1 Phone number cleaner: CSV/XLSX/TXT -> cleaned CSV
# Heuristics: search for phone-like tokens in any column; normalize to E.164; dedupe
def data_phone_clean(inp: Path, default_region: str | None = None) -> Path:
    # Load to dataframe (best-effort)
    if inp.suffix.lower() == ".xlsx":
        df = pd.read_excel(inp, dtype=str)
    elif inp.suffix.lower() == ".csv":
        df = pd.read_csv(inp, dtype=str, keep_default_na=False, na_filter=False)
    elif inp.suffix.lower() == ".txt":
        df = pd.DataFrame({"value": _read_text(inp).splitlines()})
    else:
        raise RuntimeError("Phone cleaner expects CSV/XLSX/TXT")

    numbers = []
    cols = list(df.columns)
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

    # dedupe by e164 (keep first non-empty)
    seen = set()
    cleaned = []
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
    # very light heuristic: sequences of digits/+/( )/-
    import re
    candidates = re.findall(r"[+()\- \d]{6,}", s)
    # strip spaces and obvious junk
    return [c.strip() for c in candidates if len(re.sub(r"\D", "", c)) >= 6]

def _try_parse_phone(s: str, default_region: str | None):
    try:
        # If starts with +, region is ignored; else use default_region if given
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

# ---- 2.2 Contacts: VCF <-> CSV
def data_vcf_to_csv(inp: Path) -> Path:
    text = _read_text(inp)
    cards = list(vobject.readComponents(text))
    rows = []
    for card in cards:
        name = getattr(card, "fn", None).value if hasattr(card, "fn") else ""
        # Gather phones/emails
        phones = []
        emails = []
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
    vcf_parts = []
    for _, r in df.fillna("").iterrows():
        card = vobject.vCard()
        if r.get("name"):
            card.add("fn").value = r["name"]
        # split multi-values by ; or , 
        for col, kind in (("phones","TEL"), ("phone","TEL"), ("emails","EMAIL"), ("email","EMAIL")):
            val = (r.get(col) or "").strip()
            if not val: 
                continue
            for piece in [p.strip() for p in re_split_multi(val)]:
                try:
                    card.add(kind.lower()).value = piece
                except Exception:
                    pass
        vcf_parts.append(card.serialize())
    outp = _change_to(inp, "vcf")
    _write_text(outp, "".join(vcf_parts))
    return outp

def re_split_multi(s: str):
    import re
    return re.split(r"[;,]", s)

# ---- 2.3 Subtitles: SRT <-> VTT
def data_srt_to_vtt(inp: Path) -> Path:
    s = _read_text(inp)
    # replace comma in timestamps with dot, add WEBVTT header
    import re
    s = "WEBVTT\n\n" + re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", s)
    outp = _change_to(inp, "vtt")
    _write_text(outp, s)
    return outp

def data_vtt_to_srt(inp: Path) -> Path:
    s = _read_text(inp)
    import re
    s = re.sub(r"^WEBVTT[^\n]*\n+\s*", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(\d{2}:\d{2}:\d{2})\.(\d{3})", r"\1,\2", s)
    # Add sequence numbers if missing
    lines = []
    seq = 1
    for block in s.strip().split("\n\n"):
        if "-->" in block and not block.strip().splitlines()[0].isdigit():
            block = f"{seq}\n{block}"
            seq += 1
        lines.append(block)
    out = "\n\n".join(lines) + "\n"
    outp = _change_to(inp, "srt")
    _write_text(outp, out)
    return outp

# ---- 2.4 JSON/CSV/YAML conversions
def data_json_to_csv(inp: Path) -> Path:
    data = json.loads(_read_text(inp) or "[]")
    outp = _change_to(inp, "csv")
    if isinstance(data, dict):  # wrap single dict
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
