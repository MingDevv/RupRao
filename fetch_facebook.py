# -*- coding: utf-8 -*-
"""
fetch_facebook.py — สคริปต์ดูดรูปกิจกรรมจากเพจ Facebook เข้าสู่ระบบ RupRao อัตโนมัติ
=============================================================================
การใช้งาน:
  python fetch_facebook.py <facebook_url> [--limit จำนวนรูป] [--album ชื่องาน] [--scan]

ตัวอย่าง:
  # ดูด 30 รูปล่าสุดจากเพจ CRMS6 พร้อมสแกนใบหน้าทันที:
  python fetch_facebook.py https://www.facebook.com/crms6officialpage --limit 30 --album "กิจกรรม_CRMS6" --scan

  # ดูดอัลบั้มเฉพาะเจาะจง:
  python fetch_facebook.py https://www.facebook.com/media/set/?set=a.xxxx --album "กีฬาสี_2567" --scan
"""
import argparse
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_RAW = BASE / "data" / "raw"
GALLERY_DL = BASE / "venv" / "bin" / "gallery-dl"
PYTHON_BIN = BASE / "venv" / "bin" / "python"


def main():
    parser = argparse.ArgumentParser(description="ดูดรูปภาพจากเพจ Facebook โรงเรียนเข้าสู่ RupRao")
    parser.add_argument("url", help="URL ของเพจ Facebook หรือ อัลบั้มรูป")
    parser.add_argument("--limit", type=int, default=50, help="จำนวนรูปสูงสุดที่ต้องการดึง (ค่าเริ่มต้น: 50 รูป)")
    parser.add_argument("--album", type=str, default="Facebook_Photos", help="ชื่องาน/โฟลเดอร์สำหรับเก็บรูป")
    parser.add_argument("--scan", action="store_true", help="สั่งสแกนใบหน้าเข้าระบบทันทีหลังจากดาวน์โหลดเสร็จ")

    args = parser.parse_args()

    # จัดการ URL: ถ้าเป็นหน้าเพจหลัก ให้ชี้ไปที่ /photos เพื่อดึงภาพกิจกรรม
    target_url = args.url.strip()
    if not any(k in target_url for k in ["/photos", "/media", "/albums"]):
        target_url = target_url.rstrip("/") + "/photos"

    out_dir = DATA_RAW / args.album
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 65)
    print("📥 RupRao Facebook Photo Downloader")
    print("=" * 65)
    print(f"🌐 แหล่งที่มา : {target_url}")
    print(f"📁 โฟลเดอร์ปลายทาง : {out_dir}")
    print(f"🎯 จำนวนสูงสุด : {args.limit} รูป")
    print("=" * 65)

    if not GALLERY_DL.exists():
        print("❌ ไม่พบ gallery-dl ใน venv กำลังติดตั้ง...")
        subprocess.run([str(PYTHON_BIN), "-m", "pip", "install", "gallery-dl"], check=True)

    cmd = [
        str(GALLERY_DL),
        "--range", f"1-{args.limit}",
        "--directory", str(out_dir),
        "--no-mtime",
        target_url
    ]

    print("⏳ กำลังเชื่อมต่อ Facebook และเริ่มดาวน์โหลดรูปภาพความละเอียดสูง...")
    try:
        proc = subprocess.run(cmd, check=True)
        print("\n✅ ดาวน์โหลดรูปภาพจาก Facebook เรียบร้อยแล้ว!")
    except subprocess.CalledProcessError as e:
        print(f"\n⚠️ เกิดข้อผิดพลาดขณะดาวน์โหลด: {e}")
        return

    # นับจำนวนรูปที่โหลดมาได้
    images = [p for p in out_dir.glob("*") if p.suffix.lower() in [".jpg", ".jpeg", ".png", ".webp"]]
    print(f"📸 พบรูปในโฟลเดอร์ {args.album} ทั้งหมด {len(images)} รูป")

    if args.scan:
        print("\n🧠 กำลังเริ่มสแกนใบหน้าเข้าสู่ฐานข้อมูล RupRao ทันที...")
        scan_script = BASE / "scan_faces.py"
        subprocess.run([str(PYTHON_BIN), str(scan_script), str(out_dir)])
        print("\n✨ เสร็จสิ้นกระบวนการทั้งหมด! สามารถเปิดเว็บและค้นหาใบหน้าได้ทันที")


if __name__ == "__main__":
    main()
