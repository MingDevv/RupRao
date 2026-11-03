# -*- coding: utf-8 -*-
"""
STEP 1 — ให้ AI "หาหน้าคน" ในรูป (Face Detection)
=================================================
สิ่งที่สคริปต์นี้ทำ:
  1. โหลดโมเดล AI ชื่อ SCRFD (โมเดลที่ถูกฝึกมาแล้วว่า "หน้าคน" หน้าตาเป็นยังไง)
  2. อ่านรูปจากไฟล์ (รูปในสายตาคอมพิวเตอร์ = ตารางตัวเลขมหึมา)
  3. ให้โมเดลสแกนหาหน้าคนทุกหน้าในรูป
  4. วาดกรอบเขียวรอบหน้า + แปะ "คะแนนความมั่นใจ" (0.00 - 1.00)
  5. เซฟรูปผลลัพธ์ไปที่ data/output/

วิธีใช้:
  python step1_detect_faces.py                  -> ใช้รูปแรกใน data/input
  python step1_detect_faces.py ชื่อรูป.jpg       -> ใช้รูปที่ระบุ
"""
import sys
from pathlib import Path

import cv2                      # opencv: เครื่องมือจัดการรูปภาพ
from insightface.app import FaceAnalysis   # ตัวช่วยโหลดโมเดล AI ของ insightface

# หา "ตำแหน่ง" ของโฟลเดอร์โปรเจกต์จากตำแหน่งไฟล์สคริปต์นี้เอง
BASE = Path(__file__).resolve().parent
INPUT_DIR = BASE / "data" / "input"
OUTPUT_DIR = BASE / "data" / "output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)   # ถ้าไม่มีโฟลเดอร์ output ให้สร้าง


def pick_input_image():
    """เลือกว่าจะสแกนรูปไหน: จากที่ผู้ใช้พิมพ์ หรือรูปแรกใน data/input"""
    if len(sys.argv) > 1:
        return Path(sys.argv[1])
    for ext in ("*.jpg", "*.jpeg", "*.png", "*.webp"):
        files = sorted(INPUT_DIR.glob(ext))
        if files:
            return files[0]
    raise SystemExit("❌ ไม่พบรูป! เอารูปไปใส่โฟลเดอร์ data/input ก่อนนะ")


def main():
    img_path = pick_input_image()
    print(f"📷 รูปที่จะสแกน: {img_path.name}\n")

    print("🧠 กำลังโหลดโมเดล AI... (ครั้งแรกจะดาวน์โหลดโมเดล ~300MB ให้รอหน่อยนึง)")
    app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
    app.prepare(ctx_id=-1, det_size=(640, 640))   # det_size = ย่อรูปเป็น 640px ก่อนสแกนเพื่อความเร็ว
    print("✅ โมเดลพร้อมทำงาน!\n")

    # cv2.imread อ่านรูปออกมาเป็น "ตารางตัวเลข" (สูง x กว้าง x 3 ค่าสี RGB)
    img = cv2.imread(str(img_path))
    if img is None:
        raise SystemExit(f"❌ อ่านรูปไม่ได้: {img_path}")
    h, w = img.shape[:2]
    print(f"📐 รูปขนาด {w} x {h} พิกเซล  = ตารางตัวเลข {w * h:,} ช่องที่ AI ต้องมองหา\n")

    # ✨ บรรทัดสำคัญที่สุดของวันนี้: ให้โมเดลสแกนหาหน้า
    faces = app.get(img)

    print(f"🎯 เจอหน้าทั้งหมด {len(faces)} หน้า!")
    for i, face in enumerate(faces):
        x1, y1, x2, y2 = face.bbox.astype(int)   # กรอบหน้า: มุมซ้ายบน (x1,y1) กับมุมขวาล่าง (x2,y2)
        score = float(face.det_score)            # ความมั่นใจของโมเดล 0.0-1.0
        print(f"   หน้าที่ {i + 1}: มั่นใจ {score:.2f}  ตำแหน่ง x={x1}-{x2}, y={y1}-{y2}")
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 0), 3)   # วาดกรอบเขียว
        cv2.putText(img, f"{score:.2f}", (x1, max(y1 - 10, 25)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 200, 0), 2)

    out_path = OUTPUT_DIR / f"step1_{img_path.stem}_result.jpg"
    cv2.imwrite(str(out_path), img)
    print(f"\n💾 เซฟรูปผลลัพธ์แล้ว: {out_path}")


if __name__ == "__main__":
    main()
