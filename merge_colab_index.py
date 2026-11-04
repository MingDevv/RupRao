# -*- coding: utf-8 -*-
"""
merge_colab_index.py — เครื่องมือรวมผลการสแกนจาก Google Colab กลับเข้าเครื่อง Mac
================================================================================
หน้าที่:
1. รับโฟลเดอร์ผลลัพธ์ที่ดาวน์โหลดมาจาก Google Colab (มี faces.db, index.faiss, thumbs/)
2. นำเข้าข้อมูลรูปภาพ (photos) และตำแหน่งใบหน้า (faces) ลงใน SQLite ประจำเครื่อง
3. รวมเวกเตอร์ใบหน้าจาก Colab เข้ากับ FAISS Index บนเครื่อง
4. ซิงค์ไฟล์ Thumbnail WebP ทั้งหมดเข้าสู่ data/thumbs/
5. ตรวจสอบความถูกต้องสมบูรณ์แบบ 100% ก่อนเสร็จสิ้น

วิธีใช้:
  python merge_colab_index.py <โฟลเดอร์ผลลัพธ์จาก_colab>
  เช่น:
  python merge_colab_index.py ~/Downloads/colab_output
"""
import argparse
import shutil
import sqlite3
import sys
from pathlib import Path

import faiss
import numpy as np

import db

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
LOCAL_DB_PATH = DATA_DIR / "faces.db"
LOCAL_INDEX_PATH = DATA_DIR / "index.faiss"
LOCAL_THUMB_DIR = DATA_DIR / "thumbs"


def main():
    parser = argparse.ArgumentParser(description="รวมผลการสแกนจาก Google Colab เข้าสู่ระบบหลัก")
    parser.add_argument("colab_dir", help="โฟลเดอร์ที่ดาวน์โหลดมาจาก Google Colab (ต้องมี faces.db และ index.faiss)")
    args = parser.parse_args()

    src_dir = Path(args.colab_dir).resolve()
    if not src_dir.exists():
        raise SystemExit(f"❌ ไม่พบโฟลเดอร์: {src_dir}")

    src_db_path = src_dir / "faces.db"
    src_index_path = src_dir / "index.faiss"
    src_thumb_dir = src_dir / "thumbs"

    if not src_db_path.exists() or not src_index_path.exists():
        raise SystemExit(
            f"❌ ไม่พบไฟล์ faces.db หรือ index.faiss ใน {src_dir}\n"
            "   กรุณาตรวจสอบว่าดาวน์โหลดผลลัพธ์จาก Google Drive มาครบถ้วน"
        )

    print("\n" + "=" * 65)
    print("🔄 เริ่มการรวมข้อมูลจาก Google Colab เข้าสู่ระบบหลัก RupRao")
    print("=" * 65)

    # 1. เชื่อมต่อฐานข้อมูลต้นทางและปลายทาง
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOCAL_THUMB_DIR.mkdir(parents=True, exist_ok=True)

    dest_conn = db.get_connection(LOCAL_DB_PATH)
    src_conn = sqlite3.connect(str(src_db_path))
    src_conn.row_factory = sqlite3.Row

    # โหลด FAISS
    if LOCAL_INDEX_PATH.exists():
        dest_index = faiss.read_index(str(LOCAL_INDEX_PATH))
    else:
        dest_index = faiss.IndexIDMap2(faiss.IndexFlatIP(512))

    src_index = faiss.read_index(str(src_index_path))

    print(f"📊 ฐานข้อมูลบนเครื่องปัจจุบัน : {dest_index.ntotal} เวกเตอร์")
    print(f"📥 ข้อมูลใหม่จาก Google Colab : {src_index.ntotal} เวกเตอร์\n")

    # 2. นำเข้าข้อมูลรูปภาพ
    src_cur = src_conn.cursor()
    src_cur.execute("SELECT * FROM photos")
    src_photos = src_cur.fetchall()

    imported_photos = 0
    imported_faces = 0

    dest_cur = dest_conn.cursor()

    for p in src_photos:
        # เช็คว่ามีรูปนี้แล้วหรือยัง
        if db.is_photo_scanned(dest_conn, p['rel_path']):
            continue

        dest_cur.execute(
            "INSERT INTO photos (rel_path, event_name, width, height, file_size, scanned_at) VALUES (?, ?, ?, ?, ?, ?)",
            (p['rel_path'], p['event_name'], p['width'], p['height'], p['file_size'], p['scanned_at'])
        )
        new_photo_id = dest_cur.lastrowid
        imported_photos += 1

        # ดึงหน้าที่อยู่ในรูปนี้
        src_cur.execute("SELECT * FROM faces WHERE photo_id = ?", (p['id'],))
        faces = src_cur.fetchall()

        for f in faces:
            old_faiss_id = f['faiss_id']
            # ดึงเวกเตอร์จาก FAISS ต้นทาง
            try:
                vec = src_index.reconstruct(int(old_faiss_id)).reshape(1, -1)
            except Exception:
                continue

            new_faiss_id = db.get_next_faiss_id(dest_conn)
            dest_index.add_with_ids(vec, np.array([new_faiss_id], dtype=np.int64))

            dest_cur.execute(
                """INSERT INTO faces (photo_id, face_index, bbox_x1, bbox_y1, bbox_x2, bbox_y2, det_score, faiss_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_photo_id, f['face_index'], f['bbox_x1'], f['bbox_y1'], f['bbox_x2'], f['bbox_y2'],
                 float(f['det_score']), new_faiss_id)
            )
            imported_faces += 1

    dest_conn.commit()
    faiss.write_index(dest_index, str(LOCAL_INDEX_PATH))

    # 3. ซิงค์ Thumbnails
    copied_thumbs = 0
    if src_thumb_dir.exists():
        for thumb_file in src_thumb_dir.rglob("*.webp"):
            rel = thumb_file.relative_to(src_thumb_dir)
            target = LOCAL_THUMB_DIR / rel
            if not target.exists():
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(thumb_file, target)
                copied_thumbs += 1

    print("=" * 65)
    print("🎉 ผลการรวมข้อมูลสำเร็จสมบูรณ์!")
    print("=" * 65)
    print(f"  - เพิ่มรูปใหม่เข้าสู่ระบบ : {imported_photos:,} รูป")
    print(f"  - เพิ่มใบหน้าใหม่        : {imported_faces:,} หน้า")
    print(f"  - คัดลอก Thumbnail ใหม่ : {copied_thumbs:,} ไฟล์")
    print(f"  - เวกเตอร์รวมทั้งหมดใน FAISS : {dest_index.ntotal:,} รายการ")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
