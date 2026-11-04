# -*- coding: utf-8 -*-
"""
server.py — เว็บเซิร์ฟเวอร์ FastAPI สำหรับระบบค้นหาใบหน้า RupRao
==============================================================
API Endpoints:
- GET  /                     -> หน้าเว็บค้นหา (Single Page Web App)
- GET  /api/stats            -> สถิติจำนวนรูปและใบหน้าในระบบ
- POST /api/search           -> อัปโหลดเซลฟี่ ค้นหาใบหน้าผ่าน RAM (ลบทันที ไม่เก็บลงดิสก์)
- GET  /api/thumb/{photo_id} -> ดึงรูป Thumbnail WebP 640px
- GET  /api/photo/{photo_id} -> ดึงรูปต้นฉบับความละเอียดเต็ม
- GET  /api/download?ids=... -> ดาวน์โหลดรูปที่เลือกเป็นไฟล์ ZIP ในคลิกเดียว
"""
import io
import os
import zipfile
from pathlib import Path
from typing import List, Optional

import cv2
import faiss
import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
import face_lib
import make_thumbs

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"
THUMB_DIR = DATA_DIR / "thumbs"
INPUT_DIR = DATA_DIR / "input"
STATIC_DIR = BASE / "static"

# รหัสผ่านสำหรับป้องกันการเข้าถึงจากบุคคลภายนอกโรงเรียน (School PIN)
# สามารถกำหนดผ่าน Environment Variable RUPRAO_PIN หรือเว้นว่าง/ตั้งเป็น 0 เพื่อปิดการตรวจ
RUPRAO_PIN = os.getenv("RUPRAO_PIN", "RUPRAO2026").strip()

app = FastAPI(
    title="RupRao — ค้นหาตัวเองจากรูปกิจกรรมโรงเรียน",
    description="ระบบ AI ค้นหาภาพถ่ายบุคคลด้วย InsightFace และ FAISS",
    version="1.0.0"
)

# อนุญาต CORS สำหรับการพัฒนาและการเชื่อมต่อในวง LAN
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_index_cache = None
_index_mtime = 0


def check_pin_auth(
    x_school_pin: Optional[str] = Header(None, alias="X-School-PIN"),
    pin: Optional[str] = Query(None)
):
    """ตรวจสอบสิทธิ์การเข้าถึงด้วยรหัสผ่านโรงเรียน (School PIN Gate)"""
    if not RUPRAO_PIN or RUPRAO_PIN == "0":
        return True
    client_pin = (x_school_pin or pin or "").strip()
    if client_pin != RUPRAO_PIN:
        raise HTTPException(
            status_code=401,
            detail="กรุณาระบุรหัสผ่านโรงเรียน (School PIN) ที่ถูกต้องเพื่อเข้าใช้งาน"
        )
    return True


def get_faiss_index():
    """โหลด FAISS Index ไว้ในหน่วยความจำ และรีโหลดอัตโนมัติเมื่อไฟล์ index.faiss มีการอัปเดตใหม่"""
    global _index_cache, _index_mtime
    if INDEX_PATH.exists():
        current_mtime = INDEX_PATH.stat().st_mtime
        if _index_cache is None or current_mtime > _index_mtime:
            _index_cache = faiss.read_index(str(INDEX_PATH))
            _index_mtime = current_mtime
    elif _index_cache is None:
        # สร้าง Empty Index สำรองกรณีเพิ่งติดตั้งใหม่ยังไม่มีไฟล์ index.faiss
        _index_cache = faiss.IndexIDMap2(faiss.IndexFlatIP(512))
    return _index_cache


def get_confidence_label(score: float):
    """แปลงค่า similarity เป็นป้ายกำกับระดับความมั่นใจ"""
    if score >= 0.60:
        return "🟢 ชัวร์มาก"
    elif score >= 0.45:
        return "🟡 น่าจะใช่"
    elif score >= 0.35:
        return "⚪ ก้ำกึ่ง"
    return "❌ ต่ำกว่าเกณฑ์"


def find_photo_path(rel_path: str) -> Optional[Path]:
    """ค้นหาไฟล์ภาพจริงในเครื่องอย่างปลอดภัย ป้องกัน Path Traversal"""
    # ล้างอักขระแปลกปลอม
    safe_rel = Path(rel_path)
    if ".." in safe_rel.parts:
        return None

    candidates = [
        INPUT_DIR / safe_rel,
        DATA_DIR / safe_rel,
        BASE / safe_rel,
    ]
    for p in candidates:
        if p.exists() and p.is_file():
            return p
    return None


@app.get("/api/auth/status")
async def get_auth_status():
    """ตรวจสอบว่าระบบเปิดใช้งานรหัสผ่านโรงเรียน (School PIN) หรือไม่"""
    return {
        "pin_required": bool(RUPRAO_PIN and RUPRAO_PIN != "0")
    }


@app.post("/api/auth/verify")
async def verify_pin(payload: dict = Body(...)):
    """ตรวจสอบความถูกต้องของรหัส PIN โรงเรียน"""
    if not RUPRAO_PIN or RUPRAO_PIN == "0":
        return {"success": True, "message": "ไม่ต้องใช้รหัสผ่าน"}

    user_pin = str(payload.get("pin", "")).strip()
    if user_pin == RUPRAO_PIN:
        return {"success": True, "message": "ยืนยันรหัสโรงเรียนสำเร็จ"}
    return JSONResponse(
        status_code=401,
        content={"success": False, "error": "รหัสผ่านโรงเรียนไม่ถูกต้อง กรุณาลองใหม่อีกครั้ง"}
    )


@app.post("/api/report_photo")
async def report_photo(
    payload: dict = Body(...),
    authorized: bool = Depends(check_pin_auth)
):
    """บันทึกคำร้องขอลบรูปภาพตามกฎหมาย PDPA (Right to be Forgotten)"""
    photo_id = payload.get("photo_id")
    reason = str(payload.get("reason", "")).strip()
    contact_info = str(payload.get("contact_info", "")).strip()

    if not photo_id or not reason:
        raise HTTPException(status_code=400, detail="กรุณาระบุ photo_id และเหตุผลในการขอลบ")

    conn = db.get_connection(DB_PATH)
    photo = db.get_photo_by_id(conn, int(photo_id))
    if not photo:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพที่ระบุ")

    report_id = db.add_report(conn, int(photo_id), reason, contact_info)
    return {
        "success": True,
        "report_id": report_id,
        "message": "ส่งคำร้องขอลบรูปเรียบร้อยแล้ว ทีมงานจะตรวจสอบและดำเนินการโดยเร็วที่สุด"
    }


@app.get("/api/stats")
async def get_stats():
    """ส่งข้อมูลสถิติรูปภาพและใบหน้าในระบบ"""
    if not DB_PATH.exists():
        return {"photos": 0, "faces": 0}
    conn = db.get_connection(DB_PATH)
    return db.get_stats(conn)


@app.post("/api/search")
async def search_faces(
    selfie: UploadFile = File(...),
    threshold: float = Form(0.35),
    authorized: bool = Depends(check_pin_auth)
):
    """
    รับรูปเซลฟี่ → ประมวลผลใน RAM → ค้นหาใน FAISS → ลบเซลฟี่ออกจากหน่วยความจำทันที
    (กติกาเหล็ก: ไม่มีการบันทึกภาพเซลฟี่ของผู้ค้นหาลงดิสก์เด็ดขาด)
    """
    # 1. ตรวจสอบชนิดไฟล์และขนาดไฟล์ (จำกัด 15 MB)
    contents = await selfie.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="ขนาดไฟล์เกิน 15 MB")

    # 2. ถอดรหัสรูปภาพใน RAM
    nparr = np.frombuffer(contents, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise HTTPException(status_code=400, detail="ไม่สามารถอ่านไฟล์รูปภาพได้ กรุณาใช้ไฟล์ JPG, PNG หรือ WebP")

    # 3. ตรวจจับใบหน้า
    faces = face_lib.detect_faces(img_bgr)
    if not faces:
        return JSONResponse(
            status_code=422,
            content={
                "success": False,
                "error": "ไม่พบใบหน้าคนในรูปเซลฟี่ กรุณาลองใช้รูปที่เห็นหน้าตรงและมีแสงสว่างชัดเจน"
            }
        )

    # เลือกหน้าที่คะแนนความชัดเจนสูงที่สุด
    best_face = max(faces, key=lambda f: f.det_score)

    # 4. ทำ L2 Normalization เพื่อให้ Inner Product = Cosine Similarity
    query_emb = np.asarray(best_face.embedding, dtype=np.float32)
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm
    query_emb_2d = query_emb.reshape(1, -1)

    # 5. ค้นหาใน FAISS
    index = get_faiss_index()
    if index.ntotal == 0:
        return {"success": True, "total_found": 0, "results": []}

    k = min(50, index.ntotal)
    distances, indices = index.search(query_emb_2d, k)

    # 6. ดึงข้อมูลจาก SQLite
    conn = db.get_connection(DB_PATH)
    faiss_ids = [int(fid) for fid in indices[0] if fid != -1]
    faces_meta = db.get_faces_by_faiss_ids(conn, faiss_ids)

    # 7. จัดกลุ่มผลลัพธ์แยกตามรูปภาพ (ถ้าในรูปเดียวกันแมตช์หลายหน้า ให้เลือกหน้าที่คะแนนสูงสุด)
    photo_best_matches = {}
    for sim, fid in zip(distances[0], indices[0]):
        if fid == -1 or fid not in faces_meta:
            continue
        sim_val = float(sim)
        if sim_val < threshold:
            continue

        item = faces_meta[fid]
        pid = item["photo_id"]
        if pid not in photo_best_matches or sim_val > photo_best_matches[pid]["similarity"]:
            photo_best_matches[pid] = {
                "photo_id": pid,
                "rel_path": item["rel_path"],
                "event_name": item["event_name"],
                "similarity": round(sim_val, 4),
                "similarity_percent": int(round(sim_val * 100)),
                "confidence_label": get_confidence_label(sim_val),
                "thumb_url": f"/api/thumb/{pid}",
                "photo_url": f"/api/photo/{pid}",
                "bbox": [item["bbox_x1"], item["bbox_y1"], item["bbox_x2"], item["bbox_y2"]],
                "width": item["width"],
                "height": item["height"]
            }

    # นับจำนวนหน้าที่พบในรูปเซลฟี่ไว้ก่อนคืนผลลัพธ์
    num_selfie_faces = len(faces)

    # เรียงลำดับจากคะแนนมากไปน้อย
    results = sorted(photo_best_matches.values(), key=lambda x: x["similarity"], reverse=True)

    # ล้างตัวแปรข้อมูลภาพใน RAM ทันที (ไม่เก็บบัฟเฟอร์รูปเซลฟี่ไว้)
    del contents, nparr, img_bgr, faces, query_emb, query_emb_2d

    return {
        "success": True,
        "total_found": len(results),
        "selfie_faces_detected": num_selfie_faces,
        "results": results
    }


@app.get("/api/thumb/{photo_id}")
async def get_thumbnail(photo_id: int):
    """ส่งรูปภาพ Thumbnail WebP 640px"""
    conn = db.get_connection(DB_PATH)
    photo = db.get_photo_by_id(conn, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพ")

    if photo.get("is_hidden"):
        raise HTTPException(status_code=403, detail="รูปภาพนี้ถูกระงับการแสดงผลตามคำร้องขอ (PDPA)")

    rel = Path(photo["rel_path"])
    thumb_path = (THUMB_DIR / rel).with_suffix(".webp")

    # ถ้ายังไม่มี thumbnail ให้ลองสร้างด่วน
    if not thumb_path.exists():
        real_file = find_photo_path(photo["rel_path"])
        if real_file:
            make_thumbs.create_thumbnail(real_file, thumb_path)

    if not thumb_path.exists():
        # ถ้าไม่มี thumb ให้ส่งไฟล์เดิมถ้ามี
        real_file = find_photo_path(photo["rel_path"])
        if real_file:
            return FileResponse(str(real_file))
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์ Thumbnail")

    return FileResponse(
        str(thumb_path),
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400"}
    )


@app.get("/api/photo/{photo_id}")
async def get_photo(photo_id: int):
    """ส่งรูปภาพต้นฉบับความละเอียดเต็มสำหรับเปิดดูและดาวน์โหลด"""
    conn = db.get_connection(DB_PATH)
    photo = db.get_photo_by_id(conn, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพ")

    if photo.get("is_hidden"):
        raise HTTPException(status_code=403, detail="รูปภาพนี้ถูกระงับการเผยแพร่ตามคำร้องขอ (PDPA)")

    real_path = find_photo_path(photo["rel_path"])
    if not real_path:
        raise HTTPException(status_code=404, detail="ไฟล์รูปภาพต้นฉบับไม่อยู่ในเครื่อง")

    return FileResponse(str(real_path))


@app.get("/api/download")
async def download_photos(
    ids: str = Query(..., description="รายการ photo_id คั่นด้วยจุลภาค เช่น 1,2,5"),
    authorized: bool = Depends(check_pin_auth)
):
    """รวมรูปภาพที่เลือกลงในไฟล์ ZIP ให้ดาวน์โหลดในคลิกเดียว (ไม่รวมรูปที่ถูกสั่งซ่อน)"""
    try:
        photo_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    except Exception:
        raise HTTPException(status_code=400, detail="รูปแบบ ID ไม่ถูกต้อง")

    if not photo_ids:
        raise HTTPException(status_code=400, detail="ไม่ได้ระบุรูปภาพที่ต้องการดาวน์โหลด")

    conn = db.get_connection(DB_PATH)
    photos = db.get_photos_by_ids(conn, photo_ids)
    if not photos:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพที่ระบุ")

    # กรองเฉพาะรูปที่ไม่ได้ถูกซ่อน
    photos = [p for p in photos if not p.get("is_hidden")]
    if not photos:
        raise HTTPException(status_code=403, detail="รูปภาพที่เลือกถูกระงับการเผยแพร่ทั้งหมด")

    # สร้างไฟล์ ZIP ในหน่วยความจำ
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        added_names = set()
        for p in photos:
            real_file = find_photo_path(p["rel_path"])
            if real_file and real_file.exists():
                safe_name = f"{p['event_name']}_{real_file.name}"
                # ป้องกันชื่อไฟล์ซ้ำใน zip
                counter = 1
                unique_name = safe_name
                while unique_name in added_names:
                    unique_name = f"{real_file.stem}_{counter}{real_file.suffix}"
                    counter += 1
                added_names.add(unique_name)
                zf.write(str(real_file), arcname=unique_name)

    zip_buffer.seek(0)
    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=RupRao_photos.zip"}
    )


# ให้บริการไฟล์หน้าเว็บ Static (index.html)
if not STATIC_DIR.exists():
    STATIC_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def root():
    """เปิดหน้าแรกของเว็บ"""
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        return HTMLResponse("<h1>RupRao Web Server พร้อมทำงาน (กำลังโหลดหน้าเว็บ...)</h1>")
    return FileResponse(str(index_file))


if __name__ == "__main__":
    import uvicorn
    # เปิดรันทั้ง localhost และวงเน็ต LAN (0.0.0.0) ที่พอร์ต 8000
    print("\n🚀 กำลังเปิดเซิร์ฟเวอร์ RupRao Web...")
    print("👉 เข้าใช้งานจากเครื่องนี้: http://localhost:8000")
    print("👉 เข้าใช้งานจากมือถือ (ในวง Wi-Fi เดียวกัน): http://<IP-เครื่องคุณ>:8000\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
