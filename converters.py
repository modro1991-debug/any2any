# converters.py
# Progress-enabled converters for Any2Any

import os, csv, json, shutil, tempfile, subprocess, secrets, zipfile
from pathlib import Path
from uuid import uuid4

# Shared temp dir
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/any2any"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

def _rand_name(ext: str) -> Path:
    ext = ext.lstrip(".").lower()
    return TMP_DIR / f"{secrets.token_hex(16)}.{ext}"

# -------- progress helper ----------
def _report(progress, pct: float, msg: str = ""):
    try:
        if progress:
            progress(max(0.0, min(100.0, float(pct))), msg)
    except Exception:
        pass

# ====================== DOCUMENTS ===========================
_PDF_FILTERS = {
    "doc":  "pdf:writer_pdf_Export",
    "docx": "pdf:writer_pdf_Export",
    "odt":  "pdf:writer_pdf_Export",
    "rtf":  "pdf:writer_pdf_Export",
    "txt":  "pdf:writer_pdf_Export",
    "pdf":  "pdf:writer_pdf_Export",
    "xls":  "pdf:calc_pdf_Export",
    "xlsx": "pdf:calc_pdf_Export",
    "ods":  "pdf:calc_pdf_Export",
    "csv":  "pdf:calc_pdf_Export",
    "ppt":  "pdf:impress_pdf_Export",
    "pptx": "pdf:impress_pdf_Export",
    "odp":  "pdf:impress_pdf_Export",
}

def _lo_convert(input_path: Path, target: str, out_dir: Path) -> Path:
    input_path = input_path.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    src_ext = input_path.suffix.lower().lstrip(".")
    want_ext = "pdf" if target.lower() == "pdf" else target.lower()
    conv = _PDF_FILTERS.get(src_ext, "pdf:writer_pdf_Export") if want_ext == "pdf" else want_ext

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

    base = input_path.stem
    candidates = sorted(out_dir.glob(f"{base}*.{want_ext}"), key=lambda p: p.stat().st_mtime, reverse=True)
    if proc.returncode != 0 or not candidates:
        raise RuntimeError(
            "LibreOffice did not produce an output file.\n"
            f"CMD: {' '.join(cmd)}\nEXIT: {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout.decode(errors='ignore')}\n"
            f"STDERR:\n{proc.stderr.decode(errors='ignore')}"
        )

    try: shutil.rmtree(lo_profile, ignore_errors=True)
    except Exception: pass

    return candidates[0]

# PDF→DOCX via pdf2docx
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

def convert_doc(src_path: Path, target: str, progress=None) -> Path:
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")

    if src_ext == "pdf" and target == "docx":
        _report(progress, 10, "Parsing PDF")
        out = _pdf_to_docx(src_path)
        _report(progress, 100, "Done")
        return out

    job_out = TMP_DIR / f"job-{uuid4().hex[:10]}"
    _report(progress, 5, "Starting LibreOffice")
    produced = _lo_convert(src_path, target, job_out)
    _report(progress, 85, "Finalizing")
    final = _rand_name(produced.suffix)
    shutil.move(str(produced), str(final))
    shutil.rmtree(job_out, ignore_errors=True)
    _report(progress, 100, "Done")
    return final

# ======================== IMAGES ============================
from PIL import Image, ImageOps

def _pdf_page_count(src_path: Path) -> int:
    try:
        proc = subprocess.run(["pdfinfo", str(src_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if proc.returncode == 0:
            for line in proc.stdout.splitlines():
                if line.strip().startswith("Pages:"):
                    return int(line.split(":", 1)[1].strip())
    except Exception:
        pass
    return 0

def _pdf_to_images_zip(src_path: Path, target: str, dpi: int = 200, progress=None) -> Path:
    target = target.lower()
    if target not in {"png", "jpg", "jpeg", "webp"}:
        raise RuntimeError(f"Unsupported PDF->image target: {target}")

    total_pages = _pdf_page_count(src_path)
    if total_pages <= 0:
        total_pages = 1

    work = TMP_DIR / f"pdfimg-{uuid4().hex[:8]}"
    work.mkdir(parents=True, exist_ok=True)
    _report(progress, 0, f"Found {total_pages} page(s)")

    produced_pngs = []
    for i in range(1, total_pages + 1):
        prefix = work / f"page-{i}"
        cmd = ["pdftoppm", "-png", f"-r{dpi}", "-f", str(i), "-l", str(i), str(src_path), str(prefix)]
        proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if proc.returncode != 0:
            shutil.rmtree(work, ignore_errors=True)
            raise RuntimeError(f"pdftoppm failed on page {i} (exit {proc.returncode}).\nSTDERR:\n{proc.stderr.decode(errors='ignore')}")
        cand = (work / f"page-{i}-1.png")
        if not cand.exists():
            cand = (work / f"page-{i}.png")
        if not cand.exists():
            shutil.rmtree(work, ignore_errors=True)
            raise RuntimeError(f"No image produced for page {i}.")
        produced_pngs.append(cand)
        _report(progress, (i/total_pages)*70.0, f"Rendered page {i}/{total_pages}")

    images_for_zip = []
    for idx, p in enumerate(produced_pngs, 1):
        if target in {"png"}:
            images_for_zip.append((p, f"page-{idx}.png"))
        else:
            out_name = p.with_suffix("." + ("jpg" if target == "jpeg" else target))
            with Image.open(p) as im:
                if target in {"jpg","jpeg"} and im.mode in ("RGBA","P"):
                    im = im.convert("RGB")
                save_kwargs = {}
                if target in {"jpg","jpeg"}: save_kwargs.update(dict(quality=92, optimize=True, progressive=True))
                if target == "webp":         save_kwargs.update(dict(quality=90, method=4))
                im.save(out_name, {"jpg":"JPEG","jpeg":"JPEG","webp":"WEBP"}.get(target, "PNG"), **save_kwargs)
            images_for_zip.append((out_name, f"page-{idx}.{out_name.suffix.lstrip('.')}"))
        _report(progress, 70.0 + (idx/total_pages)*20.0, f"Encoded page {idx}/{total_pages}")

    out_zip = TMP_DIR / f"{secrets.token_hex(16)}_{target}.zip"
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, arc in images_for_zip:
            zf.write(path, arcname=arc)
    shutil.rmtree(work, ignore_errors=True)
    _report(progress, 100.0, "Done")
    return out_zip

def convert_image(src_path: Path, target: str, progress=None) -> Path:
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")

    if src_ext == "pdf":
        return _pdf_to_images_zip(src_path, target, progress=progress)

    if target not in {"jpg","jpeg","png","webp","ico"}:
        raise RuntimeError(f"Unsupported image target: {target}")

    _report(progress, 5, "Opening image")
    with Image.open(src_path) as im:
        if target in {"jpg","jpeg"} and im.mode in ("RGBA","P"):
            im = im.convert("RGB")
        elif im.mode == "P":
            im = im.convert("RGBA")

        out_ext = "jpg" if target == "jpeg" else target
        out = _rand_name(out_ext)

        save_kwargs = {}
        if out_ext == "jpg":  save_kwargs.update(dict(quality=92, optimize=True, progressive=True))
        if out_ext == "webp": save_kwargs.update(dict(quality=90, method=4))
        _report(progress, 50, "Encoding")
        if out_ext == "ico":
            icon = ImageOps.contain(im, (256, 256))
            icon.save(out, format="ICO")
        else:
            fmt = {"jpg":"JPEG","png":"PNG","webp":"WEBP"}[out_ext]
            im.save(out, format=fmt, **save_kwargs)

    _report(progress, 100, "Done")
    return out

# ===================== AUDIO / VIDEO ========================
def convert_av(src_path: Path, target: str, progress=None) -> Path:
    import shutil as _sh
    if _sh.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed in this image.")
    target = target.lower().lstrip(".")
    out = _rand_name(target)
    _report(progress, 5, "Starting ffmpeg")
    proc = subprocess.run(["ffmpeg", "-y", "-i", str(src_path), str(out)],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"ffmpeg failed (exit {proc.returncode}).\nSTDERR:\n{proc.stderr.decode(errors='ignore')}")
    _report(progress, 100, "Done")
    return out

# ======================= DATA I/O ===========================
DATA_IN  = {"csv","vcf","srt","vtt","json","yaml","yml"}
DATA_OUT = {"phonecsv","csv","vcf","srt","vtt","csv_from_json","json_from_csv","json_from_yaml","yaml_from_json"}

def data_srt_to_vtt(src_path: Path) -> Path:
    dst = _rand_name("vtt")
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        f_out.write("WEBVTT\n\n")
        for line in f_in:
            s = line.rstrip("\n")
            if s.strip().isdigit():  # drop indices
                continue
            if "-->" in s:
                s = s.replace(",", ".")
            f_out.write(s + "\n")
    return dst

def data_vtt_to_srt(src_path: Path) -> Path:
    dst = _rand_name("srt")
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f_in:
        lines = [l.rstrip("\n") for l in f_in]
    if lines and lines[0].strip().upper().startswith("WEBVTT"):
        lines = lines[1:]
        if lines and lines[0] == "":
            lines = lines[1:]
    with open(dst, "w", encoding="utf-8") as f_out:
        idx, i = 1, 0
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

# Contacts helpers
import phonenumbers, vobject

def data_vcf_to_csv(src_path: Path) -> Path:
    rows = []
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
        data = f.read()
    for card in vobject.readComponents(data):
        name = ""
        try:
            if hasattr(card, "fn") and card.fn.value:
                name = str(card.fn.value)
            elif hasattr(card, "n"):
                name = " ".join([x for x in card.n.value if x])
        except Exception:
            pass
        phs = [str(t.value) for t in getattr(card, "tel_list", []) if getattr(t, "value", None)]
        ems = [str(e.value) for e in getattr(card, "email_list", []) if getattr(e, "value", None)]
        rows.append({"name": name.strip(), "phone": ";".join(phs), "email": ";".join(ems)})

    dst = _rand_name("csv")
    with open(dst, "w", newline="", encoding="utf-8") as f_out:
        w = csv.DictWriter(f_out, fieldnames=["name","phone","email"])
        w.writeheader()
        w.writerows(rows)
    return dst

def data_csv_to_vcf(src_path: Path) -> Path:
    dst = _rand_name("vcf")
    with open(src_path, "r", encoding="utf-8") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        reader = csv.DictReader(f_in)
        for row in reader:
            name  = (row.get("name") or "").strip()
            phones = [p.strip() for p in (row.get("phone") or "").split(";") if p.strip()]
            emails = [e.strip() for e in (row.get("email") or "").split(";") if e.strip()]
            v = vobject.vCard()
            if name: v.add("fn").value = name
            for p in phones:
                tel = v.add("tel"); tel.value = p; tel.type_param = "CELL"
            for e in emails:
                em = v.add("email"); em.value = e; em.type_param = "INTERNET"
            f_out.write(v.serialize())
    return dst

def data_phone_clean(src_path: Path, default_region=None) -> Path:
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
            for piece in [p.strip() for p in raw.split(";") if p.strip()]:
                try:
                    num = phonenumbers.parse(piece, default_region)
                    if phonenumbers.is_valid_number(num):
                        cleaned.append(phonenumbers.format_number(num, phonenumbers.PhoneNumberFormat.E164))
                except Exception:
                    pass
            row["phone"] = ";".join(cleaned)
            rows_out.append(row)
    with open(dst, "w", newline="", encoding="utf-8") as f_out:
        writer = csv.DictWriter(f_out, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows_out)
    return dst
