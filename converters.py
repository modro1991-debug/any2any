import os
import subprocess
import time
import zipfile
from pathlib import Path

from PIL import Image, ImageOps
import pytesseract
from pytesseract import Output
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader
from docx import Document
from pdf2docx import Converter as Pdf2DocxConverter

# Where we store temporary files
TMP_DIR = Path(os.getenv("TMP_DIR", "/tmp/any2any"))
TMP_DIR.mkdir(parents=True, exist_ok=True)


# ----------------- Helpers -----------------


def _rand_name(ext: str) -> Path:
    """Generate a random temp file path with given extension."""
    import secrets

    name = secrets.token_hex(16)
    return TMP_DIR / f"{name}.{ext.lstrip('.')}"


def _report(progress, pct: int, msg: str):
    """Call a progress callback if provided."""
    if callable(progress):
        try:
            progress(pct, msg)
        except Exception:
            pass


# ----------------- PDF → images (ZIP) -----------------


def _pdf_to_images_zip(
    src_path: Path,
    target: str,
    dpi: int = 150,
    progress=None,
) -> Path:
    """
    Convert a PDF into a ZIP of images (one per page).
    Uses pdftoppm (from poppler-utils).
    target: 'jpg' | 'png' | 'webp' (webp uses an extra Pillow step).
    """
    target = target.lower()
    if target not in {"jpg", "png", "webp"}:
        raise RuntimeError(f"Unsupported PDF->image target: {target}")

    _report(progress, 5, "Inspecting PDF…")

    # First, find number of pages using pdfinfo
    try:
        info = subprocess.run(
            ["pdfinfo", str(src_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=True,
        )
        pages = 1
        for line in info.stdout.splitlines():
            if line.lower().startswith("pages:"):
                pages = int(line.split(":", 1)[1].strip())
                break
    except Exception:
        pages = 1  # fallback

    _report(progress, 10, f"Converting {pages} page(s)…")

    # Use pdftoppm to generate images
    # We'll output to a temp prefix, then collect and zip.
    prefix = TMP_DIR / f"pp_{int(time.time())}"
    ppm_fmt = "png" if target in {"png", "webp"} else "jpeg"

    cmd = [
        "pdftoppm",
        "-r",
        str(dpi),
        f"-{ppm_fmt}",
        str(src_path),
        str(prefix),
    ]
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"pdftoppm failed (exit {proc.returncode}). STDERR: {proc.stderr}"
        )

    # Collect generated images
    img_paths = sorted(TMP_DIR.glob(prefix.name + "-*." + ("png" if ppm_fmt == "png" else "jpg")))

    if not img_paths:
        raise RuntimeError("No pages were generated from PDF.")

    # Optionally convert PNG -> WEBP if requested
    final_imgs = []
    for idx, img_path in enumerate(img_paths, start=1):
        _report(progress, 10 + int(70 * idx / max(1, pages)), f"Page {idx}/{pages}")
        if target == "webp":
            with Image.open(img_path) as im:
                out = TMP_DIR / f"{img_path.stem}.webp"
                im.save(out, format="WEBP", quality=90, method=4)
            final_imgs.append(out)
        else:
            # For jpg/png we can reuse what pdftoppm produced (jpg) or rename
            if ppm_fmt == "jpeg" and target == "jpg":
                final_imgs.append(img_path)
            else:
                # png case or mismatch
                with Image.open(img_path) as im:
                    out = TMP_DIR / f"{img_path.stem}.{target}"
                    im.save(out, format=target.upper())
                final_imgs.append(out)

    # Zip them
    zip_path = _rand_name("zip")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for i, p in enumerate(final_imgs, start=1):
            arcname = f"page-{i}.{target}"
            zf.write(p, arcname=arcname)

    _report(progress, 100, "PDF pages packaged into ZIP.")
    return zip_path


# ----------------- Image → searchable PDF -----------------


def _image_to_searchable_pdf(
    src_path: Path,
    dpi: int = 300,
    progress=None,
    lang: str = "eng",
) -> Path:
    """
    High-quality searchable PDF:
    - run OCR on a preprocessed grayscale copy (better detection)
    - draw ORIGINAL color image as the visual layer
    - overlay INVISIBLE text so it's selectable/searchable but not visible
    """
    _report(progress, 5, "Preparing image for OCR…")

    orig = Image.open(src_path).convert("RGB")
    ow, oh = orig.size

    # Build OCR image (grayscale + contrast + optional upscale)
    ocr_img = orig.convert("L")
    ocr_img = ImageOps.autocontrast(ocr_img)

    min_side = min(ow, oh)
    scale = 1.0
    target_min = 1200
    if min_side < target_min:
        scale = target_min / float(min_side)
        new_size = (int(ow * scale), int(oh * scale))
        ocr_img = ocr_img.resize(new_size, Image.LANCZOS)
    w, h = ocr_img.size

    _report(progress, 25, "Running OCR (Tesseract)…")
    config = "--oem 3 --psm 6"
    data = pytesseract.image_to_data(
        ocr_img,
        lang=lang,
        config=config,
        output_type=Output.DICT,
    )

    # PDF page size based on original image size & dpi
    width_pt = ow * 72.0 / dpi
    height_pt = oh * 72.0 / dpi

    out = _rand_name("pdf")
    c = canvas.Canvas(str(out), pagesize=(width_pt, height_pt))

    # Draw original color image
    _report(progress, 60, "Placing image…")
    img_reader = ImageReader(orig)
    c.drawImage(img_reader, 0, 0, width=width_pt, height=height_pt)

    _report(progress, 80, "Overlaying invisible text layer…")
    text_obj = c.beginText()
    if hasattr(text_obj, "setTextRenderMode"):
        # 3 = invisible but selectable
        text_obj.setTextRenderMode(3)

    text_obj.setFont("Helvetica", 8)

    n = len(data["text"])
    for i in range(n):
        text = (data["text"][i] or "").strip()
        if not text:
            continue
        try:
            x = data["left"][i]
            y = data["top"][i]
            bw = data["width"][i]
            bh = data["height"][i]
        except Exception:
            continue

        x_orig = x / scale
        y_orig = y / scale
        bw_orig = bw / scale
        bh_orig = bh / scale

        x_pt = x_orig * 72.0 / dpi
        y_pt = height_pt - ((y_orig + bh_orig * 0.8) * 72.0 / dpi)

        text_obj.setTextOrigin(x_pt, y_pt)
        text_obj.textLine(text)

    c.drawText(text_obj)
    c.showPage()
    c.save()

    if not out.exists():
        raise RuntimeError("Searchable PDF was not created on disk")

    _report(progress, 100, "Done")
    return out


# ----------------- Image → DOCX -----------------


def _image_to_docx(
    src_path: Path,
    progress=None,
    lang: str = "eng",
) -> Path:
    """
    Convert an image (photo/screenshot) into an editable DOCX using OCR.
    """
    _report(progress, 5, "Reading image for DOCX OCR…")

    img = Image.open(src_path)

    _report(progress, 25, "Extracting text…")
    text = pytesseract.image_to_string(img, lang=lang)

    _report(progress, 60, "Building DOCX…")
    doc = Document()
    for line in text.splitlines():
        # Preserve empty lines as paragraph breaks
        doc.add_paragraph(line if line.strip() else "")

    out = _rand_name("docx")
    doc.save(out)

    _report(progress, 100, "Done")
    return out


# ----------------- Public API functions -----------------


# Sets for the backend to validate source/target
IMAGE_IN = {"jpg", "jpeg", "png", "webp", "tiff", "bmp"}
IMAGE_OUT = {"pdf", "docx", "jpg", "png", "webp"}

DOC_IN = {"pdf"}
DOC_OUT = {"docx"}


def convert_image(
    src_path: Path,
    target: str,
    progress=None,
    dpi: int = 150,
) -> Path:
    """
    Handle all 'image' category conversions for v1:
    - image -> searchable PDF
    - image -> editable DOCX
    - PDF -> images ZIP (delegated when src is pdf)
    (image->image conversions are NOT exposed in v1 UI, but you can keep jpg/png/webp if needed.)
    """
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")

    # PDF source -> images ZIP
    if src_ext == "pdf":
        if target not in {"jpg", "png", "webp"}:
            raise RuntimeError("For PDF source, image targets must be JPG, PNG, or WEBP.")
        return _pdf_to_images_zip(src_path, target, dpi=dpi, progress=progress)

    # source is a regular image
    if src_ext not in IMAGE_IN:
        raise RuntimeError(f"Unsupported image source type: {src_ext}")

    if target not in IMAGE_OUT:
        raise RuntimeError(f"Unsupported image target: {target}")

    # Image -> searchable PDF
    if target == "pdf":
        return _image_to_searchable_pdf(src_path, dpi=dpi, progress=progress)

    # Image -> editable DOCX
    if target == "docx":
        return _image_to_docx(src_path, progress=progress)

    # If you want to support image->image, keep this:
    _report(progress, 5, "Opening image…")
    with Image.open(src_path) as im:
        if target in {"jpg", "jpeg"} and im.mode in ("RGBA", "P"):
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

        _report(progress, 60, "Encoding image…")
        if out_ext == "ico":
            icon = ImageOps.contain(im, (256, 256))
            icon.save(out, format="ICO")
        else:
            fmt = {"jpg": "JPEG", "png": "PNG", "webp": "WEBP"}.get(out_ext, out_ext.upper())
            im.save(out, format=fmt, **save_kwargs)

    _report(progress, 100, "Done")
    return out


def convert_doc(
    src_path: Path,
    target: str,
    progress=None,
) -> Path:
    """
    Handle 'doc' category conversions for v1:
    - PDF -> DOCX (via pdf2docx)
    """
    target = target.lower()
    src_ext = src_path.suffix.lower().lstrip(".")

    if src_ext not in DOC_IN:
        raise RuntimeError(f"Unsupported document source: {src_ext}")
    if target not in DOC_OUT:
        raise RuntimeError(f"Unsupported document target: {target}")

    # PDF -> DOCX
    if src_ext == "pdf" and target == "docx":
        _report(progress, 10, "Converting PDF to DOCX…")
        out = _rand_name("docx")
        cv = Pdf2DocxConverter(str(src_path))
        try:
            cv.convert(str(out))
        finally:
            cv.close()
        _report(progress, 100, "Done")
        return out

    raise RuntimeError("Unsupported document conversion in v1.")
