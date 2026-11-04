# -*- coding: utf-8 -*-
"""
scan_faces.py — สแกนคลังรูปภาพงานทั้งหมด → บันทึกลง SQLite และ FAISS Index
=======================================================================
หัวใจของเฟส 3:
1. ค้นหารูปทั้งหมดในโฟลเดอร์ (รองรับโฟลเดอร์ย่อยแยกตามชื่องาน)
2. ข้ามรูปที่เคยสแกนแล้วอัตโนมัติ (ระบบ Resume: หยุดกลางทางแล้วรันต่อได้ทันที)
3. ตรวจจับใบหน้าและสกัดลายเซ็นเวกเตอร์ 512 มิติ (InsightFace ArcFace)
4. ทำ L2 Normalization เพื่อให้ Inner Product ของ FAISS ตรงกับ Cosine Similarity เป๊ะๆ
5. บันทึกลง:
   - SQLite (data/faces.db): เก็บตำแหน่งหน้า, ความมั่นใจ, และข้อมูลรูป
   - FAISS (data/index.faiss): ดัชนีเวกเตอร์สำหรับค้นหาความคล้ายคลึงระดับมิลลิวินาที
6. สร้าง Thumbnail WebP 640px ให้พร้อมสำหรับเว็บในเฟสต่อไป

วิธีใช้:
  python scan_faces.py                      -> สแกนรูปทั้งหมดใน data/input
  python scan_faces.py <โฟลเดอร์รูป>         -> สแกนรูปในโฟลเดอร์ที่ระบุ
"""
import argparse
import os
import signal
import sys
from pathlib import Path

import faiss
import numpy as np

import db
import face_lib
import make_thumbs

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DEFAULT_INPUT_DIR = DATA_DIR / "input"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"
THUMB_DIR = DATA_DIR / "thumbs"

VECTOR_DIM = 512
MIN_DET_SCORE = 0.45   # ตัดหน้าที่มีคะแนนตรวจจับต่ำเกินไปออกเพื่อความแม่นยำ


def load_or_create_index(index_path=INDEX_PATH):
    """โหลด FAISS Index เดิม หรือสร้าง IndexIDMap2(IndexFlatIP) ใหม่ถ้ายังไม่มี"""
    index_path = Path(index_path)
    if index_path.exists():
        print(f"📂 โหลด FAISS Index เดิมจาก {index_path}...")
        index = faiss.read_index(str(index_path))
        print(f"   (มีเวกเตอร์ใบหน้าเดิมอยู่ {index.ntotal} หน้า)\n")
        return index
    
    print("✨ สร้าง FAISS Index ใหม่ (Inner Product + IDMap)...")
    base_index = faiss.IndexFlatIP(VECTOR_DIM)
    index = faiss.IndexIDMap2(base_index)
    return index


def save_index(index, index_path=INDEX_PATH):
    """บันทึก FAISS Index ลงดิสก์"""
    index_path = Path(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    faiss.write_index(index, str(index_path))


def extract_event_name(file_path, base_dir):
    """
    แกะชื่องานจากชื่อโฟลเดอร์
    เช่น school-photos/2568-02-14_กีฬาสี/001.jpg -> '2568-02-14_กีฬาสี'
    """
    try:
        rel = file_path.relative_to(base_dir)
        if len(rel.parts) > 1:
            return rel.parts[0]
    except Exception:
        pass
    return "กิจกรรมทั่วไป"


def main():
    parser = argparse.ArgumentParser(description="สแกนใบหน้าลง SQLite + FAISS")
    parser.add_argument("folder", nargs="?", default=str(DEFAULT_INPUT_DIR),
                        help="โฟลเดอร์ที่เก็บรูปภาพ (ค่าเริ่มต้น: data/input)")
    args = parser.parse_args()

    source_dir = Path(args.folder).resolve()
    if not source_dir.exists():
        raise SystemExit(f"❌ ไม่พบโฟลเดอร์: {source_dir}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    # 1. เชื่อมต่อฐานข้อมูลและ FAISS
    conn = db.get_connection(DB_PATH)
    index = load_or_create_index(INDEX_PATH)

    # 2. หารูปภาพทั้งหมด
    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP"}
    all_files = sorted([p for p in source_dir.rglob("*") if p.suffix in valid_exts])

    if not all_files:
        raise SystemExit(f"⚠️ ไม่พบไฟล์รูปภาพใน {source_dir}")

    print(f"🔍 พบรูปภาพทั้งหมด {len(all_files)} รูปใน {source_dir}\n")

    # ตัวแปรติดตามงานและการดักจับ Ctrl+C (Graceful exit)
    scanned_photos = 0
    skipped_photos = 0
    new_faces = 0
    interrupted = False

    def handle_interrupt(sig, frame):
        nonlocal interrupted
        print("\n\n⚠️ ตรวจพบการยกเลิก (Ctrl+C)! กำลังบันทึกข้อมูลก่อนปิดระบบ...")
        interrupted = True

    signal.signal(signal.SIGINT, handle_interrupt)

    # 3. เริ่มสแกนทีละรูป
    for i, file_path in enumerate(all_files, start=1):
        if interrupted:
            break

        try:
            rel_path = file_path.relative_to(source_dir)
        except ValueError:
            rel_path = file_path.name

        # ตรวจสอบระบบ Resume: ถ้าเคยสแกนแล้วให้ข้ามทันที
        if db.is_photo_scanned(conn, str(rel_path)):
            skipped_photos += 1
            continue

        print(f"[{i}/{len(all_files)}] 📷 สแกน: {rel_path} ...", end=" ", flush=True)

        try:
            img = face_lib.load_image(file_path)
            h, w = img.shape[:2]
            file_size = file_path.stat().st_size
            event_name = extract_event_name(file_path, source_dir)

            # ตรวจจับใบหน้า
            faces = face_lib.detect_faces(img)
            
            # บันทึกรูปภาพลงตาราง photos
            photo_id = db.add_photo(conn, str(rel_path), event_name, w, h, file_size)

            # วนบันทึกแต่ละใบหน้าที่พบ
            valid_face_count = 0
            for idx, face in enumerate(faces):
                score = float(face.det_score)
                if score < MIN_DET_SCORE:
                    continue

                emb = np.asarray(face.embedding, dtype=np.float32)
                # L2 Normalize: เวกเตอร์ความยาว 1 เพื่อให้ Inner Product = Cosine Similarity
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                else:
                    continue

                faiss_id = db.get_next_faiss_id(conn)

                # เพิ่มลง FAISS Index
                emb_2d = emb.reshape(1, -1)
                index.add_with_ids(emb_2d, np.array([faiss_id], dtype=np.int64))

                # เพิ่มลง SQLite
                db.add_face(conn, photo_id, idx, face.bbox, score, faiss_id)
                valid_face_count += 1
                new_faces += 1

            # สร้าง Thumbnail ควบคู่กันไป
            thumb_path = (THUMB_DIR / rel_path).with_suffix(".webp")
            make_thumbs.create_thumbnail(file_path, thumb_path)

            scanned_photos += 1
            print(f"เจอ {valid_face_count} หน้า ✅")

            # Commit เป็นระยะทุกๆ 10 รูป เพื่อป้องกันข้อมูลสูญหาย
            if scanned_photos % 10 == 0:
                conn.commit()
                save_index(index, INDEX_PATH)

        except Exception as e:
            print(f"❌ ขัดข้อง: {e}")

    # 4. บันทึกผลลัพธ์ทั้งหมด
    conn.commit()
    save_index(index, INDEX_PATH)

    stats = db.get_stats(conn)
    print("\n" + "=" * 50)
    print("📊 สรุปผลการสแกน (Phase 3)")
    print("=" * 50)
    print(f"  - รูปที่สแกนใหม่รอบนี้ : {scanned_photos} รูป")
    print(f"  - รูปที่ข้าม (สแกนแล้ว) : {skipped_photos} รูป")
    print(f"  - หน้าที่พบใหม่รอบนี้   : {new_faces} หน้า")
    print(f"  - รวมทั้งหมดในระบบ    : {stats['photos']} รูป | {stats['faces']} หน้า")
    print(f"  - เวกเตอร์ใน FAISS   : {index.ntotal} รายการ")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    main()
