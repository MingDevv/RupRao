# -*- coding: utf-8 -*-
"""
test_sensitivity.py — ห้องแล็บทดสอบ "ความ sensitive" ของระบบค้นหาหน้า
=====================================================================
ทดสอบ 3 เรื่อง (ตามคำขอของเพื่อน 😄):
  1) หน้าตัวเอง        -> ต้องตรงสูงๆ          (true positive)
  2) หน้าคนอื่น/หน้าเก๊  -> ต้องต่ำ ห้ามหลอก      (false positive = 0)
  3) หน้าเล็กจิว        -> เล็กแค่ไหนยังจับ/ค้นได้
พร้อมโชว์ว่า threshold แต่ละค่า (0.30-0.50) เปลี่ยนจำนวนผลลัพธ์ยังไง

วิธีใช้: เปิด server.py ค้างไว้ แล้วรัน  python test_sensitivity.py
"""
from pathlib import Path

import cv2
import numpy as np
import requests

BASE = Path(__file__).resolve().parent
INPUT = BASE / "data" / "input"
API = "http://localhost:8000"
THRESHOLDS = [0.30, 0.34, 0.40, 0.45, 0.50]   # ระดับที่อยากรู้ว่า "ถ้าตัดตรงนี้จะได้กี่รูป"


def search_file(img_path, threshold=0.30):
    """ส่งรูปเข้า /api/search ของเซิร์ฟเวอร์จริง (threshold ต่ำสุด = ได้คะแนนดิบมากที่สุด)"""
    with open(img_path, "rb") as f:
        r = requests.post(f"{API}/api/search",
                          files={"selfie": (img_path.name, f, "image/jpeg")},
                          data={"threshold": str(threshold)}, timeout=120)
    if r.status_code == 401:   # เผื่อเปิด PIN gate ในอนาคต
        with open(img_path, "rb") as f:
            r = requests.post(f"{API}/api/search",
                              headers={"X-School-PIN": ""},
                              files={"selfie": (img_path.name, f, "image/jpeg")},
                              data={"threshold": str(threshold)}, timeout=120)
    if r.status_code == 422:   # เซิร์ฟเวอร์มองไม่เห็นหน้าใน query (เช่นรูปเบลอ/หน้าเล็กเกิน)
        return None
    r.raise_for_status()
    return r.json().get("results") or []


def make_tiny_face_crop():
    """ครอปบริเวณรอบ 'หน้าเล็กจิ๋ว' จาก d.jpeg แบบเผื่อบริบทรอบข้างเยอะๆ และไม่ขยาย
    (บทเรียน: ขยายรูปเล็ก = เบลอ = detector มองไม่เห็น! ให้เห็นเป็น 'หน้าเล็กในภาพกลาง'
    เหมือนตอนสแกนรูปเต็มจะดีกว่า)"""
    img = cv2.imread(str(INPUT / "d.jpeg"))
    x1, y1, x2, y2 = 79, 174, 113, 216          # bbox จากผล step1 (หน้าเล็กสุด)
    m = 110                                      # เผื่อบริบทรอบข้าง 110px
    H, W = img.shape[:2]
    crop = img[max(0, y1 - m):min(H, y2 + m), max(0, x1 - m):min(W, x2 + m)]
    out = INPUT / "_tiny_face.jpg"
    cv2.imwrite(str(out), crop)
    return out


def scale_test():
    """ทดสอบ local: ย่อ d.jpeg เหลือ 50%/25%/12% แล้วดูว่ายังจับหน้าได้กี่หน้า
    (ตอบคำถาม 'หน้าน้อย/หน้าเล็ก' ในระดับการตรวจจับ ก่อนถึงขั้นค้นหา)"""
    import face_lib
    img = cv2.imread(str(INPUT / "d.jpeg"))
    h, w = img.shape[:2]
    print("\n🔬 ทดสอบการจับหน้าเมื่อรูปเล็กลง (d.jpeg ปกติ 3 หน้า)")
    for factor in (0.5, 0.25, 0.12):
        small = cv2.resize(img, (int(w * factor), int(h * factor)))
        p = INPUT / f"_scale_{int(factor * 100)}.jpg"
        cv2.imwrite(str(p), small)
        faces = face_lib.detect_faces(small)
        best = max((float(f.det_score) for f in faces), default=0)
        print(f"   ย่อเหลือ {int(factor * 100)}% ({small.shape[1]}px): เจอ {len(faces)} หน้า "
              f"(คะแนนสูงสุด {best:.2f})")


def main():
    # เช็คว่าเซิร์ฟเวอร์ตื่นอยู่
    r = requests.get(f"{API}/api/health", timeout=5)
    print(f"🩺 เซิร์ฟเวอร์: {r.json()['status']} | รูป {r.json()['photos']} | หน้า {r.json()['faces']}")

    # เตรียมหน้าเล็กจิว
    tiny = make_tiny_face_crop()
    print(f"✂️  สร้างหน้าเล็กจิวแล้ว: {tiny.name}\n")

    queries = [
        ("ming.jpeg    = หน้าตัวเอง", INPUT / "ming.jpeg"),
        ("d.jpeg       = รูปงานจริง (มีตัวเอง)", INPUT / "d.jpeg"),
        ("_a.jpg       = หน้าแปลกปลอม 1", INPUT / "_a.jpg"),
        ("_b.jpg       = หน้าแปลกปลอม 2", INPUT / "_b.jpg"),
        ("_c.jpg       = หน้าแปลกปลอม 3", INPUT / "_c.jpg"),
        ("_tiny_face   = หน้าเล็กจิวจากพื้นหลัง", tiny),
    ]

    header = f"{'query':<38}{'top%':>5}{'topID':>6}" + "".join(f"@{t:.2f}".rjust(7) for t in THRESHOLDS)
    print(header)
    print("-" * len(header))

    report = {}
    for label, path in queries:
        results = search_file(path)                       # ขอครั้งเดียวที่ 0.30 = คะแนนดิบ
        if results is None:
            print(f"{label:<38}{'—':>5}{'>':>6}   ← เซิร์ฟเวอร์มองไม่เห็นหน้าใน query นี้")
            continue
        top = results[0] if results else None
        top_pct = top["similarity_percent"] if top else 0
        top_id = f"#{top['photo_id']}" if top else "-"
        counts = [sum(1 for x in results if x["similarity"] >= t) for t in THRESHOLDS]
        print(f"{label:<38}{top_pct:>4}%{top_id:>6}" + "".join(f"{c:>7}" for c in counts))
        report[label] = (top_pct, counts)

    print("\n🔎 อ่านผล:")
    self_pct = report[queries[0][0]][0]
    stranger_top = max(report[q[0]][0] for q in queries[2:5])
    tiny_pct = report[queries[5][0]][0]
    print(f"   • หน้าตัวเองตรงที่ {self_pct}% (เกณฑ์อยากได้ ≥ 70%)")
    print(f"   • หน้าแปลกปลอมสูงสุดแค่ {stranger_top}% (ยิ่งต่ำยิ่งดี — ควร < 40%)")
    print(f"   • หน้าเล็กจิวตรงที่ {tiny_pct}% ({'ใช้ได้ ✅' if tiny_pct >= 50 else 'จิ๋วเกินไป ❌'})")
    margin = self_pct - stranger_top
    print(f"   • ระยะห่าง 'ตัวเอง vs คนแปลก' = {margin} จุด → ยิ่งกว้างยิ่งตั้ง threshold สบาย")

    scale_test()

    print("\n💡 ข้อสรุปการจูน: ถ้าคนแปลกไม่เคยแตะ 40% และตัวเองไม่เคยต่ำกว่า 70%")
    print("   threshold 0.34-0.45 คือโซนปลอดภัย — จะเลือกค่าไหนขึ้นกับว่าอยาก 'ครบ' หรือ 'ชัวร์' กว่ากัน")


if __name__ == "__main__":
    main()
