import os
import time
import secrets
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware

from converters import (
    TMP_DIR,
    convert_image,
    convert_doc,
    IMAGE_IN,
    IMAGE_OUT,
    DOC_IN,
    DOC_OUT,
)

app = FastAPI(title="Any2Any Converter v1")

# CORS: safe for now; tighten later for specific domain
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Basic limits and rate limiting
MAX_SIZE_BYTES = int(os.getenv("MAX_SIZE_BYTES", 50 * 1024 * 1024))  # 50MB
MAX_REQUESTS = int(os.getenv("MAX_REQUESTS_PER_10M", 30))
WINDOW = 600  # 10 minutes window
BUCKET = {}   # ip -> [timestamps]


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
                raise HTTPException(
                    413, f"File too large (>{limit // (1024 * 1024)}MB)."
                )
            out.write(chunk)
    dest.chmod(0o600)
    dest.touch()
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


@app.post("/api/convert")
async def convert(
    request: Request,
    file: UploadFile = File(...),
    target: str = Form(...),
    category: Optional[str] = Form(None),
):
    """
    Main conversion endpoint for v1.

    Supported flows:
    - image (jpg/png/webp/tiff/bmp) -> searchable PDF
    - image -> editable DOCX
    - PDF -> images (JPG/PNG/WEBP) ZIP
    - PDF -> DOCX
    """
    _sweep_tmp()
    _rate_limit(_ip(request))

    if not file.filename:
        raise HTTPException(400, "No file provided.")

    ext = (Path(file.filename).suffix or "").lower().lstrip(".")
    if not ext:
        raise HTTPException(400, "File must have an extension.")

    target = target.lower().lstrip(".")
    if target == ext:
        raise HTTPException(400, "Target format matches source. Pick a different format.")

    # Save upload
    src_path = await _save_upload(file, MAX_SIZE_BYTES)
    t0 = time.time()

    try:
        # Auto-detect category if not provided
        if category is None:
            if ext in IMAGE_IN or ext == "pdf":
                # In v1 we treat PDF as doc for PDF->DOCX,
                # but for PDF->images we call convert_image with category 'image'.
                # We will decide routing based on target below.
                # Start with heuristic:
                if ext == "pdf":
                    # If target is docx -> doc category; if jpg/png/webp -> image category
                    if target == "docx":
                        category = "doc"
                    else:
                        category = "image"
                else:
                    category = "image"
            elif ext in DOC_IN:
                category = "doc"
            else:
                category = "doc"  # fallback

        # Route based on category and target
        if category == "image":
            # - image -> pdf/docx
            # - pdf -> jpg/png/webp
            if ext == "pdf":
                if target not in {"jpg", "png", "webp"}:
                    raise HTTPException(
                        400,
                        "For PDF source in image mode, target must be JPG, PNG, or WEBP.",
                    )
            elif ext not in IMAGE_IN:
                raise HTTPException(400, f"Unsupported image source: {ext}")

            out_path = convert_image(src_path, target)

        elif category == "doc":
            # Only PDF -> DOCX in v1
            if ext not in DOC_IN:
                raise HTTPException(400, f"Unsupported document source: {ext}")
            if target not in DOC_OUT:
                raise HTTPException(
                    400,
                    f"Unsupported document target: {target}. Only DOCX is supported in v1.",
                )
            out_path = convert_doc(src_path, target)

        else:
            raise HTTPException(400, "Unsupported category.")

        elapsed = round(time.time() - t0, 2)
        return JSONResponse(
            {
                "download": f"/download/{out_path.name}",
                "filename": out_path.name,
                "process_time": elapsed,
            }
        )

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
