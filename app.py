import os, time, secrets, shutil
from pathlib import Path
from typing import Optional
from threading import Thread

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from converters import (
    TMP_DIR,
    convert_image, convert_av, convert_doc,
    DATA_IN, DATA_OUT,
    data_phone_clean, data_vcf_to_csv, data_csv_to_vcf,
    data_srt_to_vtt, data_vtt_to_srt,
    data_json_to_csv, data_csv_to_json, data_yaml_to_json, data_json_to_yaml
)

app = FastAPI(title="Any2Any Converter")

# CORS (open for now; restrict later)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Limits & simple rate limit
MAX_SIZE_BYTES = int(os.getenv("MAX_SIZE_BYTES", 50 * 1024 * 1024))  # 50 MB
MAX_REQUESTS = int(os.getenv("MAX_REQUESTS_PER_10M", 30))
WINDOW = 600
BUCKET = {}

IMAGE_IN = {"jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"}
IMAGE_OUT = IMAGE_IN
AV_IN = {"mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"}
AV_OUT = AV_IN
DOC_IN = {"pdf","doc","docx","ppt","pptx","xls","xlsx","odt","odp","ods","rtf","txt"}
DOC_OUT = {"pdf","docx","xlsx","pptx","odt","ods","odp"}

def _ip(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host)

def _rate_limit(ip: str):
    now = time.time()
    start = now - WINDOW
    BUCKET.setdefault(ip, [])
    BUCKET[ip] = [t for t in BUCKET[ip] if t >= start]
    if len(BUCKET[ip]) >= MAX_REQUESTS:
        raise HTTPException(429, "Too many requests, please try again later.")
    BUCKET[ip].append(now)

def _secure_name(name: str) -> str:
    return secrets.token_hex(16) + (Path(name).suffix or "")

def _sweep_tmp(ttl_seconds: int = 20 * 60):
    now = time.time()
    for f in TMP_DIR.glob("*"):
        try:
            if now - f.stat().st_mtime > ttl_seconds:
                if f.is_dir():
                    shutil.rmtree(f, ignore_errors=True)
                else:
                    f.unlink()
        except Exception:
            pass

async def _save_upload(f: UploadFile, limit: int) -> Path:
    dest = TMP_DIR / _secure_name(f.filename or "file.bin")
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await f.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (>{limit//(1024*1024)}MB).")
            out.write(chunk)
    dest.chmod(0o600); dest.touch()
    return dest

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    _sweep_tmp()
    return templates.TemplateResponse("index.html", {"request": request})

@app.head("/")
async def index_head():
    return HTMLResponse(status_code=200)

@app.get("/healthz")
async def healthz_get():
    return PlainTextResponse("ok", status_code=200)

@app.head("/healthz")
async def healthz_head():
    return PlainTextResponse("", status_code=200)

@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    return templates.TemplateResponse("privacy.html", {"request": request})

@app.get("/cookies", response_class=HTMLResponse)
async def cookies(request: Request):
    return templates.TemplateResponse("cookies.html", {"request": request})

# ---------- Background jobs + status ----------
JOBS = {}  # job_id -> {status, percent, msg, download, filename, error}

def _set_progress(job_id, pct: float, msg: str = ""):
    JOBS.setdefault(job_id, {})
    JOBS[job_id].update({
        "percent": max(0.0, min(100.0, float(pct))),
        "msg": msg or JOBS[job_id].get("msg", "")
    })

@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    data = JOBS.get(job_id)
    if not data:
        raise HTTPException(404, "Unknown job id.")
    return data

@app.post("/api/convert")
async def convert(request: Request,
                  file: UploadFile = File(...),
                  target: str = Form(...),
                  category: Optional[str] = Form(None)):
    _sweep_tmp()
    _rate_limit(_ip(request))

    if not file.filename:
        raise HTTPException(400, "No file provided.")
    ext = (Path(file.filename).suffix or "").lower().lstrip(".")
    if not ext:
        raise HTTPException(400, "File must have an extension.")
    if target.lower().lstrip(".") == ext.lower():
        raise HTTPException(400, "Target format matches source. Pick a different format.")

    src_path = await _save_upload(file, MAX_SIZE_BYTES)

    # Decide category if not set
    if category is None:
        if ext in IMAGE_IN and target in IMAGE_OUT: category = "image"
        elif ext in AV_IN and target in AV_OUT:     category = "av"
        elif ext in DOC_IN and target in DOC_OUT:   category = "doc"
        elif ext in DATA_IN and target in DATA_OUT: category = "data"
        else:
            if   ext in IMAGE_IN: category = "image"
            elif ext in AV_IN:    category = "av"
            elif ext in DOC_IN:   category = "doc"
            elif ext in DATA_IN:  category = "data"
            else:                 category = "doc"

    job_id = secrets.token_hex(8)
    JOBS[job_id] = {"status": "processing", "percent": 0.0, "msg": "Queued"}

    def _run():
        try:
            def prog(p, m=""): _set_progress(job_id, p, m)
            if category == "image":
                out_path = convert_image(src_path, target, progress=prog)
            elif category == "av":
                out_path = convert_av(src_path, target, progress=prog)
            elif category == "doc":
                out_path = convert_doc(src_path, target, progress=prog)
            elif category == "data":
                _set_progress(job_id, 10, "Parsing data")
                if target == "phonecsv":
                    out_path = data_phone_clean(src_path, default_region=None)
                elif target == "csv" and ext == "vcf":
                    out_path = data_vcf_to_csv(src_path)
                elif target == "vcf" and ext == "csv":
                    out_path = data_csv_to_vcf(src_path)
                elif target == "vtt" and ext == "srt":
                    out_path = data_srt_to_vtt(src_path)
                elif target == "srt" and ext == "vtt":
                    out_path = data_vtt_to_srt(src_path)
                elif target == "csv_from_json" and ext == "json":
                    out_path = data_json_to_csv(src_path)
                elif target == "json_from_csv" and ext == "csv":
                    out_path = data_csv_to_json(src_path)
                elif target == "json_from_yaml" and ext in {"yaml","yml"}:
                    out_path = data_yaml_to_json(src_path)
                elif target == "yaml_from_json" and ext == "json":
                    out_path = data_json_to_yaml(src_path)
                else:
                    raise HTTPException(400, "Unsupported data conversion pairing.")
                _set_progress(job_id, 100, "Done")
            else:
                raise HTTPException(400, "Unsupported category.")

            JOBS[job_id].update({
                "status": "done",
                "download": f"/download/{out_path.name}",
                "filename": out_path.name,
                "percent": 100.0,
                "msg": "Done",
            })
        except HTTPException as he:
            JOBS[job_id].update({"status": "error", "error": he.detail, "percent": 100.0})
        except Exception as e:
            JOBS[job_id].update({"status": "error", "error": str(e), "percent": 100.0})

    Thread(target=_run, daemon=True).start()
    return JSONResponse({"job_id": job_id, "status": "processing"})

@app.get("/download/{fname}")
async def download(fname: str):
    p = TMP_DIR / fname
    if not p.exists():
        raise HTTPException(404, "File not found or expired.")
    return FileResponse(str(p), filename=fname)
