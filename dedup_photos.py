# -*- coding: utf-8 -*-
"""
dedup_photos.py — หาและลบ "รูปซ้ำ" ออกจากคลัง RupRao
====================================================
เช็คซ้ำ 2 ชั้น:
  1) Facebook ID เดียวกัน (ชื่อไฟล์) ข้ามโฟลเดอร์อัลบั้ม
  2) เนื้อไฟล์เหมือนกันเป๊ะ (MD5 ของไบต์จริง)
เกณฑ์การเก็บ: อัลบั้มหลัก (KEEP_PRIORITY) มีสิทธิ์ถูกเก็บไว้ก่อนเสมอ
โฟลเดอร์ทดสอบ/ชั่วคราวถูกส่งออกนอก (ลบทิ้ง) เมื่อซ้ำกัน

ตัวลบใช้กลไกเดียวกับ delete_photos.py = ลบครบ 3 ชั้น
(FAISS + SQLite + ไฟล์/thumbnail) ไม่ต้องกลัวข้อมูลเหลือค้าง

วิธีใช้:
  python dedup_photos.py            -> โหมดดูอย่างเดียว (dry-run) ปลอดภัย
  python dedup_photos.py --apply    -> ลบจริง
"""
import hashlib
import sys
from pathlib import Path

import db

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "raw"
KEEP_PRIORITY = ["CRMS6_Students"]   # อัลบั้มหลัก — ชนะทุกคนเวลาซ้ำกัน


def album_priority(rel_path: str) -> int:
    """ยิ่งเลขน้อย = ยิ่งมีสิทธิ์ถูกเก็บไว้ (อัลบั้มหลักมาก่อน โฟลเดอร์ทดสอบอยู่ท้ายสุด)"""
    for i, album in enumerate(KEEP_PRIORITY):
        if rel_path.startswith(album + "/"):
            return i
    low = rel_path.lower()
    if "test" in low:
        return 900   # โฟลเดอร์ทดสอบ = ตัวเลือกแรกที่จะถูกลบ
    return 500       # อัลบั้มอื่นๆ


def find_duplicates(rel_paths):
    """คืน list ของ rel_path ที่ควรลบ (ตัวซ้ำ) — ตัวที่ถูกเก็บไว้ไม่อยู่ใน list"""
    rel_set = set(rel_paths)

    # ชั้น 1: ซ้ำด้วยชื่อไฟล์ (Facebook ID) ข้ามอัลบั้ม
    by_stem = {}
    for rel in rel_paths:
        by_stem.setdefault(Path(rel).stem, []).append(rel)

    # ชั้น 2: ซ้ำด้วยเนื้อไฟล์จริง (MD5) — จับโพสต์ซ้ำที่คนละ ID แต่รูปเดียวกัน
    by_hash = {}
    for rel in rel_paths:
        f = RAW / rel
        if f.exists():
            h = hashlib.md5(f.read_bytes()).hexdigest()
            by_hash.setdefault(h, []).append(rel)

    to_remove = set()
    for groups in (by_stem, by_hash):
        for members in groups.values():
            members = sorted({m for m in members if m in rel_set}, key=album_priority)
            if len(members) > 1:
                to_remove.update(members[1:])   # เก็บตัวที่มีสิทธิ์สูงสุดไว้ ลบที่เหลือ
    return sorted(to_remove)


def main():
    conn = db.get_connection(BASE / "data" / "faces.db")
    rows = conn.execute("SELECT rel_path FROM photos ORDER BY id").fetchall()
    conn.close()
    rel_paths = [r["rel_path"] for r in rows]
    print(f"📦 รูปในคลังทั้งหมด: {len(rel_paths)} รูป")

    dupes = find_duplicates(rel_paths)
    if not dupes:
        print("✨ ไม่พบรูปซ้ำเลย คลังสะอาดอยู่แล้ว!")
        return

    print(f"🔍 พบรูปซ้ำ {len(dupes)} รูป ที่ควรลบ (เก็บตัวในอัลบั้มหลักไว้):")
    for d in dupes:
        print("   🗑️", d)

    if "--apply" not in sys.argv:
        print("\n(โหมดดูอย่างเดียว — ใส่ --apply ท้ายคำสั่งเพื่อลบจริง)")
        return

    print("\n🗑️ เริ่มลบจริง...")
    from delete_photos import delete_by_rel_paths   # ใช้กลไกลบ 3 ชั้นตัวเดิมของโปรเจกต์
    delete_by_rel_paths(dupes)


if __name__ == "__main__":
    main()
