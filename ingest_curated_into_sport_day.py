# -*- coding: utf-8 -*-
"""
ingest_curated_into_sport_day.py
นำเข้ารูปภาพคัดแล้วจากทั้ง 2 งาน (แข่งวันที่ 1 และ เดินขบวน)
เข้าสู่อัลบั้มและโฟลเดอร์ 'กีฬาสี' โดยตรงตามความต้องการของผู้ใช้งาน
"""
import os
import sys
import time
from pathlib import Path

import cv2
import faiss
import numpy as np
from PIL import Image, ImageOps

import db
import face_lib
import make_thumbs

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
RAW_DIR = DATA_DIR / "raw" / "กีฬาสี"
THUMB_DIR = DATA_DIR / "thumbs" / "กีฬาสี"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"

GDRIVE_BASE = Path(os.path.expanduser("~/Library/CloudStorage/GoogleDrive-thawinming@gmail.com/ไดรฟ์ของฉัน"))
DAY1_DIR = GDRIVE_BASE / "68-12-01 บรรยากาศการแข่งกีฬาสี วันที่ 1" / "คัดแล้ว"
PARADE_DIR = GDRIVE_BASE / "68-12-03 เดินขบวนกีฬาสี 68" / "คัด"

VALID_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG"}


def main():
    print("=" * 65)
    print("🏆 เริ่มต้นการนำเข้ารูป 'คัดแล้ว' เข้าสู่อัลบั้ม 'กีฬาสี'")
    print("=" * 65)

    RAW_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    # 1. รวบรวมรายการไฟล์เป้าหมายที่จะนำเข้า
    items_to_process = []

    # 1.1 วันแข่งวันที่ 1 (133 รูป)
    if DAY1_DIR.exists():
        day1_files = sorted([f for f in DAY1_DIR.iterdir() if f.is_file() and f.suffix in VALID_EXTS])
        print(f"📂 พบรูปคัดแล้ว (วันแข่ง): {len(day1_files)} รูป")
        for f in day1_files:
            # ป้องกันชื่อชนกับเดินขบวน
            if f.name in {"01.JPG", "02.JPG", "03.JPG", "04.JPG"}:
                target_name = f"วันแข่ง_{f.name}"
            else:
                target_name = f.name
            items_to_process.append((f, target_name, "วันแข่ง"))
    else:
        print(f"⚠️ ไม่พบโฟลเดอร์: {DAY1_DIR}")

    # 1.2 เดินขบวนกีฬาสี (91 รูป)
    if PARADE_DIR.exists():
        parade_files = sorted([f for f in PARADE_DIR.iterdir() if f.is_file() and f.suffix in VALID_EXTS])
        print(f"📂 พบรูปคัดแล้ว (เดินขบวน): {len(parade_files)} รูป")
        for f in parade_files:
            target_name = f"ขบวน_{f.name}"
            items_to_process.append((f, target_name, "เดินขบวน"))
    else:
        print(f"⚠️ ไม่พบโฟลเดอร์: {PARADE_DIR}")

    total_items = len(items_to_process)
    print(f"\n📊 รูปคัดแล้วทั้งหมดที่จะนำเข้า: {total_items} รูป")
    if total_items == 0:
        print("❌ ไม่มีรูปที่ต้องนำเข้า จบการทำงาน")
        return

    # 2. เชื่อมต่อฐานข้อมูลและ FAISS Index
    conn = db.get_connection(DB_PATH)
    if INDEX_PATH.exists():
        index = faiss.read_index(str(INDEX_PATH))
    else:
        index = faiss.IndexIDMap2(faiss.IndexFlatIP(512))

    print(f"📈 เวกเตอร์เดิมในระบบ: {index.ntotal:,} รายการ")

    # 3. เริ่มการสแกนและนำเข้า
    scanned_count = 0
    new_faces_count = 0
    start_time = time.time()

    for idx_item, (src_file, target_name, source_tag) in enumerate(items_to_process, 1):
        target_raw = RAW_DIR / target_name
        rel_path = f"กีฬาสี/{target_name}"

        # 3.1 สร้าง Symlink ใน data/raw/กีฬาสี/ (ใช้ 0 byte บนดิสก์)
        if not target_raw.exists():
            try:
                target_raw.symlink_to(src_file)
            except Exception as sym_err:
                # fallback เป็น copy ถ้า symlink ล้มเหลว
                import shutil
                shutil.copy2(src_file, target_raw)

        # 3.2 ตรวจสอบว่ารูปนี้เคยสแกนแล้วหรือไม่
        if db.is_photo_scanned(conn, rel_path):
            continue

        # 3.3 เปิดอ่านรูปภาพ
        try:
            with Image.open(target_raw) as pil_img:
                pil_img = ImageOps.exif_transpose(pil_img)
                w, h = pil_img.size
                img_bgr = cv2.cvtColor(np.array(pil_img.convert("RGB")), cv2.COLOR_RGB2BGR)

            file_size = src_file.stat().st_size

            # 3.4 สร้างภาพย่อ Thumbnail WebP
            thumb_path = (THUMB_DIR / target_name).with_suffix(".webp")
            if not thumb_path.exists():
                make_thumbs.create_thumbnail(target_raw, thumb_path)

            # 3.5 ตรวจจับใบหน้าด้วย AI
            faces = face_lib.detect_faces(img_bgr)
            added_fids = []

            photo_id = db.add_photo(conn, rel_path, "กีฬาสี", w, h, file_size)

            for f_idx, face in enumerate(faces):
                if float(face.det_score) < 0.45:
                    continue
                emb = np.asarray(face.embedding, dtype=np.float32)
                norm = np.linalg.norm(emb)
                if norm <= 0:
                    continue
                emb = (emb / norm).reshape(1, -1)

                faiss_id = db.get_next_faiss_id(conn)
                index.add_with_ids(emb, np.array([faiss_id], dtype=np.int64))
                added_fids.append(faiss_id)

                db.add_face(conn, photo_id, f_idx, face.bbox, float(face.det_score), faiss_id)
                new_faces_count += 1

            conn.commit()
            scanned_count += 1

            # รายงานความคืบหน้าทุก 10 รูป
            if idx_item % 10 == 0 or idx_item == total_items:
                pct = int((idx_item / total_items) * 100)
                elapsed = time.time() - start_time
                print(f"[{pct}%] นำเข้าแล้ว {idx_item}/{total_items} รูป | พบสะสม {new_faces_count} ใบหน้า ({elapsed:.1f}s)")

        except Exception as e:
            print(f"⚠️ ข้ามรูป {target_name}: {e}")
            conn.rollback()

    # 4. บันทึก FAISS Index รอบสุดท้าย
    if new_faces_count > 0:
        tmp_idx = INDEX_PATH.with_suffix(".tmp")
        faiss.write_index(index, str(tmp_idx))
        os.replace(str(tmp_idx), str(INDEX_PATH))

    total_time = time.time() - start_time
    print("\n" + "=" * 65)
    print("✨ รวมรูปคัดแล้วเข้าสู่อัลบั้ม 'กีฬาสี' สำเร็จเรียบร้อย!")
    print("=" * 65)
    print(f"  • รูปภาพที่นำเข้าใหม่: {scanned_count} รูป")
    print(f"  • ใบหน้าที่ตรวจพบใหม่: {new_faces_count} หน้า")
    print(f"  • เวกเตอร์รวมใน FAISS ตอนนี้: {index.ntotal:,} รายการ")
    print(f"  • ใช้เวลารวมทั้งหมด: {total_time:.1f} วินาที")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
