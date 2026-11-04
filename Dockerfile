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
    git \
    && rm -rf /var/lib/apt/lists/*

# กำหนดสิทธิ์ผู้ใช้งาน user (UID 1000) ตามมาตรฐานของ Hugging Face Spaces
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

WORKDIR $HOME/app

# คัดลอกและติดตั้ง Python dependencies
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# สั่งดาวน์โหลดโมเดล InsightFace (buffalo_l) ล่วงหน้าในขั้นตอน build
# เพื่อให้เซิร์ฟเวอร์เปิดใช้งานได้ทันทีใน 1 วินาที ไม่ต้องรอโหลดตอนเริ่มรัน
RUN python -c "from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider']); app.prepare(ctx_id=0, det_size=(640, 640))"

# คัดลอกซอร์สโค้ดของโปรเจกต์
COPY --chown=user . .

# สร้างโฟลเดอร์สำหรับเก็บข้อมูลชั่วคราว
RUN mkdir -p data/thumbs data/input static

# พอร์ตมาตรฐานของ Hugging Face Spaces
EXPOSE 7860

# คำสั่งสตาร์ตเซิร์ฟเวอร์
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "7860"]
