# Dockerfile for RupRao (รูปเรา)
# รองรับทั้ง Hugging Face Spaces (Port 7860) และ VPS / Docker ทั่วไป (Port 8000)
FROM python:3.9-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=7860

# ติดตั้งไลบรารีของระบบที่จำเป็นสำหรับ OpenCV, ONNX Runtime และ InsightFace
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# คัดลอกและติดตั้ง Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# คัดลอกซอร์สโค้ดของโปรเจกต์
COPY . .

# สร้างโฟลเดอร์ data และ static ถ้ายังไม่มี
RUN mkdir -p data/thumbs data/input static

# เปิดพอร์ตสำหรับรับการเชื่อมต่อ (HF Spaces ใช้ 7860)
EXPOSE 7860

# คำสั่งรัน FastAPI เซิร์ฟเวอร์
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
