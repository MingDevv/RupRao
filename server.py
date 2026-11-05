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
import hmac
import io
import logging
import os
import re
import sys
import threading
import zipfile
from pathlib import Path
from typing import List, Optional

import cv2
import faiss
import numpy as np
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile, Body, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

import db
import face_lib
import make_thumbs

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger("ruprao")
logger.setLevel(logging.INFO)

BASE = Path(__file__).resolve().parent
DATA_DIR = BASE / "data"
DB_PATH = DATA_DIR / "faces.db"
INDEX_PATH = DATA_DIR / "index.faiss"
THUMB_DIR = DATA_DIR / "thumbs"
INPUT_DIR = DATA_DIR / "input"
STATIC_DIR = BASE / "static"

# รหัสผ่านสำหรับป้องกันการเข้าถึงจากบุคคลภายนอกโรงเรียน (School PIN)
# ปิดการตรวจรหัสผ่านเป็นค่าเริ่มต้น (เว้นว่างไว้) เพื่อให้ผู้ใช้งานเข้าใช้งานได้ทันที
RUPRAO_PIN = os.getenv("RUPRAO_PIN", "").strip()
RUPRAO_ADMIN_PIN = os.getenv("RUPRAO_ADMIN_PIN", "").strip()

_faiss_lock = threading.Lock()

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


def save_index_atomic(index, path: Path = INDEX_PATH):
    """บันทึกไฟล์ FAISS Index อย่างปลอดภัย (Atomic Save) ป้องกันไฟล์เสียหายจากไฟดับหรือ crash"""
    tmp = path.with_suffix(".tmp")
    faiss.write_index(index, str(tmp))
    os.replace(tmp, path)


def check_pin_auth(
    request: Request,
    x_school_pin: Optional[str] = Header(None, alias="X-School-PIN"),
    pin: Optional[str] = Query(None)
):
    """ตรวจสอบสิทธิ์การเข้าถึงด้วยรหัสผ่านโรงเรียน (School PIN Gate) รองรับ Header, Query และ Cookie"""
    if not RUPRAO_PIN or RUPRAO_PIN == "0":
        return True
    client_pin = (x_school_pin or pin or request.cookies.get("ruprao_pin") or "").strip()
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


def find_photo_path(rel_path: str, event_name: str = None) -> Optional[Path]:
    """ค้นหาไฟล์ภาพจริงในเครื่องอย่างปลอดภัย ป้องกัน Path Traversal (ระบุ Path เต็ม ไม่ใช้ recursive fallback)"""
    safe_rel = Path(rel_path)
    if ".." in safe_rel.parts:
        return None

    candidates = [
        INPUT_DIR / safe_rel,
        DATA_DIR / "raw" / safe_rel,
        DATA_DIR / safe_rel,
        BASE / safe_rel,
    ]
    if event_name:
        candidates.insert(0, DATA_DIR / "raw" / event_name / safe_rel)
        candidates.insert(1, INPUT_DIR / event_name / safe_rel)

    for p in candidates:
        if p.exists() and p.is_file():
            return p

    return None


@app.get("/api/auth/status")
async def get_auth_status():
    """ตรวจสอบว่าระบบเปิดใช้งานรหัสผ่านโรงเรียน (School PIN) หรือไม่"""
    return {
        "pin_required": bool(RUPRAO_PIN and RUPRAO_PIN != "0"),
        "admin_pin_required": bool(RUPRAO_ADMIN_PIN and RUPRAO_ADMIN_PIN != "0")
    }


@app.post("/api/auth/verify")
async def verify_pin(payload: dict = Body(...), response: Response = None):
    """ตรวจสอบความถูกต้องของรหัส PIN โรงเรียน พร้อมตั้ง Cookie สำหรับรูปภาพ"""
    if not RUPRAO_PIN or RUPRAO_PIN == "0":
        return {"success": True, "message": "ไม่ต้องใช้รหัสผ่าน"}

    user_pin = str(payload.get("pin", "")).strip()
    if user_pin == RUPRAO_PIN:
        r = JSONResponse({"success": True, "message": "ยืนยันรหัสโรงเรียนสำเร็จ"})
        r.set_cookie("ruprao_pin", user_pin, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
        return r
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


@app.post("/api/search/detect")
async def detect_selfie_faces(
    selfie: UploadFile = File(...),
    authorized: bool = Depends(check_pin_auth)
):
    """ตรวจจับหน้าในเซลฟี่แล้วคืนภาพครอปของแต่ละหน้า (base64) ให้ผู้ใช้เลือกหน้าตัวเองก่อนค้นหา
    (กติกาเหล็ก: ไม่บันทึกอะไรลงดิสก์ — ครอปอยู่ใน RAM แล้วส่งกลับทันที)"""
    import base64
    contents = await selfie.read()
    if len(contents) > 15 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="ขนาดไฟล์เกิน 15 MB")
    nparr = np.frombuffer(contents, np.uint8)
    img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if img_bgr is None:
        raise HTTPException(status_code=400, detail="ไม่สามารถอ่านไฟล์รูปภาพได้ กรุณาใช้ไฟล์ JPG, PNG หรือ WebP")

    faces = face_lib.detect_faces(img_bgr)
    H, W = img_bgr.shape[:2]
    out = []
    for i, f in enumerate(faces):
        x1, y1, x2, y2 = [int(v) for v in f.bbox]
        bw, bh = x2 - x1, y2 - y1
        mx, my = int(bw * 0.25), int(bh * 0.25)   # ขยายกรอบรอบหน้า 25% ให้เห็นบริบท
        cx1, cy1 = max(0, x1 - mx), max(0, y1 - my)
        cx2, cy2 = min(W, x2 + mx), min(H, y2 + my)
        crop = img_bgr[cy1:cy2, cx1:cx2]
        ch, cw = crop.shape[:2]
        scale = min(1.0, 160.0 / max(1, ch))      # ย่อพอสำหรับ UI (ไม่ขยาย — บทเรียนหน้าเบลอ!)
        if scale < 1.0:
            crop = cv2.resize(crop, (max(1, int(cw * scale)), max(1, int(ch * scale))))
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 82])
        out.append({
            "index": i,
            "score": round(float(f.det_score), 3),
            "crop": ("data:image/jpeg;base64," + base64.b64encode(buf).decode("ascii")) if ok else None,
        })
    return {"success": True, "count": len(out), "faces": out}


@app.post("/api/search")
async def search_faces(
    selfie: UploadFile = File(...),
    threshold: float = Form(0.45),
    face_index: int = Form(-1),
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

    # เลือกหน้า: ถ้าผู้ใช้เลือกหน้ามาจาก face-picker ให้ใช้หน้านั้น
    # ไม่งั้นใช้หน้าที่โมเดลมั่นใจสูงสุด (พฤติกรรมเดิม)
    if 0 <= face_index < len(faces):
        best_face = faces[face_index]
    else:
        best_face = max(faces, key=lambda f: f.det_score)

    # 4. ทำ L2 Normalization เพื่อให้ Inner Product = Cosine Similarity
    query_emb = np.asarray(best_face.embedding, dtype=np.float32)
    norm = np.linalg.norm(query_emb)
    if norm > 0:
        query_emb = query_emb / norm
    query_emb_2d = query_emb.reshape(1, -1)

    # 5. ค้นหาใน FAISS (เริ่มต้น k=500 และขยายอัตโนมัติหากยังมีผลคะแนนผ่าน threshold ที่ขอบเขต)
    index = get_faiss_index()
    if index.ntotal == 0:
        return {"success": True, "total_found": 0, "results": []}

    k = min(500, index.ntotal)
    while True:
        distances, indices = index.search(query_emb_2d, k)
        # หากเวกเตอร์ตัวสุดท้ายที่ตำแหน่ง k-1 ยังมีคะแนน >= threshold และยังมีเวกเตอร์ใน index เหลืออยู่ ให้ขยาย k ต่อไป
        if k < index.ntotal and distances[0][-1] >= threshold:
            next_k = min(k + 500, index.ntotal, 20000)
            if next_k == k:
                break
            k = next_k
        else:
            break

    logger.debug(f"[SEARCH LOG] faces: {len(faces)} | k_used: {k} | Top 5: {[round(float(d), 4) for d in distances[0][:5]]} | Threshold: {threshold}")

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
async def get_thumbnail(
    photo_id: int,
    authorized: bool = Depends(check_pin_auth)
):
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
        real_file = find_photo_path(photo["rel_path"], photo.get("event_name"))
        if real_file:
            make_thumbs.create_thumbnail(real_file, thumb_path)

    if not thumb_path.exists():
        # ถ้าไม่มี thumb ให้ส่งไฟล์เดิมถ้ามี
        real_file = find_photo_path(photo["rel_path"], photo.get("event_name"))
        if real_file:
            return FileResponse(str(real_file))
        raise HTTPException(status_code=404, detail="ไม่พบไฟล์ Thumbnail")

    return FileResponse(
        str(thumb_path),
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=86400"}
    )


@app.get("/api/photo/{photo_id}")
async def get_photo(
    photo_id: int,
    authorized: bool = Depends(check_pin_auth)
):
    """ส่งรูปภาพต้นฉบับความละเอียดเต็มสำหรับเปิดดูและดาวน์โหลด"""
    conn = db.get_connection(DB_PATH)
    photo = db.get_photo_by_id(conn, photo_id)
    if not photo:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพ")

    if photo.get("is_hidden"):
        raise HTTPException(status_code=403, detail="รูปภาพนี้ถูกระงับการเผยแพร่ตามคำร้องขอ (PDPA)")

    real_path = find_photo_path(photo["rel_path"], photo.get("event_name"))
    if not real_path:
        raise HTTPException(status_code=404, detail="ไฟล์รูปภาพต้นฉบับไม่อยู่ในเครื่อง")

    return FileResponse(str(real_path))


@app.get("/api/download")
async def download_photos(
    ids: str = Query(..., description="รายการ photo_id คั่นด้วยจุลภาค เช่น 1,2,5"),
    authorized: bool = Depends(check_pin_auth)
):
    """รวมรูปภาพที่เลือกลงในไฟล์ ZIP ให้ดาวน์โหลดในคลิกเดียว (ไม่รวมรูปที่ถูกสั่งซ่อน จำกัดสูงสุด 50 รูปต่อครั้ง)"""
    try:
        photo_ids = [int(x.strip()) for x in ids.split(",") if x.strip().isdigit()]
    except Exception:
        raise HTTPException(status_code=400, detail="รูปแบบ ID ไม่ถูกต้อง")

    if not photo_ids:
        raise HTTPException(status_code=400, detail="ไม่ได้ระบุรูปภาพที่ต้องการดาวน์โหลด")

    if len(photo_ids) > 50:
        raise HTTPException(status_code=400, detail="เลือกได้ไม่เกิน 50 รูปต่อครั้งเพื่อความเสถียรของระบบ")
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
            real_file = find_photo_path(p["rel_path"], p.get("event_name"))
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


@app.get("/admin")
async def admin_page():
    """เปิดหน้าเว็บ Admin Dashboard"""
    admin_file = STATIC_DIR / "admin.html"
    if not admin_file.exists():
        return HTMLResponse("<h1>Admin Dashboard กำลังติดตั้ง...</h1>")
    return FileResponse(str(admin_file))


# =============================================================================
# ADMIN DASHBOARD & MANAGEMENT API
# =============================================================================

RUPRAO_ADMIN_PIN = os.getenv("RUPRAO_ADMIN_PIN", "").strip()


def _admin_ok(pin: str) -> bool:
    """ตรวจสอบความถูกต้องของรหัส Admin PIN ด้วย Constant-Time comparison (ไม่รับ student PIN หรือ hardcoded fallback)"""
    if not RUPRAO_ADMIN_PIN or RUPRAO_ADMIN_PIN == "0":
        return True
    return bool(pin) and hmac.compare_digest(pin, RUPRAO_ADMIN_PIN)


def check_admin_auth(
    request: Request,
    x_admin_pin: Optional[str] = Header(None, alias="X-Admin-PIN"),
    admin_pin: Optional[str] = Query(None)
):
    """ตรวจสอบสิทธิ์สำหรับผู้ดูแลระบบ (Admin PIN Gate)"""
    if not RUPRAO_ADMIN_PIN or RUPRAO_ADMIN_PIN == "0":
        return True

    client_pin = (x_admin_pin or admin_pin or request.cookies.get("ruprao_admin_pin") or "").strip()
    if not _admin_ok(client_pin):
        raise HTTPException(
            status_code=401,
            detail="กรุณาระบุรหัสผ่านผู้ดูแลระบบ (Admin PIN) ที่ถูกต้อง"
        )
    return True


@app.post("/api/admin/login")
async def admin_login(payload: dict = Body(...), response: Response = None):
    """ยืนยันตัวตนเข้าสู่ระบบผู้ดูแลระบบ พร้อมตั้ง Cookie"""
    if not RUPRAO_ADMIN_PIN or RUPRAO_ADMIN_PIN == "0":
        return {"success": True, "message": "เข้าสู่ระบบสำเร็จ (โหมดเปิดเสรี)", "token": "OPEN"}

    pin = str(payload.get("pin", "")).strip()
    if _admin_ok(pin):
        r = JSONResponse({"success": True, "message": "เข้าสู่ระบบแอดมินสำเร็จ", "token": pin})
        r.set_cookie("ruprao_admin_pin", pin, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 7)
        return r
    return JSONResponse(
        status_code=401,
        content={"success": False, "error": "รหัสผ่านแอดมินไม่ถูกต้อง"}
    )


@app.get("/api/admin/overview")
async def get_admin_overview(authorized: bool = Depends(check_admin_auth)):
    """ส่งสถิติภาพรวมสำหรับแดชบอร์ดแอดมิน"""
    conn = db.get_connection(DB_PATH)
    stats = db.get_stats(conn)
    albums = db.get_albums_summary(conn)
    reports = db.get_reports(conn)
    pending_reports = sum(1 for r in reports if r.get("status") == "pending")
    index = get_faiss_index()

    return {
        "success": True,
        "photos_count": stats.get("photos", 0),
        "faces_count": stats.get("faces", 0),
        "faiss_vectors": index.ntotal if index else 0,
        "albums_count": len(albums),
        "albums": albums,
        "reports_total": len(reports),
        "reports_pending": pending_reports
    }


@app.get("/api/admin/photos")
async def get_admin_photos(
    page: int = Query(1, ge=1),
    limit: int = Query(24, ge=1, le=100),
    album: Optional[str] = Query(None),
    search: Optional[str] = Query(None),
    authorized: bool = Depends(check_admin_auth)
):
    """ส่งรายการรูปภาพแบบแบ่งหน้าพร้อมตัวกรองอัลบั้ม"""
    conn = db.get_connection(DB_PATH)
    offset = (page - 1) * limit
    total = db.get_total_photos_count(conn, album=album, search=search)
    photos = db.get_all_photos(conn, limit=limit, offset=offset, album=album, search=search)

    for p in photos:
        p["thumb_url"] = f"/api/thumb/{p['id']}"
        p["photo_url"] = f"/api/photo/{p['id']}"

    return {
        "success": True,
        "page": page,
        "limit": limit,
        "total": total,
        "total_pages": (total + limit - 1) // limit if total > 0 else 1,
        "photos": photos
    }


@app.post("/api/admin/photos/delete")
async def delete_photos_admin(
    payload: dict = Body(...),
    authorized: bool = Depends(check_admin_auth)
):
    """ลบรูปภาพและเวกเตอร์ใบหน้าออกจากระบบทันที"""
    photo_ids = payload.get("photo_ids", [])
    if not photo_ids:
        raise HTTPException(status_code=400, detail="กรุณาระบุ photo_ids ที่ต้องการลบ")

    conn = db.get_connection(DB_PATH)
    photos = db.get_photos_by_ids(conn, photo_ids)
    if not photos:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพที่ระบุ")

    # 1. รวบรวม faiss_ids เพื่อลบออกจาก FAISS
    faiss_ids_to_remove = []
    for p in photos:
        fids = db.get_photo_faiss_ids(conn, p["id"])
        faiss_ids_to_remove.extend(fids)

    # 2. ลบออกจาก FAISS Index อย่างปลอดภัยด้วย Thread Lock และ Atomic Save
    if faiss_ids_to_remove:
        with _faiss_lock:
            index = get_faiss_index()
            index.remove_ids(np.array(faiss_ids_to_remove, dtype=np.int64))
            if INDEX_PATH.exists():
                save_index_atomic(index)

    # 3. ลบไฟล์จริงบนดิสก์และ Thumbnail
    deleted_files = 0
    for p in photos:
        real_file = find_photo_path(p["rel_path"], p.get("event_name"))
        if real_file and real_file.exists():
            try:
                real_file.unlink()
                deleted_files += 1
            except Exception:
                pass
        thumb = (THUMB_DIR / Path(p["rel_path"])).with_suffix(".webp")
        if thumb.exists():
            try:
                thumb.unlink()
            except Exception:
                pass

    # 4. ลบจาก SQLite (CASCADE จะลบ faces และ reports ให้ด้วย)
    for p in photos:
        db.delete_photo_by_id(conn, p["id"])

    return {
        "success": True,
        "deleted_photos": len(photos),
        "removed_faces": len(faiss_ids_to_remove),
        "message": f"ลบรูปภาพ {len(photos)} รูป และใบหน้า {len(faiss_ids_to_remove)} หน้า เรียบร้อยแล้ว"
    }


@app.post("/api/admin/photos/toggle_visibility")
async def toggle_photo_visibility(
    payload: dict = Body(...),
    authorized: bool = Depends(check_admin_auth)
):
    """สลับสถานะซ่อน/แสดงรูปภาพ (PDPA)"""
    photo_id = payload.get("photo_id")
    is_hidden = payload.get("is_hidden")
    if photo_id is None or is_hidden is None:
        raise HTTPException(status_code=400, detail="ข้อมูลไม่ครบถ้วน")

    conn = db.get_connection(DB_PATH)
    success = db.set_photo_hidden(conn, int(photo_id), int(is_hidden))
    if not success:
        raise HTTPException(status_code=404, detail="ไม่พบรูปภาพ")

    status_str = "ซ่อนรูปภาพแล้ว" if is_hidden else "เปิดแสดงรูปภาพแล้ว"
    return {"success": True, "message": f"{status_str} (ID: {photo_id})"}


@app.post("/api/admin/upload")
async def upload_photos_admin(
    album: str = Form("งานกิจกรรมโรงเรียน"),
    files: List[UploadFile] = File(...),
    authorized: bool = Depends(check_admin_auth)
):
    """อัปโหลดรูปภาพกิจกรรมเข้าสู่อัลบั้ม พร้อมสแกนตรวจจับใบหน้าอัตโนมัติทันที"""
    # ป้องกัน Path Traversal ในชื่ออัลบั้ม
    safe_album = re.sub(r'[\\/:*?"<>|]', "_", album.strip()).strip(". ") or "งานกิจกรรมทั่วไป"
    album_dir = DATA_DIR / "raw" / safe_album
    album_dir.mkdir(parents=True, exist_ok=True)
    THUMB_DIR.mkdir(parents=True, exist_ok=True)

    conn = db.get_connection(DB_PATH)
    index = get_faiss_index()

    saved_count = 0
    new_faces_count = 0

    for up_file in files:
        if not up_file.filename:
            continue
        safe_name = re.sub(r'[\\/:*?"<>|]', "_", Path(up_file.filename).name)
        rel_path = f"{safe_album}/{safe_name}"

        # ตรวจสอบว่ารูปนี้เคยบันทึกลงระบบแล้วหรือยัง ก่อนเขียนไฟล์ลงดิสก์
        if db.is_photo_scanned(conn, rel_path):
            continue

        content = await up_file.read()
        save_path = album_dir / safe_name
        is_preexisting = save_path.exists()
        if not is_preexisting:
            with open(save_path, "wb") as f:
                f.write(content)

        nparr = np.frombuffer(content, np.uint8)
        img_bgr = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img_bgr is None:
            if not is_preexisting and save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass
            continue

        thumb_path = (THUMB_DIR / rel_path).with_suffix(".webp")
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            make_thumbs.create_thumbnail(save_path, thumb_path)
        except Exception as thumb_err:
            if not is_preexisting and save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass
            logger.warning(f"Failed to create thumbnail for {rel_path}: {thumb_err}")
            continue

        h, w = img_bgr.shape[:2]
        file_size = len(content)

        faces = face_lib.detect_faces(img_bgr)
        added_fids_this_photo = []
        try:
            photo_id = db.add_photo(conn, rel_path, safe_album, w, h, file_size)

            for idx, face in enumerate(faces):
                if float(face.det_score) < 0.45:
                    continue
                emb = np.asarray(face.embedding, dtype=np.float32)
                norm = np.linalg.norm(emb)
                if norm > 0:
                    emb = emb / norm
                else:
                    continue

                faiss_id = db.get_next_faiss_id(conn)
                emb_2d = emb.reshape(1, -1)
                with _faiss_lock:
                    index.add_with_ids(emb_2d, np.array([faiss_id], dtype=np.int64))
                added_fids_this_photo.append(faiss_id)
                db.add_face(conn, photo_id, idx, face.bbox, float(face.det_score), faiss_id)
                new_faces_count += 1

            conn.commit()
            saved_count += 1
        except Exception as photo_err:
            conn.rollback()
            if added_fids_this_photo:
                with _faiss_lock:
                    index.remove_ids(np.array(added_fids_this_photo, dtype=np.int64))
            if thumb_path.exists():
                try:
                    thumb_path.unlink()
                except Exception:
                    pass
            if not is_preexisting and save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass
            logger.error(f"Error processing {rel_path}: {photo_err}")

    conn.commit()
    if new_faces_count > 0:
        with _faiss_lock:
            save_index_atomic(index)

    return {
        "success": True,
        "saved_photos": saved_count,
        "faces_detected": new_faces_count,
        "message": f"อัปโหลดสำเร็จ {saved_count} รูป ตรวจพบใบหน้าใหม่ {new_faces_count} หน้า"
    }


# ── งานดึงรูป Facebook เบื้องหลัง: สั่งแล้วตอบทันที หน้าเว็บถามความคืบหน้าผ่าน /status ──
import subprocess  # noqa: E402
import time  # noqa: E402

_fetch_job = {
    "running": False, "done": False, "success": None,
    "lines": [], "message": "", "added": None, "started_at": None,
}
_fetch_lock = threading.Lock()


def _count_photos() -> int:
    conn = db.get_connection(DB_PATH)
    n = conn.execute("SELECT COUNT(*) FROM photos").fetchone()[0]
    conn.close()
    return int(n)


def _run_fetch_job(url: str, album: str, limit: int, photos_before: int):
    """รัน fetch_facebook.py (--scan) ในเธรดแยก อ่าน output สดๆ เก็บไว้ให้หน้าเว็บดู"""
    script = BASE / "fetch_facebook.py"
    cmd = [sys.executable, str(script), url, "--album", album, "--limit", str(limit), "--scan"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                with _fetch_lock:
                    _fetch_job["lines"].append(line)
                    del _fetch_job["lines"][:-40]   # เก็บแค่ 40 บรรทัดล่าสุด
        code = proc.wait(timeout=1800)
        added = _count_photos() - photos_before

        # 🧽 นิสัยดีหลังดึงรูป: เก็บกวาดรูปซ้ำทันที (เก็บตัวในอัลบั้มหลักไว้เสมอ)
        removed_dupes = 0
        try:
            import dedup_photos
            from delete_photos import delete_by_rel_paths
            conn = db.get_connection(DB_PATH)
            rel_paths = [r[0] for r in conn.execute("SELECT rel_path FROM photos").fetchall()]
            conn.close()
            dupes = dedup_photos.find_duplicates(rel_paths)
            if dupes:
                delete_by_rel_paths(dupes)
                removed_dupes = len(dupes)
                with _fetch_lock:
                    _fetch_job["lines"].append(f"🧽 จับรูปซ้ำได้ {removed_dupes} รูป — ลบอัตโนมัติ (เก็บตัวในอัลบั้มหลักไว้)")
        except Exception as e:
            with _fetch_lock:
                _fetch_job["lines"].append(f"⚠️ เช็ครูปซ้ำขัดข้อง (ข้ามไป ไม่กระทบงานหลัก): {e}")

        net = added - removed_dupes
        with _fetch_lock:
            _fetch_job["done"] = True
            _fetch_job["success"] = (code == 0)
            _fetch_job["added"] = net
            _fetch_job["message"] = (
                f"เสร็จสมบูรณ์! เพิ่มเข้าคลัง {added} รูป" + (f" (เก็บกวาดรูปซ้ำออก {removed_dupes} รูป สุทธิ {net} รูป)" if removed_dupes else "")
                if code == 0 else f"กระบวนการจบด้วยรหัส {code} — ดูรายละเอียดใน log ด้านบน"
            )
    except Exception as e:
        with _fetch_lock:
            _fetch_job["done"] = True
            _fetch_job["success"] = False
            _fetch_job["message"] = f"ระบบขัดข้องระหว่างดึงรูป: {e}"
    finally:
        get_faiss_index()   # บังคับ reload index ทันทีหลังสแกนเสร็จ
        with _fetch_lock:
            _fetch_job["running"] = False


@app.post("/api/admin/fetch_facebook")
async def fetch_facebook_admin(
    payload: dict = Body(...),
    authorized: bool = Depends(check_admin_auth)
):
    """สั่งดึงรูปจาก Facebook — ตอบทันทีว่าเริ่มแล้ว ความคืบหน้าดูที่ /api/admin/fetch_facebook/status"""
    url = str(payload.get("url", "")).strip()
    album = str(payload.get("album", "Facebook_Photos")).strip() or "Facebook_Photos"
    limit = int(payload.get("limit", 30))

    if not url:
        raise HTTPException(status_code=400, detail="กรุณาระบุ URL ของ Facebook")

    import shutil
    has_gallery_dl = shutil.which("gallery-dl") is not None
    if not has_gallery_dl:
        try:
            import gallery_dl  # noqa: F401
            has_gallery_dl = True
        except ImportError:
            has_gallery_dl = False
    if not has_gallery_dl:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "ยังไม่ได้ติดตั้ง gallery-dl ในระบบ กรุณาติดตั้งด้วยคำสั่ง: pip install gallery-dl"}
        )

    with _fetch_lock:
        if _fetch_job["running"]:
            return {"success": True, "already_running": True,
                    "message": "มีงานดึงรูปกำลังทำงานอยู่แล้ว — ดูความคืบหน้าที่กล่องสถานะเดิม"}
        photos_before = _count_photos()
        _fetch_job.update(running=True, done=False, success=None, lines=[],
                          message="", added=None, started_at=time.time())

    threading.Thread(target=_run_fetch_job, args=(url, album, limit, photos_before), daemon=True).start()
    return {"success": True, "started": True,
            "message": f"เริ่มดึงรูปสูงสุด {limit} รูปเข้าอัลบั้ม '{album}' ในพื้นหลังแล้ว — ระบบจะสแกนหน้าให้ต่อเอง"}


@app.get("/api/admin/fetch_facebook/status")
async def fetch_facebook_status(authorized: bool = Depends(check_admin_auth)):
    """หน้าเว็บถามความคืบหน้าของงานดึงรูป (เรียกทุก ~2-3 วินาที)"""
    with _fetch_lock:
        return dict(_fetch_job)


@app.post("/api/admin/scan_disk")
async def scan_disk_admin(
    authorized: bool = Depends(check_admin_auth)
):
    """สแกนรูปภาพที่ยังไม่ได้เข้าระบบทั้งหมดใน data/raw และ data/input ทันที (ใช้ sys.executable)"""
    import subprocess
    script = BASE / "scan_faces.py"
    python_bin = sys.executable

    targets = [DATA_DIR / "raw", INPUT_DIR]
    outputs = []
    for t in targets:
        if t.exists():
            cmd = [str(python_bin), str(script), str(t)]
            try:
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                outputs.append(proc.stdout)
            except Exception as e:
                outputs.append(f"Error: {e}")

    # บังคับรีโหลด FAISS Index
    global _index_cache, _index_mtime
    _index_cache = None
    _index_mtime = 0
    get_faiss_index()

    conn = db.get_connection(DB_PATH)
    stats = db.get_stats(conn)

    return {
        "success": True,
        "message": "สแกนรูปภาพทั้งหมดในระบบเรียบร้อยแล้ว",
        "photos_count": stats.get("photos", 0),
        "faces_count": stats.get("faces", 0),
        "details": "\n".join(outputs)
    }


@app.get("/api/admin/reports")
async def get_admin_reports(
    status: Optional[str] = Query(None),
    authorized: bool = Depends(check_admin_auth)
):
    """ดึงรายการคำขอลบรูปภาพ (PDPA) ทั้งหมด"""
    conn = db.get_connection(DB_PATH)
    reports = db.get_reports(conn, status=status)
    for r in reports:
        r["thumb_url"] = f"/api/thumb/{r['photo_id']}"
        r["photo_url"] = f"/api/photo/{r['photo_id']}"
    return {"success": True, "reports": reports}


@app.post("/api/admin/reports/{report_id}/action")
async def act_on_report(
    report_id: int,
    payload: dict = Body(...),
    authorized: bool = Depends(check_admin_auth)
):
    """อนุมัติ/ปฏิเสธ/ลบถาวร คำขอลบรูปภาพตามกฎหมาย PDPA"""
    action = str(payload.get("action", "")).lower()
    conn = db.get_connection(DB_PATH)

    if action == "approve":
        success = db.update_report_status(conn, report_id, status="approved", hide_photo=True)
    elif action == "reject":
        success = db.update_report_status(conn, report_id, status="rejected", hide_photo=False)
    elif action == "hide":
        cur = conn.cursor()
        cur.execute("SELECT photo_id FROM reports WHERE id = ?", (int(report_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="ไม่พบคำขอ")
        success = db.set_photo_hidden(conn, int(row[0]), 1)
    elif action == "unhide":
        cur = conn.cursor()
        cur.execute("SELECT photo_id FROM reports WHERE id = ?", (int(report_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="ไม่พบคำขอ")
        success = db.set_photo_hidden(conn, int(row[0]), 0)
    elif action == "delete":
        # ลบรูปภาพและเวกเตอร์ออกจากระบบถาวร (PDPA Right to Erasure)
        cur = conn.cursor()
        cur.execute("SELECT photo_id FROM reports WHERE id = ?", (int(report_id),))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="ไม่พบคำขอ")
        target_photo_id = int(row[0])
        photo = db.get_photo_by_id(conn, target_photo_id)
        if photo:
            fids = db.get_photo_faiss_ids(conn, target_photo_id)
            if fids:
                with _faiss_lock:
                    index = get_faiss_index()
                    index.remove_ids(np.array(fids, dtype=np.int64))
                    if INDEX_PATH.exists():
                        save_index_atomic(index)
            real_file = find_photo_path(photo["rel_path"], photo.get("event_name"))
            if real_file and real_file.exists():
                try:
                    real_file.unlink()
                except Exception:
                    pass
            thumb = (THUMB_DIR / Path(photo["rel_path"])).with_suffix(".webp")
            if thumb.exists():
                try:
                    thumb.unlink()
                except Exception:
                    pass
            db.delete_photo_by_id(conn, target_photo_id)
        success = db.update_report_status(conn, report_id, status="approved", hide_photo=False)
    else:
        raise HTTPException(status_code=400, detail="action ไม่ถูกต้อง")

    if not success:
        raise HTTPException(status_code=404, detail="ไม่พบคำขอ")

    return {"success": True, "message": f"ดำเนินการ '{action}' สำเร็จ"}


@app.delete("/api/admin/albums/{album_name}")
async def delete_album_admin(
    album_name: str,
    authorized: bool = Depends(check_admin_auth)
):
    """ลบทั้งอัลบั้มและเวกเตอร์ใบหน้าทั้งหมดของอัลบั้มนั้น (สำหรับล้างข้อมูลทดสอบ)"""
    conn = db.get_connection(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT id FROM photos WHERE event_name = ?", (album_name,))
    pids = [r[0] for r in cur.fetchall()]
    if not pids:
        raise HTTPException(status_code=404, detail="ไม่พบอัลบั้มที่ระบุ")

    faiss_ids_to_remove = []
    for pid in pids:
        faiss_ids_to_remove.extend(db.get_photo_faiss_ids(conn, pid))

    if faiss_ids_to_remove:
        with _faiss_lock:
            index = get_faiss_index()
            index.remove_ids(np.array(faiss_ids_to_remove, dtype=np.int64))
            if INDEX_PATH.exists():
                save_index_atomic(index)

    photos = db.get_photos_by_ids(conn, pids)
    for p in photos:
        real_file = find_photo_path(p["rel_path"], p.get("event_name"))
        if real_file and real_file.exists():
            try:
                real_file.unlink()
            except Exception:
                pass
        thumb = (THUMB_DIR / Path(p["rel_path"])).with_suffix(".webp")
        if thumb.exists():
            try:
                thumb.unlink()
            except Exception:
                pass
        db.delete_photo_by_id(conn, p["id"])

    return {
        "success": True,
        "deleted_photos": len(photos),
        "removed_faces": len(faiss_ids_to_remove),
        "message": f"ลบอัลบั้ม '{album_name}' เรียบร้อยแล้ว ({len(photos)} รูป, {len(faiss_ids_to_remove)} ใบหน้า)"
    }


@app.get("/api/health")
async def health_check():
    """ตรวจสอบความสมบูรณ์ของระบบ ฐานข้อมูล และ FAISS Index"""
    conn = db.get_connection(DB_PATH)
    stats = db.get_stats(conn)
    index = get_faiss_index()
    photos_count = stats.get("photos", 0)
    faces_count = stats.get("faces", 0)
    faiss_vectors = index.ntotal if index else 0
    is_consistent = (faces_count == faiss_vectors)
    return {
        "status": "healthy" if is_consistent else "warning",
        "photos": photos_count,
        "faces": faces_count,
        "faiss_vectors": faiss_vectors,
        "ok": is_consistent
    }



if __name__ == "__main__":
    import uvicorn
    # เปิดรันทั้ง localhost และวงเน็ต LAN (0.0.0.0) ที่พอร์ต 8000
    print("\n🚀 กำลังเปิดเซิร์ฟเวอร์ RupRao Web...")
    print("👉 เข้าใช้งานจากเครื่องนี้: http://localhost:8000")
    print("👉 เข้าใช้งานจากมือถือ (ในวง Wi-Fi เดียวกัน): http://<IP-เครื่องคุณ>:8000\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=False)
