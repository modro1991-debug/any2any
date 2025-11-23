# converters.py
import os, shutil, tempfile, subprocess, secrets, csv, json
from pathlib import Path
from uuid import uuid4

# ---------- Shared temp dir ----------
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/any2any"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

def _rand_name(ext: str) -> Path:
    ext = ext.lstrip(".").lower()
    return TMP_DIR / f"{secrets.token_hex(16)}.{ext}"

# ---------- Document conversion (LibreOffice) ----------
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
    input_path = input_path.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    src_ext = input_path.suffix.lower().lstrip(".")
    want_ext = "pdf" if target.lower() == "pdf" else target.lower()
    conv = _PDF_FILTERS.get(src_ext, "pdf:writer_pdf_Export") if want_ext == "pdf" else want_ext

    profile = Path(tempfile.gettempdir()) / f"lo-profile-{uuid4().hex}"
    profile.mkdir(parents=True, exist_ok=True)

    cmd = [
        "soffice",
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
        f"-env:UserInstallation=file://{profile}",
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
            f"CMD: {' '.join(cmd)}\n"
            f"EXIT: {proc.returncode}\n"
            f"STDOUT:\n{proc.stdout.decode(errors='ignore')}\n"
            f"STDERR:\n{proc.stderr.decode(errors='ignore')}"
        )

    try:
        shutil.rmtree(profile, ignore_errors=True)
    except Exception:
        pass

    return candidates[0]

def convert_doc(src_path: Path, target: str) -> Path:
    job_dir = TMP_DIR / f"job-{uuid4().hex[:10]}"
    produced = _lo_convert(src_path, target, job_dir)
    final = _rand_name(produced.suffix)
    shutil.move(str(produced), str(final))
    shutil.rmtree(job_dir, ignore_errors=True)
    return final

# ---------- Image conversion (Pillow) ----------
# Supports: jpg/jpeg/png/webp/gif/tiff/bmp/ico  → jpg/png/webp/ico (same family)
# NOTE: PDF→images is handled client-side in your app; server path here excludes PDF.
from PIL import Image, ImageOps  # pillow

def convert_image(src_path: Path, target: str) -> Path:
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")
    if src_ext == "pdf":
        raise RuntimeError("Server-side PDF→image not supported. Use local mode or convert to DOCX/PDF.")
    if target not in {"jpg","jpeg","png","webp","ico"}:
        raise RuntimeError(f"Unsupported image target: {target}")

    # Open with Pillow
    with Image.open(src_path) as im:
        # Flatten to RGB if needed (e.g., for JPEG)
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
            # ICO likes sizes like 256x256
            icon = ImageOps.contain(im, (256, 256))
            icon.save(out, format="ICO")
        else:
            fmt = {"jpg":"JPEG","png":"PNG","webp":"WEBP"}[out_ext]
            im.save(out, format=fmt, **save_kwargs)

    return out

# ---------- Audio/Video conversion (ffmpeg) ----------
def convert_av(src_path: Path, target: str) -> Path:
    """Convert with ffmpeg if installed; otherwise raise a clear error."""
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

# ---------- DATA conversions ----------
# Expose these sets so app.py can check support
DATA_IN  = {"csv","vcf","srt","vtt","json","yaml","yml"}
DATA_OUT = {"phonecsv","csv","vcf","srt","vtt","csv_from_json","json_from_csv","json_from_yaml","yaml_from_json"}

# -- SRT <-> VTT --
def data_srt_to_vtt(src_path: Path) -> Path:
    dst = _rand_name("vtt")
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        f_out.write("WEBVTT\n\n")
        idx = 0
        for line in f_in:
            line = line.rstrip("\n")
            # drop numeric indices; VTT doesn’t need them
            if line.strip().isdigit():
                continue
            # 00:00:01,000 --> 00:00:02,000  => replace comma with dot
            if "-->" in line:
                line = line.replace(",", ".")
            f_out.write(line + "\n")
    return dst

def data_vtt_to_srt(src_path: Path) -> Path:
    dst = _rand_name("srt")
    with open(src_path, "r", encoding="utf-8", errors="ignore") as f_in, open(dst, "w", encoding="utf-8") as f_out:
        # skip WEBVTT header if present
        lines = [l.rstrip("\n") for l in f_in]
        if lines and lines[0].strip().upper().startswith("WEBVTT"):
            lines = lines[1:]
            if lines and lines[0] == "":
                lines = lines[1:]

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

# -- JSON <-> CSV --
def data_json_to_csv(src_path: Path) -> Path:
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Expect a list of dicts
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

# -- JSON <-> YAML --
def data_yaml_to_json(src_path: Path) -> Path:
    import yaml
    with open(src_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    dst = _rand_name("json")
    with open(dst, "w", encoding="utf-8") as f_out:
        json.dump(data, f_out, ensure_ascii=False, indent=2)
    return dst

def data_json_to_yaml(src_path: Path) -> Path:
    import yaml
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    dst = _rand_name("yaml")
    with open(dst, "w", encoding="utf-8") as f_out:
        yaml.safe_dump(data, f_out, sort_keys=False, allow_unicode=True)
    return dst

# -- VCF/Phone CSV (stubs with clear messaging) --
def data_vcf_to_csv(src_path: Path) -> Path:
    """Stub. Implement real VCF parsing if needed."""
    raise RuntimeError("VCF→CSV not implemented in server yet.")

def data_csv_to_vcf(src_path: Path) -> Path:
    """Stub. Implement real CSV→VCF if needed."""
    raise RuntimeError("CSV→VCF not implemented in server yet.")

def data_phone_clean(src_path: Path, default_region=None) -> Path:
    """
    Stub: copy to CSV. For real cleaning, install `phonenumbers` and normalize each value.
    """
    dst = _rand_name("csv")
    shutil.copy2(src_path, dst)
    return dst
