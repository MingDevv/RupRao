# -*- coding: utf-8 -*-
"""
make_thumbs.py — สร้างรูปตัวอย่างขนาดย่อม (Thumbnail) WebP 640px
==============================================================
เหตุผลที่ต้องทำ:
- รูปต้นฉบับจากกล้องมักมีขนาดใหญ่ 5–20 MB (โหลดช้า กินแรม)
- เว็บโชว์ผลลัพธ์ต้องการรูปขนาดพอดีๆ (~640px) โหลดเร็วในพริบตา
- ฟอร์แมต WebP บีบอัดได้ดีกว่า JPEG 30-50% โดยความคมชัดใกล้เคียงกัน

วิธีใช้:
  python make_thumbs.py                      -> ย่อทุกรูปใน data/input ไปไว้ที่ data/thumbs
  python make_thumbs.py <โฟลเดอร์รูป>         -> ย่อรูปในโฟลเดอร์ที่ระบุ
"""
import sys
from pathlib import Path
from PIL import Image, ImageOps

BASE = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = BASE / "data" / "input"
DEFAULT_THUMB_DIR = BASE / "data" / "thumbs"
MAX_SIZE = (640, 640)
WEBP_QUALITY = 82


def create_thumbnail(src_path, dest_path, max_size=MAX_SIZE, quality=WEBP_QUALITY):
    """
    สร้าง thumbnail ไฟล์ WebP:
    - หมุนภาพตามค่า EXIF อัตโนมัติ (เช่น รูปที่ถ่ายแนวตั้งจากมือถือ)
    - ย่อขนาดไม่เกิน max_size โดยคงสัดส่วนเดิมไว้
    - บันทึกเป็นนามสกุล .webp
    """
    src_path = Path(src_path)
    dest_path = Path(dest_path)
    dest_path.parent.mkdir(parents=True, exist_ok=True)

    # ข้ามถ้า thumbnail มีอยู่แล้วและใหม่กว่าไฟล์ต้นฉบับ
    if dest_path.exists() and dest_path.stat().st_mtime >= src_path.stat().st_mtime:
        return False

    with Image.open(src_path) as img:
        img = ImageOps.exif_transpose(img)
        img.thumbnail(max_size, Image.Resampling.LANCZOS)
        img.save(dest_path, "WEBP", quality=quality, method=4)

    return True


def batch_create_thumbs(source_dir, output_dir=DEFAULT_THUMB_DIR):
    """วนย่อรูปทั้งหมดในโฟลเดอร์ source_dir และโฟลเดอร์ย่อย"""
    source_dir = Path(source_dir)
    output_dir = Path(output_dir)

    valid_exts = {".jpg", ".jpeg", ".png", ".webp", ".JPG", ".JPEG", ".PNG", ".WEBP"}
    all_files = [p for p in source_dir.rglob("*") if p.suffix in valid_exts]

    if not all_files:
        print(f"⚠️ ไม่พบรูปภาพใน {source_dir}")
        return 0, 0

    print(f"🖼️ เริ่มสร้าง Thumbnail สำหรับ {len(all_files)} รูป...")
    created_count = 0
    skipped_count = 0

    for p in all_files:
        rel = p.relative_to(source_dir)
        dest = (output_dir / rel).with_suffix(".webp")
        try:
            if create_thumbnail(p, dest):
                created_count += 1
            else:
                skipped_count += 1
        except Exception as e:
            print(f"❌ ย่อรูป {p.name} ไม่สำเร็จ: {e}")

    print(f"✅ เสร็จสิ้น: สร้างใหม่ {created_count} รูป, ข้าม (มีอยู่แล้ว) {skipped_count} รูป\n")
    return created_count, skipped_count


def main():
    target_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_INPUT_DIR
    batch_create_thumbs(target_dir, DEFAULT_THUMB_DIR)


if __name__ == "__main__":
    main()
