# -*- coding: utf-8 -*-
"""
face_lib.py — กล่องเครื่องมือร่วมของโปรเจกต์ RupRao
=================================================
ทุกสคริปต์ (step2, step3, และโปรแกรมค้นหาในอนาคต) มาเรียกใช้ที่นี่
ประโยชน์: โมเดลโหลดครั้งเดียวต่อการรัน ไม่ต้องโหลดซ้ำทุกสคริปต์
"""
from pathlib import Path

import numpy as np

_app = None   # เก็บโมเดลไว้ในหน่วยความจำ — None = ยังไม่โหลด


def get_app():
    """โหลดโมเดล AI (ถ้ายังไม่ได้โหลด) แล้วคืนตัวใช้งาน — เรียกกี่ครั้งก็โหลดครั้งเดียว"""
    global _app
    if _app is None:
        print("🧠 โหลดโมเดล AI (โหลดครั้งเดียวต่อการรัน)...")
        from insightface.app import FaceAnalysis
        _app = FaceAnalysis(name="buffalo_l", providers=["CPUExecutionProvider"])
        _app.prepare(ctx_id=-1, det_size=(640, 640))
        print("✅ โมเดลพร้อม!\n")
    return _app


def load_image(path):
    """อ่านไฟล์รูปออกมาเป็นตารางตัวเลข — ถ้าไฟล์พังจะบอกชัดๆ"""
    import cv2
    img = cv2.imread(str(path))
    if img is None:
        raise SystemExit(f"❌ อ่านรูปไม่ได้: {path}")
    return img


def detect_faces(img_bgr):
    """รับรูป → คืน list ของหน้าที่เจอ
    แต่ละหน้ามีทั้ง: .bbox (กรอบ) .det_score (คะแนน) และ .embedding (ลายเซ็น 512 ตัวเลข)
    (insightface คำนวณ embedding ให้อัตโนมัติด้วยโมเดล ArcFace ที่แถมมากับ buffalo_l)"""
    return get_app().get(img_bgr)


def cosine(a, b):
    """วัดความเหมือนของเวกเตอร์ 2 ตัว (cosine similarity)
    ค่าใกล้ 1.0 = ทิศทางเดียวกัน (หน้าเดียวกัน) / ใกล้ 0.0 = ไม่เกี่ยวข้องกัน"""
    a = np.asarray(a, dtype=np.float32)
    b = np.asarray(b, dtype=np.float32)
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b)))
