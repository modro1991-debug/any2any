FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# System deps you need (LibreOffice + ffmpeg + fonts)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-writer libreoffice-calc libreoffice-impress \
    default-jre-headless \
    fonts-dejavu fonts-liberation \
    ffmpeg \
    poppler-utils \
 && rm -rf /var/lib/apt/lists/*



WORKDIR /app

# ---- install Python deps first (better cache reuse) ----
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# ---- now copy your app code ----
COPY . /app

ENV PORT=8000
EXPOSE 8000
CMD ["bash","-lc","python -m uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
