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
import re
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DATA_RAW = BASE / "data" / "raw"
PYTHON_BIN = sys.executable


def get_gallery_dl_cmd():
    """ค้นหาคำสั่ง gallery-dl ทั้งแบบไบนารีและแบบโมดูลไพทอนเพื่อความเสถียรใน Docker/Server"""
    which_path = shutil.which("gallery-dl")
    if which_path:
        return [which_path]
    venv_dl = Path(PYTHON_BIN).parent / "gallery-dl"
    if venv_dl.exists():
        return [str(venv_dl)]
    return [PYTHON_BIN, "-m", "gallery_dl"]


def resolve_fb_url(url: str) -> str:
    """แปลง share link (เช่น /share/p/...) เป็น URL โพสต์จริงบน Facebook"""
    url = url.strip()
    if "/share/" in url:
        mobile_url = re.sub(r"https?://(www\.)?facebook\.com", "https://m.facebook.com", url)
        try:
            cmd = ["curl", "-s", "-i", "-A", "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X)", mobile_url]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            for line in res.stdout.splitlines():
                if line.lower().startswith("location:"):
                    loc = line.split(":", 1)[1].strip()
                    clean_url = loc.split("?")[0]
                    clean_url = clean_url.replace("m.facebook.com", "www.facebook.com")
                    print(f"🔗 แก้ไข Share URL เป็น URL จริง: {clean_url}")
                    return clean_url
        except Exception as e:
            print(f"⚠️ เกิดข้อผิดพลาดขณะ resolve share URL: {e}")
    return url


def main():
    parser = argparse.ArgumentParser(description="ดูดรูปภาพจากเพจ Facebook โรงเรียนเข้าสู่ RupRao")
    parser.add_argument("url", help="URL ของเพจ Facebook หรือ อัลบั้มรูป หรือ ลิงก์โพสต์")
    parser.add_argument("--limit", type=int, default=50, help="จำนวนรูปสูงสุดที่ต้องการดึง (ค่าเริ่มต้น: 50 รูป)")
    parser.add_argument("--album", type=str, default="Facebook_Photos", help="ชื่องาน/โฟลเดอร์สำหรับเก็บรูป")
    parser.add_argument("--scan", action="store_true", help="สั่งสแกนใบหน้าเข้าระบบทันทีหลังจากดาวน์โหลดเสร็จ")

    args = parser.parse_args()

    # จัดการ URL: ถอด Share Link และตรวจสอบประเภท URL
    target_url = resolve_fb_url(args.url)
    
    # ถ้าเป็นหน้าเพจหลัก (ไม่ใช่โพสต์, อัลบั้ม, หรือรูปภาพ) ให้ชี้ไปที่ /photos เพื่อดึงภาพกิจกรรม
    post_indicators = ["/posts/", "/photo", "/photos", "/media", "/albums", "/permalink.php", "set="]
    if not any(k in target_url for k in post_indicators):
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

    cmd = get_gallery_dl_cmd() + [
        "--range", f"1-{args.limit}",
        "--directory", str(out_dir),
        "--no-mtime",
        target_url
    ]

    image_exts = [".jpg", ".jpeg", ".png", ".webp"]
    before = {p.name for p in out_dir.glob("*") if p.suffix.lower() in image_exts}

    print("⏳ กำลังเชื่อมต่อ Facebook และเริ่มดาวน์โหลดรูปภาพความละเอียดสูง...")
    download_ok = True
    try:
        subprocess.run(cmd, check=True)
        print("\n✅ ดาวน์โหลดรูปภาพจาก Facebook เรียบร้อยแล้ว!")
    except subprocess.CalledProcessError as e:
        download_ok = False
        print(f"\n❌ ดาวน์โหลดไม่สำเร็จ (gallery-dl exit {e.returncode})")
        print("   สาเหตุที่พบบ่อย: Facebook จำกัดการเข้าถึงชั่วคราว (รอสักครู่แล้วลองใหม่) หรือ URL ไม่ถูกต้อง")

    # นับผลจริง: รูปใหม่ = ไฟล์ที่เพิ่มเข้ามาจริงๆ (ไฟล์ซ้ำ gallery-dl ข้ามให้เอง)
    after = {p.name for p in out_dir.glob("*") if p.suffix.lower() in image_exts}
    new_files = after - before
    print(f"📸 รูปในโฟลเดอร์ {args.album}: รวม {len(after)} รูป (ดึงใหม่ครั้งนี้ {len(new_files)} รูป)")
    print(f"RESULT: new={len(new_files)} total={len(after)} download_ok={download_ok}")
    if not new_files:
        print("⚠️ ไม่มีรูปใหม่เลย! อาจเป็นเพราะเคยดึงรูปเหล่านี้ไปแล้ว หรือ Facebook จำกัดการเข้าถึงชั่วคราว (ระบบจะสแกนรูปเก่าที่ยังไม่ได้สแกนต่อให้)")

    if args.scan:
        print("\n🧠 กำลังเริ่มสแกนใบหน้าเข้าสู่ฐานข้อมูล RupRao ทันที...")
        scan_script = BASE / "scan_faces.py"
        subprocess.run([str(PYTHON_BIN), str(scan_script), str(out_dir)])
        print("\n✨ เสร็จสิ้นกระบวนการทั้งหมด! สามารถเปิดเว็บและค้นหาใบหน้าได้ทันที")


if __name__ == "__main__":
    main()
