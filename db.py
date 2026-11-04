# -*- coding: utf-8 -*-
"""
db.py — ระบบฐานข้อมูล SQLite ของโปรเจกต์ RupRao
==============================================
เก็บข้อมูลภาพถ่ายและตำแหน่งใบหน้า:
- ตาราง photos: เก็บรายชื่อรูป, ขนาด, ชื่องาน, วันเวลาที่สแกน
- ตาราง faces: เก็บตำแหน่งกรอบหน้า (bbox), คะแนนความมั่นใจ, และ faiss_id
"""
import sqlite3
from pathlib import Path


def get_connection(db_path="data/faces.db"):
    """เปิดการเชื่อมต่อกับฐานข้อมูล SQLite และสร้างตารางถ้ายังไม่มี"""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    return conn


def init_schema(conn):
    """สร้างตาราง photos, faces และ index ถ้ายังไม่มี"""
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT UNIQUE NOT NULL,
            event_name TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            file_size INTEGER,
            scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS faces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            face_index INTEGER NOT NULL,
            bbox_x1 INTEGER NOT NULL,
            bbox_y1 INTEGER NOT NULL,
            bbox_x2 INTEGER NOT NULL,
            bbox_y2 INTEGER NOT NULL,
            det_score REAL NOT NULL,
            faiss_id INTEGER UNIQUE NOT NULL,
            FOREIGN KEY (photo_id) REFERENCES photos (id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_photos_rel_path ON photos(rel_path);
        CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces(photo_id);
        CREATE INDEX IF NOT EXISTS idx_faces_faiss_id ON faces(faiss_id);
        """)


def is_photo_scanned(conn, rel_path):
    """ตรวจสอบว่ารูปนี้เคยสแกนลงฐานข้อมูลไปแล้วหรือยัง (ใช้สำหรับ resume)"""
    cur = conn.cursor()
    cur.execute("SELECT id FROM photos WHERE rel_path = ?", (str(rel_path),))
    return cur.fetchone() is not None


def add_photo(conn, rel_path, event_name, width, height, file_size):
    """บันทึกข้อมูลรูปใหม่ คืนค่า photo_id"""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO photos (rel_path, event_name, width, height, file_size)
        VALUES (?, ?, ?, ?, ?)
        """,
        (str(rel_path), str(event_name), width, height, file_size)
    )
    return cur.lastrowid


def add_face(conn, photo_id, face_index, bbox, det_score, faiss_id):
    """บันทึกข้อมูลใบหน้าที่พบในรูป"""
    x1, y1, x2, y2 = [int(v) for v in bbox]
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO faces (photo_id, face_index, bbox_x1, bbox_y1, bbox_x2, bbox_y2, det_score, faiss_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (photo_id, face_index, x1, y1, x2, y2, float(det_score), int(faiss_id))
    )
    return cur.lastrowid


def get_next_faiss_id(conn):
    """หา faiss_id ถัดไปสำหรับเพิ่มเวกเตอร์ใหม่ลง FAISS"""
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(MAX(faiss_id), -1) + 1 FROM faces")
    return int(cur.fetchone()[0])


def get_faces_by_faiss_ids(conn, faiss_ids):
    """ดึงข้อมูลรูปและตำแหน่งหน้าจาก faiss_ids ที่ได้จากการค้นหา FAISS"""
    if not faiss_ids:
        return {}

    placeholders = ",".join("?" for _ in faiss_ids)
    sql = f"""
        SELECT 
            f.faiss_id,
            f.face_index,
            f.bbox_x1,
            f.bbox_y1,
            f.bbox_x2,
            f.bbox_y2,
            f.det_score,
            p.id as photo_id,
            p.rel_path,
            p.event_name,
            p.width,
            p.height
        FROM faces f
        JOIN photos p ON f.photo_id = p.id
        WHERE f.faiss_id IN ({placeholders})
    """
    cur = conn.cursor()
    cur.execute(sql, [int(fid) for fid in faiss_ids])
    rows = cur.fetchall()
    return {row["faiss_id"]: dict(row) for row in rows}


def get_stats(conn):
    """สรุปสถิติในฐานข้อมูล: จำนวนรูปทั้งหมด และจำนวนหน้าที่สแกนแล้ว"""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM photos")
    total_photos = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM faces")
    total_faces = cur.fetchone()[0]

    return {"photos": total_photos, "faces": total_faces}
