---
title: RupRao (รูปเรา) - AI School Photo Search Engine
emoji: 📸
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# 📸 RupRao (รูปเรา) — ค้นหาตัวเองจากรูปกิจกรรมโรงเรียนด้วย AI

> **ระบบค้นหาภาพถ่ายบุคคลเฉพาะโรงเรียน:** อัปโหลดเซลฟี่ 1 รูป → AI สแกนหาใบหน้าของคุณจากภาพงานกิจกรรมทั้งหมดในเสี้ยววินาที → ดาวน์โหลดรูปตัวเองเป็น ZIP ได้ในคลิกเดียว

---

## 🏛️ สถาปัตยกรรมระบบ (Architecture)

RupRao ใช้ระบบจัดเก็บข้อมูลแบบแยกส่วน 2 ชั้น (Two-Tier Storage Engine):
1. **SQLite (`data/faces.db`):** จัดเก็บเมทาดาทาภาพ (`photos`), ตำแหน่งกรอบหน้า (`faces`), และคำร้องขอลบรูป (`reports`)
2. **FAISS (`data/index.faiss`):** เวกเตอร์อินเด็กซ์ 512 มิติ (`IndexFlatIP` + `IndexIDMap2`) คำนวณ Cosine Similarity ด้วยความเร็วระดับไมโครวินาที
3. **WebP Thumbnail (`data/thumbs/`):** รูปขนาดย่อ WebP 640px ประหยัดแบนด์วิดท์ โหลดเร็วบนมือถือ
4. **FastAPI Web Service (`server.py`):** รับคำขอค้นหา, ตรวจสอบรหัสผ่านโรงเรียน (School PIN), ส่งรูป และสตรีมไฟล์ ZIP

---

## 🔒 กติกาเหล็กด้านความเป็นส่วนตัว (PDPA Compliance)

1. **รูปเซลฟี่ค้นหาไม่เซฟลงดิสก์เด็ดขาด:** ประมวลผลในหน่วยความจำชั่วคราว (RAM) และลบทันทีหลังจากสกัดเวกเตอร์เสร็จ (Zero Disk Footprint)
2. **รูปภาพและข้อมูลส่วนบุคคลไม่ขึ้น GitHub:** มีการตั้ง `.gitignore` ป้องกันโฟลเดอร์ภาพและฐานข้อมูลอย่างรัดกุม
3. **มีระบบขอใช้สิทธิลบรูปภาพ (Right to be Forgotten):** มีปุ่ม `🚩 ขอลบรูปนี้` บนการ์ดทุกใบ ผู้ใช้สามารถกดแจ้งลบรูปภาพได้ทันที

---

## 🚀 วิธีการติดตั้งและรันใช้งาน

### 1. รันในเครื่อง (Local Development)
```bash
# 1. ติดตั้งไลบรารี
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. สแกนรูปภาพกิจกรรม
python scan_faces.py data/input

# 3. เปิดเว็บเซิร์ฟเวอร์
python server.py
# เปิดเบราว์เซอร์: http://localhost:8000 (หรือเข้าผ่าน Wi-Fi เดียวกัน)
```

### 2. รันผ่าน Docker
```bash
docker compose up -d --build
# เข้าใช้งานที่: http://localhost:8000
```

### 3. ขึ้น Hugging Face Spaces (ฟรี)
1. ไปที่ [Hugging Face Spaces](https://huggingface.co/spaces) -> New Space
2. เลือก **Docker** -> Blank
3. Git Push ซอร์สโค้ดของ RupRao ขึ้นไปยัง Space

---

## ⚙️ การตั้งค่า Environment Variables

| ตัวแปร | ค่าเริ่มต้น | รายละเอียด |
|---|---|---|
| `RUPRAO_PIN` | (เว้นว่างไว้) | รหัสผ่านสำหรับป้องกันคนนอกโรงเรียน (เว้นว่างหรือใส่ `0` เพื่อเปิดใช้งานโหมดสาธารณะ) |
| `RUPRAO_ADMIN_PIN` | (เว้นว่างไว้) | รหัสผ่านสำหรับผู้ดูแลระบบ (Admin PIN) ในการเข้าถึง `/admin` |
| `PORT` | `8000` (หรือ `7860` บน Docker) | พอร์ตที่เปิดให้บริการ |
