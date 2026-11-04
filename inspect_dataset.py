# -*- coding: utf-8 -*-
"""
inspect_dataset.py — เครื่องมือตรวจสอบคุณภาพข้อมูลและสุ่มตรวจคลังรูปภาพ (Data Quality Auditor)
==========================================================================================
หน้าที่:
1. วิเคราะห์สถิติคลังภาพ: จำนวนรูป, จำนวนใบหน้า, สถิติแยกตามกิจกรรม
2. ตรวจสอบความถูกต้องของเวกเตอร์ใน FAISS ให้ตรงกับตาราง faces ใน SQLite 1:1
3. สุ่มตรวจคุณภาพ (Audit Sampling) 20 รูป:
   - ตรวจสอบความสมบูรณ์ของพิกัดกรอบใบหน้า (Bounding Box Integrity)
   - ตรวจสอบว่ามีไฟล์ Thumbnail WebP ครบถ้วนหรือไม่
   - วาดภาพตัวอย่างพร้อมกรอบหน้าบันทึกลง data/output/audit_samples/ เพื่อให้ตรวจดูด้วยตาเปล่าได้

วิธีใช้:
  python inspect_dataset.py
  python inspect_dataset.py --sample 20 --visual
"""
import argparse
import random
import sys
from pathlib import Path

import cv2
import faiss

import db

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"
THUMB_DIR = DATA_DIR / "thumbs"
INPUT_DIR = DATA_DIR / "input"
AUDIT_DIR = DATA_DIR / "output" / "audit_samples"


def find_photo_file(rel_path):
    """หาไฟล์ภาพจริงใน input/ หรือ data/"""
    candidates = [
        INPUT_DIR / rel_path,
        DATA_DIR / rel_path,
        BASE / rel_path,
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


def main():
    parser = argparse.ArgumentParser(description="ตรวจสอบคุณภาพคลังภาพ RupRao")
    parser.add_argument("--sample", type=int, default=20, help="จำนวนภาพที่ต้องการสุ่มตรวจ (default: 20)")
    parser.add_argument("--visual", action="store_true", default=True,
                        help="บันทึกภาพตัวอย่างพร้อมกรอบหน้าลงโฟลเดอร์ audit_samples")
    args = parser.parse_args()

    if not DB_PATH.exists() or not INDEX_PATH.exists():
        raise SystemExit("❌ ไม่พบฐานข้อมูล กรุณารัน scan_faces.py ก่อนตรวจสอบ")

    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()

    # 1. ข้อมูลสถิติภาพรวม
    stats = db.get_stats(conn)
    index = faiss.read_index(str(INDEX_PATH))

    print("\n" + "=" * 65)
    print("📊 รายงานการตรวจสอบคุณภาพข้อมูล RupRao (Dataset Quality Report)")
    print("=" * 65)
    print(f"  📁 จำนวนรูปภาพทั้งหมดในฐานข้อมูล : {stats['photos']:,} รูป")
    print(f"  👤 จำนวนใบหน้าที่ตรวจพบทั้งหมด  : {stats['faces']:,} หน้า")
    print(f"  ⚡ จำนวนเวกเตอร์ใน FAISS Index  : {index.ntotal:,} รายการ")

    # ตรวจสอบความสอดคล้อง 1:1
    if stats['faces'] == index.ntotal:
        print("  ✅ ความสอดคล้องข้อมูล: เวกเตอร์ใน FAISS ตรงกับ SQLite แบบ 1:1 สมบูรณ์")
    else:
        print(f"  ⚠️ คำเตือน: จำนวนหน้าใน SQLite ({stats['faces']}) ไม่ตรงกับ FAISS ({index.ntotal})")

    # 2. สถิติแยกตามกิจกรรม
    print("\n🏷️ รายการภาพแยกตามกิจกรรม:")
    cur.execute("""
        SELECT event_name, COUNT(DISTINCT p.id) as photo_count, COUNT(f.id) as face_count
        FROM photos p
        LEFT JOIN faces f ON p.id = f.photo_id
        GROUP BY event_name
        ORDER BY photo_count DESC
    """)
    events = cur.fetchall()
    for ev in events:
        avg_face = (ev['face_count'] / ev['photo_count']) if ev['photo_count'] > 0 else 0
        print(f"   • {ev['event_name']:<25} : {ev['photo_count']:>4} รูป | {ev['face_count']:>5} หน้า (เฉลี่ย {avg_face:.1f} หน้า/รูป)")

    # 3. สุ่มตรวจคุณภาพ (Audit Sampling)
    sample_size = min(args.sample, stats['photos'])
    if sample_size == 0:
        print("\n⚠️ ยังไม่มีรูปภาพในระบบ")
        return

    print(f"\n🔬 กำลังสุ่มตรวจคุณภาพ {sample_size} รูป...")
    cur.execute("SELECT * FROM photos ORDER BY RANDOM() LIMIT ?", (sample_size,))
    sample_photos = cur.fetchall()

    passed_checks = 0
    missing_files = 0
    missing_thumbs = 0
    box_anomalies = 0
    saved_visuals = []

    if args.visual:
        AUDIT_DIR.mkdir(parents=True, exist_ok=True)

    for i, p in enumerate(sample_photos, 1):
        rel = p['rel_path']
        real_file = find_photo_file(rel)
        if not real_file:
            missing_files += 1
            print(f"   [{i:02d}] ❌ ไม่พบไฟล์ต้นฉบับ: {rel}")
            continue

        # ตรวจสอบ Thumbnail
        thumb_path = (THUMB_DIR / rel).with_suffix(".webp")
        if not thumb_path.exists():
            missing_thumbs += 1

        # ดึงหน้าที่อยู่ในรูปนี้
        cur.execute("SELECT * FROM faces WHERE photo_id = ?", (p['id'],))
        faces = cur.fetchall()

        # ตรวจสอบพิกัด Bounding Box
        img = cv2.imread(str(real_file))
        img_h, img_w = (img.shape[:2]) if img is not None else (p['height'], p['width'])

        has_box_error = False
        for f in faces:
            x1, y1, x2, y2 = f['bbox_x1'], f['bbox_y1'], f['bbox_x2'], f['bbox_y2']
            if x1 < 0 or y1 < 0 or x2 > img_w or y2 > img_h or x2 <= x1 or y2 <= y1:
                has_box_error = True
                box_anomalies += 1

            if args.visual and img is not None:
                cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 2)
                cv2.putText(img, f"{f['det_score']:.2f}", (x1, max(y1 - 6, 18)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        if args.visual and img is not None:
            out_file = AUDIT_DIR / f"audit_{Path(rel).stem}.jpg"
            cv2.imwrite(str(out_file), img)
            saved_visuals.append(out_file)

        if not has_box_error:
            passed_checks += 1

    print("\n" + "=" * 65)
    print("📋 สรุปผลการสุ่มตรวจ (Audit Findings)")
    print("=" * 65)
    print(f"  - รูปที่สุ่มตรวจทั้งหมด : {sample_size} รูป")
    print(f"  - ตรวจผ่านสมบูรณ์      : {passed_checks}/{sample_size} รูป ({passed_checks / sample_size * 100:.1f}%)")
    print(f"  - ไฟล์ต้นฉบับสูญหาย    : {missing_files} รูป")
    print(f"  - Thumbnail ไม่พบ     : {missing_thumbs} รูป")
    print(f"  - กรอบพิกัดผิดปกติ     : {box_anomalies} จุด")

    if saved_visuals:
        print(f"\n🖼️ บันทึกภาพตัวอย่างสุ่มตรวจพร้อมตีกรอบ AI ไว้ที่:")
        print(f"   -> {AUDIT_DIR} ({len(saved_visuals)} ภาพ)")

    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
