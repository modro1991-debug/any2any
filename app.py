import os, time, secrets
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from converters import TMP_DIR, convert_image, convert_av, convert_doc

app = FastAPI(title="Any2Any Converter")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

MAX_SIZE_BYTES = int(os.getenv("MAX_SIZE_BYTES", 50 * 1024 * 1024))  # 50MB
MAX_REQUESTS = int(os.getenv("MAX_REQUESTS_PER_10M", 30))
WINDOW = 600  # seconds
BUCKET = {}

IMAGE_IN = {"jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"}
IMAGE_OUT = IMAGE_IN
AV_IN = {"mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"}
AV_OUT = AV_IN
DOC_IN = {"pdf","doc","docx","ppt","pptx","xls","xlsx","odt","odp","ods","rtf","txt"}
DOC_OUT = {"pdf","docx","xlsx","pptx","odt","ods","odp"}

def ip_from(request: Request) -> str:
    return request.headers.get("x-forwarded-for", request.client.host)

def rate_limit(ip: str):
    now = time.time()
    window_start = now - WINDOW
    BUCKET.setdefault(ip, [])
    BUCKET[ip] = [t for t in BUCKET[ip] if t >= window_start]
    if len(BUCKET[ip]) >= MAX_REQUESTS:
        raise HTTPException(429, "Too many requests, please try again later.")
    BUCKET[ip].append(now)

def secure_filename(name: str) -> str:
    return secrets.token_hex(16) + (Path(name).suffix or "")

def sweep_tmp(ttl_seconds: int = 20 * 60):
    now = time.time()
    for f in TMP_DIR.glob("*"):
        try:
            if now - f.stat().st_mtime > ttl_seconds:
                f.unlink()
        except Exception:
            pass

async def _save_upload(f: UploadFile, limit: int) -> Path:
    dest = TMP_DIR / secure_filename(f.filename or "file.bin")
    size = 0
    with dest.open("wb") as out:
        while True:
            chunk = await f.read(1024 * 1024)
            if not chunk: break
            size += len(chunk)
            if size > limit:
                out.close(); dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File too large (>{limit//(1024*1024)}MB).")
            out.write(chunk)
    dest.chmod(0o600); dest.touch()
    return dest

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    sweep_tmp()
    return templates.TemplateResponse("index.html", {"request": request})

@app.post("/api/convert")
async def convert(request: Request,
                  file: UploadFile = File(...),
                  target: str = Form(...),
                  category: Optional[str] = Form(None)):
    sweep_tmp()
    rate_limit(ip_from(request))
    if not file.filename:
        raise HTTPException(400, "No file provided.")
    ext = (Path(file.filename).suffix or "").lower().lstrip(".")
    if not ext:
        raise HTTPException(400, "File must have an extension.")

    src_path = await _save_upload(file, MAX_SIZE_BYTES)
    try:
        cat = category
        if cat is None:
            if ext in IMAGE_IN and target in IMAGE_OUT: cat = "image"
            elif ext in AV_IN and target in AV_OUT:   cat = "av"
            elif ext in DOC_IN and target in DOC_OUT: cat = "doc"
            else: cat = "doc" if ext in DOC_IN else ("image" if ext in IMAGE_IN else "av")

        if cat == "image":
            if ext not in IMAGE_IN or target not in IMAGE_OUT:
                raise HTTPException(400, "Unsupported image conversion.")
            out_path = convert_image(src_path, target)
        elif cat == "av":
            if ext not in AV_IN or target not in AV_OUT:
                raise HTTPException(400, "Unsupported audio/video conversion.")
            out_path = convert_av(src_path, target)
        elif cat == "doc":
            if ext not in DOC_IN or target not in DOC_OUT:
                raise HTTPException(400, "Unsupported document conversion.")
            out_path = convert_doc(src_path, target)
        else:
            raise HTTPException(400, "Unsupported category.")

        return JSONResponse({"download": f"/download/{out_path.name}", "filename": out_path.name})
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(500, f"Conversion failed: {e}")

@app.get("/download/{fname}")
async def download(fname: str):
    p = TMP_DIR / fname
    if not p.exists():
        raise HTTPException(404, "File not found or expired.")
    return FileResponse(str(p), filename=fname)
