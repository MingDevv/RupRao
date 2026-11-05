FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=8000

# ติดตั้งไลบรารีของระบบที่จำเป็นสำหรับ OpenCV, ONNX Runtime และ InsightFace
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libgl1 \
    libglib2.0-0 \
    libgomp1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# รันด้วยผู้ใช้ธรรมดา (ไม่ใช่ root) เพื่อความปลอดภัย
RUN useradd -m -u 1000 user
WORKDIR /app
RUN chown user:user /app
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH

# คัดลอกและติดตั้ง Python dependencies
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir --user -r requirements.txt

# สั่งดาวน์โหลดโมเดล InsightFace (buffalo_l) ล่วงหน้าในขั้นตอน build
# เพื่อให้เซิร์ฟเวอร์เปิดใช้งานได้ทันที ไม่ต้องรอโหลดตอนเริ่มรัน
RUN python -c "from insightface.app import FaceAnalysis; app = FaceAnalysis(name='buffalo_l', providers=['CPUExecutionProvider']); app.prepare(ctx_id=-1, det_size=(640, 640))"

# คัดลอกซอร์สโค้ดของโปรเจกต์ (ไฟล์ลับและข้อมูลถูกกันไว้ใน .dockerignore)
COPY --chown=user . .

# โฟลเดอร์ข้อมูล — ตอนรันจริงจะถูกแทนด้วย volume ./data จาก docker-compose
RUN mkdir -p data/thumbs data/raw data/input

EXPOSE 8000

HEALTHCHECK --interval=60s --timeout=10s --start-period=60s \
    CMD curl -fs http://localhost:8000/api/health || exit 1

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
