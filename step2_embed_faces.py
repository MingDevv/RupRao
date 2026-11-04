# -*- coding: utf-8 -*-
"""
STEP 2 — เปลี่ยน "หน้า" ให้เป็น "ลายเซ็น" 512 ตัวเลข (Embedding)
=============================================================
หัวใจของ RupRao อยู่ตรงนี้เลย:
- โมเดล ArcFace มองหน้า 1 ใบ → เปลี่ยนเป็นเวกเตอร์ 512 ตัวเลข
- หน้าคนเดียวกัน (มุมไหน แสงไหน) → เวกเตอร์ใกล้เคียงกันมาก
- หน้าคนต่างคน → เวกเตอร์ห่างกันไกล
เมื่อไหร่ทำ RupRao จริง เราจะ "ไม่เก็บรูปหน้า" เก็บแค่ตัวเลข 512 ตัวนี้เท่านั้น!

วิธีใช้:  python step2_embed_faces.py <รูป>
"""
import sys

import numpy as np

import face_lib


def main():
    if len(sys.argv) < 2:
        raise SystemExit("ใช้แบบนี้: python step2_embed_faces.py <รูป>")

    img = face_lib.load_image(sys.argv[1])
    faces = face_lib.detect_faces(img)
    print(f"🎯 เจอหน้า {len(faces)} หน้า\n")

    for i, face in enumerate(faces):
        emb = np.asarray(face.embedding, dtype=np.float32)
        print(f"หน้าที่ {i + 1}  (ตรวจจับมั่นใจ {float(face.det_score):.2f})")
        print(f"   ขนาดลายเซ็น   : {emb.shape[0]} ตัวเลข (เวกเตอร์ {emb.shape[0]} มิติ)")
        print(f"   8 ตัวแรก      : {np.round(emb[:8], 3)}")
        print(f"   ค่าต่ำสุด-สูงสุด: {emb.min():.3f} ถึง {emb.max():.3f}")
        print(f"   ความยาวเวกเตอร์: {np.linalg.norm(emb):.3f}")
        print()

    print("💡 สังเกต: ตัวเลขพวกนี้ตาคนอ่านไม่รู้เรื่อง แต่สำหรับ AI มันคือ \"ชื่อ-นามสกุล\" ของหน้านั้น")
    print("   ขั้นต่อไป (step3) เราจะเอาลายเซ็น 2 อันมาเทียบกันว่าเป็นคนเดียวกันมั้ย")


if __name__ == "__main__":
    main()
