import os, time, secrets
from pathlib import Path
from typing import Optional
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# uses the converters module you already added
from converters import TMP_DIR, convert_image, convert_av, convert_doc

app = FastAPI(title="Any2Any Converter")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# --- Config ---
MAX_SIZE_BYTES = int(os.getenv("MAX_SIZE_BYTES", 50 * 1024 * 1024))  # 50MB
MAX_REQUESTS = int(os.getenv("MAX_REQUESTS_PER_10M", 30))
WINDOW = 600  # seconds
BUCKET = {}   # naive in-memory rate limit bucket

# --- Allowed formats ---
IMAGE_IN = {"jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"}
IMAGE_OUT = IMAGE_IN
AV_IN = {"mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"}
AV_OUT = AV_IN
DOC_IN = {"pdf","doc","docx","ppt","pptx","xls","xlsx","odt","odp","ods","rtf","txt"}
DOC_OUT = {"pdf","docx","xlsx","pptx","odt","ods","odp"}

# --- Helpers ---
def _ip(request: Request) -> str:
    # Render/Reverse proxies set X-Forwarded-For
    return request.headers.get("x-forwarded-for", request.client.host)

def _rate_limit(ip: str):
    now = time.time()
    window_start = now - WINDOW
    BUCKET.setdefault(ip, [])
    BUCKET[ip] = [t for t in BUCKET[ip] if t >= window_start]
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

# --- Pages ---
@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    _sweep_tmp()
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/privacy", response_class=HTMLResponse)
async def privacy(request: Request):
    # ensure you created templates/privacy.html (we provided earlier)
    return templates.TemplateResponse("privacy.html", {"request": request})

@app.get("/cookies", response_class=HTMLResponse)
async def cookies(request: Request):
    # ensure you created templates/cookies.html (we provided earlier)
    return templates.TemplateResponse("cookies.html", {"request": request})

# --- API ---
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

    src_path = await _save_upload(file, MAX_SIZE_BYTES)
    try:
        # detect category if not provided
        cat = category
        if cat is None:
            if ext in IMAGE_IN and target in IMAGE_OUT: cat = "image"
            elif ext in AV_IN and target in AV_OUT:   cat = "av"
            elif ext in DOC_IN and target in DOC_OUT: cat = "doc"
            else:
                # guess best-effort
                if ext in IMAGE_IN: cat = "image"
                elif ext in AV_IN: cat = "av"
                else: cat = "doc"

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

        # NOTE (GDPR): we do NOT store files anywhere. Only temp processing; auto-swept.
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
    # File lives only briefly in tmp; we do not persist anything.
    return FileResponse(str(p), filename=fname)
