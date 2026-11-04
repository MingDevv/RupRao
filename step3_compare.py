# -*- coding: utf-8 -*-
"""
STEP 3 — เทียบหน้า 2 รูป: "ใช่คนเดียวกันมั้ย?"
============================================
หลักการ: เอาลายเซ็น (เวกเตอร์ 512 ตัวเลข) ของ 2 หน้า มาวัด "มุม" ระหว่างกัน
- มุมเล็กมาก (cosine ใกล้ 1.0) → หน้าเดียวกัน
- มุมกว้าง (cosine ใกล้ 0.0) → คนละคน

เกณฑ์ตัดสินของโปรเจกต์เรา (ค่าเริ่มต้น — เฟส 3 จะจูนจากข้อมูลจริง):
  >= 0.45  → น่าจะคนเดียวกัน
  0.30-0.45 → โซนก้ำกึ่ง ไม่ชัวร์
  < 0.30   → คนละคน

วิธีใช้:  python step3_compare.py <รูปA> <รูปB>
"""
import sys

import face_lib

SAME_MIN = 0.45   # ตั้งแต่ค่านี้ขึ้นไป = คนเดียวกัน
DIFF_MAX = 0.30   # ต่ำกว่าค่านี้ = คนละคน


def best_embedding(img):
    """คืนลายเซ็นของ "หน้าที่โมเดลมั่นใจสุด" ในรูป (กันกรณีรูปมีหลายคน)"""
    faces = face_lib.detect_faces(img)
    if not faces:
        raise SystemExit("❌ ไม่เจอหน้าคนในรูปนี้")
    return max(faces, key=lambda f: f.det_score).embedding


def main():
    if len(sys.argv) < 3:
        raise SystemExit("ใช้แบบนี้: python step3_compare.py <รูปA> <รูปB>")

    name_a, name_b = sys.argv[1], sys.argv[2]
    print(f"🆚 เทียบ: {name_a}  vs  {name_b}\n")
    emb_a = best_embedding(face_lib.load_image(name_a))
    emb_b = best_embedding(face_lib.load_image(name_b))

    sim = face_lib.cosine(emb_a, emb_b)
    print(f"📐 ความเหมือน (cosine similarity) = {sim:.4f}")

    if sim >= SAME_MIN:
        print(f"✅ {sim:.2f} ≥ {SAME_MIN} → น่าจะเป็นคนเดียวกัน!")
    elif sim >= DIFF_MAX:
        print(f"🤔 {sim:.2f} อยู่โซนก้ำกึ่ง → ไม่ชัวร์ ควรดูรูปช่วยตัดสินเพิ่ม")
    else:
        print(f"❌ {sim:.2f} < {DIFF_MAX} → คนละคน")


if __name__ == "__main__":
    main()
