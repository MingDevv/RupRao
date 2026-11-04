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
    """สร้างตาราง photos, faces, reports และ index ถ้ายังไม่มี"""
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rel_path TEXT UNIQUE NOT NULL,
            event_name TEXT NOT NULL,
            width INTEGER,
            height INTEGER,
            file_size INTEGER,
            is_hidden INTEGER DEFAULT 0,
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

        CREATE TABLE IF NOT EXISTS reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            photo_id INTEGER NOT NULL,
            reason TEXT NOT NULL,
            contact_info TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (photo_id) REFERENCES photos (id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_photos_rel_path ON photos(rel_path);
        CREATE INDEX IF NOT EXISTS idx_faces_photo_id ON faces(photo_id);
        CREATE INDEX IF NOT EXISTS idx_faces_faiss_id ON faces(faiss_id);
        CREATE INDEX IF NOT EXISTS idx_reports_photo_id ON reports(photo_id);
        CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);
        """)

        # ตรวจสอบว่ามีคอลัมน์ is_hidden ในตาราง photos เดิมหรือยัง (Migration สำหรับ DB เดิม)
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(photos)")
        columns = [col[1] for col in cur.fetchall()]
        if "is_hidden" not in columns:
            cur.execute("ALTER TABLE photos ADD COLUMN is_hidden INTEGER DEFAULT 0")


def is_photo_scanned(conn, rel_path):
    """ตรวจสอบว่ารูปนี้เคยสแกนลงฐานข้อมูลไปแล้วหรือยัง (ใช้สำหรับ resume)"""
    cur = conn.cursor()
    name = Path(rel_path).name
    cur.execute("SELECT id FROM photos WHERE rel_path = ? OR rel_path = ?", (str(rel_path), name))
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
            p.height,
            p.is_hidden
        FROM faces f
        JOIN photos p ON f.photo_id = p.id
        WHERE f.faiss_id IN ({placeholders})
          AND (p.is_hidden IS NULL OR p.is_hidden = 0)
    """
    cur = conn.cursor()
    cur.execute(sql, [int(fid) for fid in faiss_ids])
    rows = cur.fetchall()
    return {row["faiss_id"]: dict(row) for row in rows}


def get_photo_by_id(conn, photo_id):
    """ดึงข้อมูลรูปตาม photo_id"""
    cur = conn.cursor()
    cur.execute("SELECT * FROM photos WHERE id = ?", (int(photo_id),))
    row = cur.fetchone()
    return dict(row) if row else None


def get_photos_by_ids(conn, photo_ids):
    """ดึงข้อมูลรูปหลายรายการตามรายการ photo_ids"""
    if not photo_ids:
        return []
    placeholders = ",".join("?" for _ in photo_ids)
    cur = conn.cursor()
    cur.execute(f"SELECT * FROM photos WHERE id IN ({placeholders})", [int(pid) for pid in photo_ids])
    return [dict(r) for r in cur.fetchall()]


def get_stats(conn):
    """สรุปสถิติในฐานข้อมูล: จำนวนรูปทั้งหมด และจำนวนหน้าที่สแกนแล้ว"""
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM photos WHERE is_hidden IS NULL OR is_hidden = 0")
    total_photos = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM faces f JOIN photos p ON f.photo_id = p.id WHERE p.is_hidden IS NULL OR p.is_hidden = 0")
    total_faces = cur.fetchone()[0]

    return {"photos": total_photos, "faces": total_faces}


# --- ส่วนจัดการสิทธิ PDPA และคำขอลบรูปภาพ ---

def add_report(conn, photo_id: int, reason: str, contact_info: str = "") -> int:
    """บันทึกคำร้องขอลบรูปภาพจากผู้ใช้งาน (PDPA Right to be Forgotten)"""
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO reports (photo_id, reason, contact_info, status)
        VALUES (?, ?, ?, 'pending')
        """,
        (int(photo_id), str(reason).strip(), str(contact_info).strip())
    )
    conn.commit()
    return cur.lastrowid


def get_reports(conn, status: str = None):
    """ดึงรายการคำขอลบรูปภาพทั้งหมด"""
    cur = conn.cursor()
    if status:
        cur.execute(
            """
            SELECT r.*, p.rel_path, p.event_name, p.is_hidden
            FROM reports r
            JOIN photos p ON r.photo_id = p.id
            WHERE r.status = ?
            ORDER BY r.created_at DESC
            """,
            (status,)
        )
    else:
        cur.execute(
            """
            SELECT r.*, p.rel_path, p.event_name, p.is_hidden
            FROM reports r
            JOIN photos p ON r.photo_id = p.id
            ORDER BY r.created_at DESC
            """
        )
    return [dict(row) for row in cur.fetchall()]


def set_photo_hidden(conn, photo_id: int, is_hidden: int = 1):
    """สั่งซ่อนหรือเลิกซ่อนรูปภาพ (ซ่อน = ไม่แสดงในผลค้นหาและดาวน์โหลดไม่ได้)"""
    cur = conn.cursor()
    cur.execute("UPDATE photos SET is_hidden = ? WHERE id = ?", (int(is_hidden), int(photo_id)))
    conn.commit()
    return cur.rowcount > 0


def update_report_status(conn, report_id: int, status: str, hide_photo: bool = False):
    """อัปเดตสถานะคำขอ (approved / rejected) และซ่อนรูปภาพถ้าได้รับการอนุมัติ"""
    cur = conn.cursor()
    cur.execute("SELECT photo_id FROM reports WHERE id = ?", (int(report_id),))
    row = cur.fetchone()
    if not row:
        return False

    photo_id = row[0]
    cur.execute("UPDATE reports SET status = ? WHERE id = ?", (status, int(report_id)))
    if hide_photo and status == "approved":
        cur.execute("UPDATE photos SET is_hidden = 1 WHERE id = ?", (int(photo_id),))
    conn.commit()
    return True


def get_all_photos(conn, limit: int = 50, offset: int = 0, album: str = None, search: str = None):
    """ดึงรายการรูปภาพทั้งหมดสำหรับหน้าแอดมิน พร้อมจำนวนใบหน้าที่พบในแต่ละรูป"""
    cur = conn.cursor()
    params = []
    where_clauses = []
    if album:
        where_clauses.append("p.event_name = ?")
        params.append(album)
    if search:
        where_clauses.append("(p.rel_path LIKE ? OR p.event_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    sql = f"""
        SELECT p.*, count(f.id) as face_count
        FROM photos p
        LEFT JOIN faces f ON p.id = f.photo_id
        {where_sql}
        GROUP BY p.id
        ORDER BY p.id DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    cur.execute(sql, params)
    return [dict(r) for r in cur.fetchall()]


def get_total_photos_count(conn, album: str = None, search: str = None):
    """นับจำนวนรูปภาพทั้งหมดตาม filter"""
    cur = conn.cursor()
    params = []
    where_clauses = []
    if album:
        where_clauses.append("event_name = ?")
        params.append(album)
    if search:
        where_clauses.append("(rel_path LIKE ? OR event_name LIKE ?)")
        params.extend([f"%{search}%", f"%{search}%"])

    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
    cur.execute(f"SELECT count(*) FROM photos {where_sql}", params)
    return cur.fetchone()[0]


def get_albums_summary(conn):
    """ดึงรายชื่ออัลบั้ม/งานทั้งหมดพร้อมจำนวนรูปในแต่ละงาน"""
    cur = conn.cursor()
    cur.execute("""
        SELECT event_name, count(*) as photo_count
        FROM photos
        GROUP BY event_name
        ORDER BY photo_count DESC
    """)
    return [dict(r) for r in cur.fetchall()]


def get_photo_faiss_ids(conn, photo_id: int):
    """ดึง faiss_id ทั้งหมดของรูปนี้"""
    cur = conn.cursor()
    cur.execute("SELECT faiss_id FROM faces WHERE photo_id = ?", (int(photo_id),))
    return [r[0] for r in cur.fetchall()]


def delete_photo_by_id(conn, photo_id: int):
    """ลบรูปภาพจาก SQLite (cascade delete faces และ reports อัตโนมัติ)"""
    cur = conn.cursor()
    cur.execute("DELETE FROM photos WHERE id = ?", (int(photo_id),))
    conn.commit()
    return cur.rowcount > 0



