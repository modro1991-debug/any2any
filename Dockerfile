FROM python:3.11-slim

# System deps for conversions (each item on its own line with a trailing backslash)
RUN apt-get update && apt-get install -y --no-install-recommends \
    imagemagick \
    ghostscript \
    ffmpeg \
    libreoffice \
    p7zip-full \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PORT=8000
EXPOSE 8000

# Start FastAPI (Render will set PORT)
CMD ["bash", "-lc", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
