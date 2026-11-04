# -*- coding: utf-8 -*-
"""
delete_photos.py — ลบรูปภาพและใบหน้าออกจากระบบ RupRao (SQLite + FAISS + Files)
"""
import sys
from pathlib import Path
import faiss
import numpy as np
import db

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"
THUMB_DIR = DATA_DIR / "thumbs"
INPUT_DIR = DATA_DIR / "input"


def delete_by_rel_paths(rel_paths):
    conn = db.get_connection(DB_PATH)
    if not INDEX_PATH.exists():
        print("❌ ไม่พบไฟล์ FAISS index")
        return

    index = faiss.read_index(str(INDEX_PATH))
    print(f"📊 จำนวนเวกเตอร์ใน FAISS ก่อนลบ: {index.ntotal}")

    cur = conn.cursor()
    placeholders = ",".join("?" for _ in rel_paths)
    
    # 1. ค้นหา faiss_ids ของหน้าที่อยู่ในรูปเหล่านี้
    cur.execute(f"""
        SELECT f.faiss_id, p.id, p.rel_path 
        FROM faces f 
        JOIN photos p ON f.photo_id = p.id 
        WHERE p.rel_path IN ({placeholders})
    """, rel_paths)
    rows = cur.fetchall()
    
    faiss_ids = [r["faiss_id"] for r in rows]
    photo_ids = list(set(r["id"] for r in rows))
    
    print(f"🎯 พบรูปภาพที่ตรงกัน: {len(photo_ids)} รูป")
    print(f"🎯 พบใบหน้าที่เกี่ยวข้อง: {len(faiss_ids)} หน้า (FAISS IDs: {faiss_ids})")

    # 2. ลบออกจาก FAISS Index
    if faiss_ids:
        ids_array = np.array(faiss_ids, dtype=np.int64)
        index.remove_ids(ids_array)
        faiss.write_index(index, str(INDEX_PATH))
        print(f"✅ ลบเวกเตอร์ออกจาก FAISS สำเร็จ (เหลือ {index.ntotal} หน้า)")

    # 3. ลบออกจาก SQLite (CASCADE จะลบ faces และ reports ให้ด้วย)
    if photo_ids:
        cur.execute(f"DELETE FROM photos WHERE id IN ({','.join('?' for _ in photo_ids)})", photo_ids)
        conn.commit()
        print(f"✅ ลบข้อมูลรูปภาพออกจาก SQLite สำเร็จ")

    # 4. ลบไฟล์ภาพต้นฉบับและ Thumbnail
    for rel_path in rel_paths:
        # เช็คใน data/input
        f1 = INPUT_DIR / rel_path
        if f1.exists():
            f1.unlink()
            print(f"🗑️ ลบไฟล์: {f1}")
        
        # เช็ค thumbnail
        thumb = (THUMB_DIR / rel_path).with_suffix(".webp")
        if thumb.exists():
            thumb.unlink()
            print(f"🗑️ ลบ Thumbnail: {thumb}")

    print("\n✨ ลบรูปภาพและใบหน้าเรียบร้อยแล้ว!")


if __name__ == "__main__":
    targets = sys.argv[1:]
    if not targets:
        raise SystemExit(
            "⚠️  ต้องระบุชื่อไฟล์ที่ต้องการลบ เช่น:\n"
            "    python delete_photos.py CRMS6_Students/123456.jpg\n"
            "    python delete_photos.py d.jpeg ming.jpeg\n"
            "(ไม่ระบุ = ไม่ลบอะไรเลย กันมือลั่ว)"
        )
    delete_by_rel_paths(targets)
