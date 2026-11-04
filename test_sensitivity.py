# -*- coding: utf-8 -*-
"""
test_sensitivity.py — ห้องแล็บทดสอบ "ความ sensitive" ของระบบค้นหาหน้า
=====================================================================
ทดสอบ 3 เรื่อง:
  1) หน้าตัวเอง/รูปเพจ  -> ต้องตรงสูงๆ     (true positive)
  2) หน้าเก๊/หน้าคนอื่น  -> ต้องต่ำ ห้ามหลอก (false positive = 0)
  3) หน้าเล็กจิว        -> เล็กแค่ไหนยังจับ/ค้นได้
พร้อมโชว์ว่า threshold แต่ละค่า (0.30-0.50) เปลี่ยนจำนวนผลลัพธ์ยังไง

ยืดหยุ่น: ใช้เฉพาะไฟล์ที่มีอยู่จริงใน data/input — ไฟล์ไหนหายไปข้ามอัตโนมัติ
(อยากทดสอบหน้าเพื่อน แค่เอารูปใส่ data/input แล้วรันใหม่ หรือส่งชื่อไฟล์ท้ายคำสั่ง)

วิธีใช้: เปิด server.py ค้างไว้ แล้วรัน  python test_sensitivity.py [ชื่อไฟล์เพิ่มเติม...]
"""
import sys
from pathlib import Path

import cv2
import requests

BASE = Path(__file__).resolve().parent
INPUT = BASE / "data" / "input"
API = "http://localhost:8000"
THRESHOLDS = [0.30, 0.34, 0.40, 0.45, 0.50]   # ระดับที่อยากรู้ว่า "ถ้าตัดตรงนี้จะได้กี่รูป"


def search_file(img_path, threshold=0.30):
    """ส่งรูปเข้า /api/search ของเซิร์ฟเวอร์จริง (threshold ต่ำสุด = ได้คะแนนดิบมากที่สุด)
    คืน list ผลลัพธ์ / None ถ้าเซิร์ฟเวอร์มองไม่เห็นหน้าในรูป (422)"""
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
    if r.status_code == 422:
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
    """ทดสอบ local: ย่อ d.jpeg เหลือ 50%/25%/12% แล้วดูว่ายังจับหน้าได้กี่หน้า"""
    if not (INPUT / "d.jpeg").exists():
        print("\n🔬 (ข้ามทดสอบย่อรูป — ไม่มีไฟล์ d.jpeg ต้นทางแล้ว)")
        return
    import face_lib
    img = cv2.imread(str(INPUT / "d.jpeg"))
    h, w = img.shape[:2]
    print("\n🔬 ทดสอบการจับหน้าเมื่อรูปเล็กลง")
    for factor in (0.5, 0.25, 0.12):
        small = cv2.resize(img, (int(w * factor), int(h * factor)))
        p = INPUT / f"_scale_{int(factor * 100)}.jpg"
        cv2.imwrite(str(p), small)
        faces = face_lib.detect_faces(small)
        best = max((float(f.det_score) for f in faces), default=0)
        print(f"   ย่อเหลือ {int(factor * 100)}% ({small.shape[1]}px): เจอ {len(faces)} หน้า "
              f"(คะแนนสูงสุด {best:.2f})")


def build_queries(extra):
    """ประกอบชุด query จากไฟล์ที่มีอยู่จริงเท่านั้น — คืน list ของ (label, path, kind)"""
    queries = []
    for name, label, kind in [
        ("ming.jpeg", "หน้าตัวเอง", "self"),
        ("d.jpeg", "รูปงานส่วนตัว", "self"),
        ("_a.jpg", "หน้าเก๊ 1", "stranger"),
        ("_b.jpg", "หน้าเก๊ 2", "stranger"),
        ("_c.jpg", "หน้าเก๊ 3", "stranger"),
    ]:
        p = INPUT / name
        if p.exists():
            queries.append((f"{name} = {label}", p, kind))

    # หน้าเล็กจิว (ต้องมี d.jpeg ต้นทาง)
    tiny = None
    if (INPUT / "d.jpeg").exists():
        tiny = make_tiny_face_crop()
        queries.append(("_tiny_face = หน้าเล็กจิวจากพื้นหลัง", tiny, "tiny"))

    # ถ้าไม่มีรูปส่วนตัว/หน้าเก๊เลย ใช้รูปเพจ 3 รูปแรกทดสอบ "ตรงตัว" แทน
    # (รูปจากเพจเก็บที่ data/raw/CRMS6_Students — server ใช้ find_photo_path หาหลายที่)
    if not any(kind in ("self", "stranger") for _, _, kind in queries):
        page = []
        for cand in (INPUT / "CRMS6_Students", BASE / "data" / "raw" / "CRMS6_Students"):
            if cand.is_dir():
                page = sorted(cand.glob("*.jpg"))[:3]
                if page:
                    break
        for p in page:
            queries.append((f"รูปเพจ: {p.name} = ทดสอบตรงตัว", p, "page"))

    # ไฟล์เพิ่มเติมจากผู้ใช้ (เช่น หน้าเก๊ หน้าน้อย) — ส่งชื่อไฟล์ท้ายคำสั่งได้
    for name in extra:
        p = INPUT / name
        if p.exists():
            queries.append((f"{name} = ทดสอบเพิ่มเติม", p, "extra"))
        else:
            print(f"⚠️  ไม่พบไฟล์ {name} (ข้าม)")
    return queries, tiny


def main():
    r = requests.get(f"{API}/api/health", timeout=5)
    print(f"🩺 เซิร์ฟเวอร์: {r.json()['status']} | รูป {r.json()['photos']} | หน้า {r.json()['faces']}")

    queries, tiny = build_queries(sys.argv[1:])
    if tiny:
        print(f"✂️  สร้างหน้าเล็กจิวแล้ว: {tiny.name}")
    print(f"🎯 มี {len(queries)} query ที่จะทดสอบ\n")

    header = f"{'query':<52}{'top%':>5}{'topID':>7}" + "".join(f"@{t:.2f}".rjust(7) for t in THRESHOLDS)
    print(header)
    print("-" * len(header))

    report = []   # (label, kind, top_pct)
    for label, path, kind in queries:
        results = search_file(path)                       # ขอครั้งเดียวที่ 0.30 = คะแนนดิบ
        if results is None:
            print(f"{label:<52}{'—':>5}{'>':>7}   ← เซิร์ฟเวอร์มองไม่เห็นหน้าใน query นี้")
            continue
        top = results[0] if results else None
        top_pct = top["similarity_percent"] if top else 0
        top_id = f"#{top['photo_id']}" if top else "-"
        counts = [sum(1 for x in results if x["similarity"] >= t) for t in THRESHOLDS]
        print(f"{label:<52}{top_pct:>4}%{top_id:>7}" + "".join(f"{c:>7}" for c in counts))
        report.append((label, kind, top_pct))

    print("\n🔎 อ่านผล:")
    selfs = [pct for _, kind, pct in report if kind in ("self", "page")]
    strans = [pct for _, kind, pct in report if kind == "stranger"]
    tinys = [pct for _, kind, pct in report if kind == "tiny"]
    if selfs:
        print(f"   • หน้าตัวเอง/รูปเพจตรงตัว: สูงสุด {max(selfs)}% (ควร ≥ 70%)")
    if strans:
        print(f"   • หน้าเก๊/คนแปลก: สูงสุด {max(strans)}% (ยิ่งต่ำยิ่งดี — ควร < 40%)")
    if tinys:
        print(f"   • หน้าเล็กจิว: {max(tinys)}% ({'ใช้ได้ ✅' if max(tinys) >= 50 else 'จิ๋วเกินไป ❌'})")
    if selfs and strans:
        margin = max(selfs) - max(strans)
        print(f"   • ระยะห่าง 'ตัวเอง vs คนเก๊' = {margin} จุด → ยิ่งกว้างยิ่งตั้ง threshold สบาย")

    scale_test()

    print("\n💡 ข้อสรุปการจูน: ถ้าหน้าเก๊ไม่เคยแตะ 40% และหน้าจริงไม่เคยต่ำกว่า 70%")
    print("   threshold 0.34-0.45 คือโซนปลอดภัย — เลือกค่าไหนขึ้นกับว่าอยาก 'ครบ' หรือ 'ชัวร์' กว่ากัน")


if __name__ == "__main__":
    main()
