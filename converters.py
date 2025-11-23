# converters.py
import os, shutil, tempfile, subprocess, secrets
from pathlib import Path
from uuid import uuid4

# --- Ensure a writable temp directory used by app.py /download ---
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/any2any"))
TMP_DIR.mkdir(parents=True, exist_ok=True)

# If you already export DATA_IN / DATA_OUT and other helpers here, keep them.

# Map source ext → proper PDF export filter (Writer/Calc/Impress)
_PDF_FILTERS = {
    # Writer family
    "doc":  "pdf:writer_pdf_Export",
    "docx": "pdf:writer_pdf_Export",
    "odt":  "pdf:writer_pdf_Export",
    "rtf":  "pdf:writer_pdf_Export",
    "txt":  "pdf:writer_pdf_Export",
    "pdf":  "pdf:writer_pdf_Export",
    # Calc family
    "xls":  "pdf:calc_pdf_Export",
    "xlsx": "pdf:calc_pdf_Export",
    "ods":  "pdf:calc_pdf_Export",
    "csv":  "pdf:calc_pdf_Export",  # best effort
    # Impress family
    "ppt":  "pdf:impress_pdf_Export",
    "pptx": "pdf:impress_pdf_Export",
    "odp":  "pdf:impress_pdf_Export",
}

def _lo_convert(input_path: Path, target: str, out_dir: Path) -> Path:
    """
    Call headless LibreOffice to convert input_path to `target` extension.
    Writes output into `out_dir` and returns the produced file path.
    Raises RuntimeError with STDERR on failure.
    """
    input_path = input_path.resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    src_ext = input_path.suffix.lower().lstrip(".")
    want_ext = "pdf" if target.lower() == "pdf" else target.lower()

    # Writable temporary LO profile (Render often has read-only HOME)
    lo_profile = Path(tempfile.gettempdir()) / f"lo-profile-{uuid4().hex}"
    lo_profile.mkdir(parents=True, exist_ok=True)

    # Choose explicit filter for PDF; for other targets LO picks automatically
    conv = _PDF_FILTERS.get(src_ext, "pdf:writer_pdf_Export") if want_ext == "pdf" else want_ext

    cmd = [
        "soffice",
        "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard",
        f"-env:UserInstallation=file://{lo_profile}",
        "--convert-to", conv,
        "--outdir", str(out_dir),
        str(input_path),
    ]

    env = os.environ.copy()
    # make sure these are writable
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

    # Clean up profile (best effort)
    try:
        shutil.rmtree(lo_profile, ignore_errors=True)
    except Exception:
        pass

    return candidates[0]

def convert_doc(src_path: Path, target: str) -> Path:
    """
    Public API used by app.py. Returns a file **in TMP_DIR** with a random name.
    """
    job_out = TMP_DIR / f"job-{uuid4().hex[:10]}"
    produced = _lo_convert(src_path, target, job_out)

    # Move/rename into TMP_DIR with a random filename to serve via /download/{name}
    final_name = f"{secrets.token_hex(16)}.{produced.suffix.lstrip('.')}"
    final_path = TMP_DIR / final_name
    shutil.move(str(produced), str(final_path))

    # remove the per-job folder
    shutil.rmtree(job_out, ignore_errors=True)

    return final_path
