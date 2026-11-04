# -*- coding: utf-8 -*-
"""
search.py — ค้นหาใบหน้าตัวเองในคลังภาพด้วย FAISS + SQLite
=========================================================
การทำงาน:
1. รับรูปเซลฟี่ (อ่านเข้า RAM ประมวลผลแล้วไม่เซฟรูปเซลฟี่เก็บไว้ตามกติกาความเป็นส่วนตัว)
2. ตรวจจับใบหน้าและดึงเวกเตอร์ 512 มิติ (InsightFace)
3. ทำ L2 Normalization แล้วส่งให้ FAISS ค้นหาแบบ Inner Product (ระดับไมโครวินาที)
4. แมป faiss_id กลับไปยังฐานข้อมูล SQLite เพื่อดึงข้อมูลรูปภาพและตำแหน่งกรอบหน้า
5. แสดงผลลัพธ์จัดอันดับความเหมือน พร้อมวาดกรอบไฮไลต์ตำแหน่งที่พบ

วิธีใช้:
  python search.py <รูปเซลฟี่>
  python search.py <รูปเซลฟี่> --top 10 --threshold 0.45
  python search.py <รูปเซลฟี่> --save-result
"""
import argparse
import sys
from pathlib import Path

import cv2
import faiss
import numpy as np

import db
import face_lib

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"
INPUT_DIR = DATA_DIR / "input"
OUTPUT_DIR = DATA_DIR / "output" / "search_results"

# ระดับความมั่นใจในการจำแนกใบหน้า
CONFIDENCE_LEVELS = [
    (0.60, "🟢 ชัวร์มาก (คนเดียวกันแน่นอน)"),
    (0.45, "🟡 น่าจะใช่ (ความคล้ายคลึงสูง)"),
    (0.35, "⚪ ก้ำกึ่ง (ควรตรวจดูด้วยตาเพิ่ม)"),
    (0.00, "❌ ต่ำกว่าเกณฑ์"),
]


def get_confidence_label(score):
    """แปลงคะแนน similarity เป็นป้ายกำกับที่เข้าใจง่าย"""
    for threshold, label in CONFIDENCE_LEVELS:
        if score >= threshold:
            return label
    return "❌ ต่ำกว่าเกณฑ์"


def draw_found_faces(photo_path, matched_faces, out_path):
    """วาดกรอบสี่เหลี่ยมไฮไลต์ตำแหน่งใบหน้าที่ค้นพบ พร้อมระบุคะแนนความเหมือน"""
    img = cv2.imread(str(photo_path))
    if img is None:
        return None

    for item in matched_faces:
        x1 = item["bbox_x1"]
        y1 = item["bbox_y1"]
        x2 = item["bbox_x2"]
        y2 = item["bbox_y2"]
        sim = item["similarity"]

        # กรอบสีเขียวหนาสำหรับหน้าที่แมตช์
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 255, 0), 3)
        label = f"Match: {sim:.2f}"
        
        # วาดพื้นหลังป้ายข้อความให้อ่านง่าย
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.8, 2)
        cv2.rectangle(img, (x1, max(y1 - 30, 0)), (x1 + tw + 10, max(y1, 25)), (0, 255, 0), -1)
        cv2.putText(img, label, (x1 + 5, max(y1 - 8, 18)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 0), 2)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    cv2.imwrite(str(out_path), img)
    return out_path


def main():
    parser = argparse.ArgumentParser(description="ค้นหาใบหน้าตัวเองในคลังรูปภาพ RupRao")
    parser.add_argument("selfie", help="เส้นทางไฟล์รูปเซลฟี่ที่ต้องการใช้ค้นหา")
    parser.add_argument("--top", type=int, default=15, help="จำนวนผลลัพธ์สูงสุดที่ต้องการแสดง (default: 15)")
    parser.add_argument("--threshold", type=float, default=0.35,
                        help="คะแนนความเหมือนขั้นต่ำ 0.0 - 1.0 (default: 0.35)")
    parser.add_argument("--save-result", action="store_true", default=True,
                        help="บันทึกรูปผลลัพธ์พร้อมวาดกรอบไฮไลต์ลงโฟลเดอร์ output")
    args = parser.parse_args()

    selfie_path = Path(args.selfie)
    if not selfie_path.exists():
        raise SystemExit(f"❌ ไม่พบไฟล์รูปเซลฟี่: {selfie_path}")

    # ตรวจสอบว่ามีฐานข้อมูลและ FAISS index แล้วหรือยัง
    if not DB_PATH.exists() or not INDEX_PATH.exists():
        raise SystemExit(
            "❌ ยังไม่มีฐานข้อมูลใบหน้า!\n"
            "   กรุณารัน `python scan_faces.py` เพื่อสแกนรูปภาพเข้าระบบก่อนเริ่มค้นหา"
        )

    print(f"🤳 อ่านรูปเซลฟี่: {selfie_path.name}")
    selfie_img = face_lib.load_image(selfie_path)

    # 1. ตรวจจับหน้าในรูปเซลฟี่
    faces = face_lib.detect_faces(selfie_img)
    if not faces:
        raise SystemExit("❌ ไม่พบใบหน้าคนในรูปเซลฟี่นี้ กรุณาใช้รูปที่เห็นหน้าชัดเจน")

    # ถ้าเจอหลายหน้า ให้เลือกหน้าที่ชัดเจนที่สุด
    best_face = max(faces, key=lambda f: f.det_score)
    print(f"🎯 ตรวจพบใบหน้าในรูปเซลฟี่ (ความชัดเจน: {best_face.det_score:.2f})")

    # 2. สกัดเวกเตอร์และทำ L2 Normalization
    query_emb = np.asarray(best_face.embedding, dtype=np.float32)
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm
    query_emb_2d = query_emb.reshape(1, -1)

    # 3. ค้นหาใน FAISS
    index = faiss.read_index(str(INDEX_PATH))
    if index.ntotal == 0:
        raise SystemExit("⚠️ คลังข้อมูล FAISS ยังว่างเปล่า กรุณารัน scan_faces.py ก่อน")

    k = min(args.top, index.ntotal)
    distances, indices = index.search(query_emb_2d, k)

    # 4. ดึงข้อมูลจาก SQLite
    conn = db.get_connection(DB_PATH)
    faiss_ids = [int(fid) for fid in indices[0] if fid != -1]
    faces_meta = db.get_faces_by_faiss_ids(conn, faiss_ids)

    # 5. รวมผลลัพธ์และกรองตาม threshold
    results = []
    for sim, fid in zip(distances[0], indices[0]):
        if fid == -1 or fid not in faces_meta:
            continue
        sim_val = float(sim)
        if sim_val < args.threshold:
            continue
        item = dict(faces_meta[fid])
        item["similarity"] = sim_val
        results.append(item)

    # 6. แสดงผลลัพธ์
    print("\n" + "=" * 65)
    print(f"🔎 ผลการค้นหา (เกณฑ์ขั้นต่ำ: {args.threshold:.2f} | พบ {len(results)} รายการ)")
    print("=" * 65)

    if not results:
        print("😢 ไม่พบรูปภาพที่ตรงกับใบหน้าของคุณในเกณฑ์ที่กำหนด")
        print("💡 แนะนำ: ลองปรับลด threshold เช่น `--threshold 0.30` หรือใช้รูปเซลฟี่ที่สว่างและหน้าตรงขึ้น")
        return

    # จัดกลุ่มตามรูปภาพเพื่อความสะดวกในการเปิดดู
    photos_matched = {}
    for rank, item in enumerate(results, start=1):
        rel = item["rel_path"]
        sim = item["similarity"]
        label = get_confidence_label(sim)
        event = item["event_name"]

        print(f"อันดับ #{rank:02d} | ความเหมือน: {sim:.4f}  {label}")
        print(f"   📁 งาน : {event}")
        print(f"   🖼️ รูป  : {rel}")
        print(f"   📍 กรอบ: x={item['bbox_x1']}-{item['bbox_x2']}, y={item['bbox_y1']}-{item['bbox_y2']}")
        print()

        photos_matched.setdefault(rel, []).append(item)

    # 7. วาดกรอบและบันทึกภาพตัวอย่างผลลัพธ์ (ถ้าผู้ใช้เปิดออปชัน)
    if args.save_result:
        saved_files = []
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        for rel_path, matched_list in photos_matched.items():
            # ค้นหาไฟล์ต้นฉบับใน INPUT_DIR หรือ BASE
            candidate_paths = [
                INPUT_DIR / rel_path,
                BASE / rel_path,
                DATA_DIR / rel_path,
            ]
            real_path = next((p for p in candidate_paths if p.exists()), None)
            if real_path:
                safe_name = f"found_{Path(rel_path).stem}.jpg"
                out_file = OUTPUT_DIR / safe_name
                res = draw_found_faces(real_path, matched_list, out_file)
                if res:
                    saved_files.append(res)

        if saved_files:
            print("💾 วาดกรอบไฮไลต์ใบหน้าที่พบบันทึกไว้ที่:")
            for sf in saved_files:
                print(f"   -> {sf}")

    print("=" * 65)
    print(f"🎉 สรุป: พบคุณในภาพกิจกรรมทั้งหมด {len(photos_matched)} รูป!")
    print("=" * 65 + "\n")


if __name__ == "__main__":
    main()
