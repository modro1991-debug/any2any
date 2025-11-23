import os, shutil, subprocess
from pathlib import Path
from typing import Literal

TMP_DIR = Path("tmp")
TMP_DIR.mkdir(exist_ok=True)

IS_WINDOWS = os.name == "nt"
IM_CMD = "magick" if IS_WINDOWS else "convert"

SOFFICE = shutil.which("soffice") or (
    r"C:\Program Files\LibreOffice\program\soffice.exe" if IS_WINDOWS else "soffice"
)

def run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{proc.stdout}")

def change_ext(p: Path, new_ext: str) -> Path:
    return p.with_suffix("." + new_ext.lstrip("."))

# Images
ImageFormat = Literal["jpg","jpeg","png","webp","gif","tiff","bmp","ico","pdf"]
def convert_image(inp: Path, target_ext: ImageFormat) -> Path:
    outp = change_ext(inp, target_ext)
    run([IM_CMD, str(inp), "-strip", str(outp)])
    return outp

# Audio/Video
AvFormat = Literal["mp3","wav","aac","flac","ogg","mp4","mkv","mov","webm"]
def convert_av(inp: Path, target_ext: AvFormat) -> Path:
    outp = change_ext(inp, target_ext)
    if target_ext in {"mp3"}:
        run(["ffmpeg","-y","-i",str(inp),"-vn","-codec:a","libmp3lame","-qscale:a","2",str(outp)])
    elif target_ext in {"wav"}:
        run(["ffmpeg","-y","-i",str(inp),"-vn","-acodec","pcm_s16le",str(outp)])
    elif target_ext in {"aac"}:
        run(["ffmpeg","-y","-i",str(inp),"-vn","-c:a","aac","-b:a","192k",str(outp)])
    elif target_ext in {"flac"}:
        run(["ffmpeg","-y","-i",str(inp),"-vn","-c:a","flac",str(outp)])
    elif target_ext in {"ogg"}:
        run(["ffmpeg","-y","-i",str(inp),"-vn","-c:a","libvorbis","-qscale:a","5",str(outp)])
    elif target_ext in {"mp4"}:
        run(["ffmpeg","-y","-i",str(inp),"-c:v","libx264","-preset","veryfast","-crf","23","-c:a","aac","-b:a","128k",str(outp)])
    elif target_ext in {"mkv","webm"}:
        run(["ffmpeg","-y","-i",str(inp),"-c:v","libvpx-vp9" if target_ext=="webm" else "libx264","-b:v","0","-crf","30","-c:a","libopus",str(outp)])
    elif target_ext in {"mov"}:
        run(["ffmpeg","-y","-i",str(inp),"-c:v","prores_ks","-profile:v","3","-c:a","aac",str(outp)])
    else:
        raise ValueError("Unsupported AV target")
    return outp

# Documents
DocTarget = Literal["pdf","docx","xlsx","pptx","odt","ods","odp"]
def convert_doc(inp: Path, target_ext: DocTarget) -> Path:
    run([SOFFICE, "--headless", "--convert-to", target_ext, "--outdir", str(TMP_DIR), str(inp)])
    outp_candidate = change_ext(TMP_DIR / inp.name, target_ext)
    if outp_candidate.exists():
        return outp_candidate
    for f in TMP_DIR.glob(f"*{'.'+target_ext}"):
        if f.stat().st_mtime >= inp.stat().st_mtime:
            return f
    raise RuntimeError("Conversion output not found.")
