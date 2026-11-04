# -*- coding: utf-8 -*-
"""
test_production.py — ชุดทดสอบระบบ RupRao ระดับ Production End-to-End
================================================================
ตรวจสอบ:
1. การทำงานของระบบ School PIN Gate (ยืนยันสิทธิ์, ปฏิเสธ PIN ผิด)
2. การค้นหาใบหน้าแบบ In-Memory Zero-Disk
3. การดึง Thumbnail WebP และ Full-res Photo
4. การดาวน์โหลดรูปชุดเป็น ZIP
5. ระบบขอใช้สิทธิลบรูปภาพตามกฎหมาย PDPA (Report Photo)
6. การทำงานของเครื่องมือผู้ดูแลระบบ (Admin Moderation & Hide Photo)
"""
import io
import os
import sys
import time
import zipfile
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
TEST_SELFIE = BASE / "data" / "input" / "ming.jpeg"
SERVER_URL = "http://localhost:8000"
TEST_PIN = "RUPRAO2026"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}")


def test_suite():
    print("=" * 70)
    print("🧪 เริ่มต้นการทดสอบ RupRao Production End-to-End Test Suite")
    print("=" * 70)

    # 1. ตรวจสอบว่าเซิร์ฟเวอร์เปิดอยู่
    log("1️⃣ ตรวจสอบการตอบสนองของเซิร์ฟเวอร์...")
    try:
        r = requests.get(f"{SERVER_URL}/api/stats", timeout=5)
        assert r.status_code == 200, f"Status code ผิดปกติ: {r.status_code}"
        stats = r.json()
        log(f"   ✅ เซิร์ฟเวอร์พร้อมทำงาน! รูปทั้งหมด: {stats['photos']} รูป, ใบหน้า: {stats['faces']} หน้า")
    except Exception as e:
        log(f"   ❌ ไม่สามารถเชื่อมต่อเซิร์ฟเวอร์ได้ ({e}) กรุณาเปิด server.py ก่อน")
        sys.exit(1)

    # 2. ทดสอบระบบความปลอดภัย / สถานะการยืนยันตัวตน
    log("2️⃣ ตรวจสอบสถานะการยืนยันตัวตน...")
    r = requests.get(f"{SERVER_URL}/api/auth/status")
    assert r.status_code == 200
    pin_required = r.json().get("pin_required", False)
    if pin_required:
        log("   🔒 ตรวจพบการเปิดใช้งาน School PIN Gate (ต้องใส่รหัสผ่าน)")
        # ส่ง PIN ผิด
        r = requests.post(f"{SERVER_URL}/api/auth/verify", json={"pin": "WRONG_PIN"})
        assert r.status_code == 401
        log("   ✅ ปฏิเสธ PIN ที่ไม่ถูกต้อง (401 Unauthorized) สำเร็จ")
        # ส่ง PIN ถูก
        r = requests.post(f"{SERVER_URL}/api/auth/verify", json={"pin": TEST_PIN})
        assert r.status_code == 200
        log("   ✅ ยืนยัน PIN ถูกต้อง (200 OK) สำเร็จ")
    else:
        log("   🔓 โหมดเปิดเสรี (ไม่ต้องใส่รหัสผ่าน) ตามการตั้งค่าปัจจุบัน")

    # 3. ทดสอบการค้นหาใบหน้า (Search API)
    log("3️⃣ ทดสอบการค้นหาใบหน้าผ่าน RAM (In-Memory Search)...")
    if not TEST_SELFIE.exists():
        log(f"   ⚠️ ไม่พบไฟล์ภาพทดสอบ {TEST_SELFIE}")
        sys.exit(1)

    with open(TEST_SELFIE, "rb") as f:
        headers = {"X-School-PIN": TEST_PIN} if pin_required else {}
        r = requests.post(
            f"{SERVER_URL}/api/search",
            headers=headers,
            files={"selfie": f},
            data={"threshold": 0.35}
        )
    assert r.status_code == 200, f"Search ล้มเหลว: {r.text}"
    data = r.json()
    assert data["success"] is True
    results = data["results"]
    log(f"   ✅ ค้นหาสำเร็จ! พบรูปภาพที่ตรงกัน {len(results)} รูป")
    for res in results[:3]:
        log(f"      • ID #{res['photo_id']} ({res['event_name']}): {res['similarity_percent']}% ({res['confidence_label']})")

    assert len(results) > 0, "ต้องพบรูปทดสอบอย่างน้อย 1 รูป"
    sample_pid = results[0]["photo_id"]

    # 4. ทดสอบการดึง Thumbnail WebP และ Original Photo
    log("4️⃣ ทดสอบการดึงรูปภาพ Thumbnail และ Original Photo...")
    r_thumb = requests.get(f"{SERVER_URL}/api/thumb/{sample_pid}")
    assert r_thumb.status_code == 200, "ไม่สามารถดึง Thumbnail ได้"
    assert "image/webp" in r_thumb.headers.get("Content-Type", ""), "Thumbnail ควรเป็น WebP"
    log(f"   ✅ ดึงรูป Thumbnail WebP สำเร็จ (ขนาด: {len(r_thumb.content):,} bytes)")

    r_orig = requests.get(f"{SERVER_URL}/api/photo/{sample_pid}")
    assert r_orig.status_code == 200, "ไม่สามารถดึง Original Photo ได้"
    log(f"   ✅ ดึงรูปภาพต้นฉบับสำเร็จ (ขนาด: {len(r_orig.content):,} bytes)")

    # 5. ทดสอบการดาวน์โหลดชุดรูปเป็น ZIP
    log("5️⃣ ทดสอบการสร้างไฟล์ ZIP ดาวน์โหลดในคลิกเดียว...")
    ids_str = ",".join(str(r["photo_id"]) for r in results[:2])
    download_headers = {"X-School-PIN": TEST_PIN} if pin_required else {}
    r_zip = requests.get(f"{SERVER_URL}/api/download?ids={ids_str}", headers=download_headers)
    assert r_zip.status_code == 200, "ดาวน์โหลด ZIP ล้มเหลว"
    assert "application/zip" in r_zip.headers.get("Content-Type", ""), "ต้องเป็น ZIP"
    with zipfile.ZipFile(io.BytesIO(r_zip.content)) as z:
        namelist = z.namelist()
        log(f"   ✅ โหลด ZIP สำเร็จ! มีไฟล์ข้างใน {len(namelist)} ไฟล์: {namelist}")

    # 6. ทดสอบระบบ PDPA ขอลบรูปภาพ (Report Photo)
    log("6️⃣ ทดสอบระบบรับคำขอลบรูปภาพตามกฎหมาย PDPA...")
    report_payload = {
        "photo_id": sample_pid,
        "reason": "ทดสอบระบบขอใช้สิทธิลบรูปภาพ PDPA (Unit Test)",
        "contact_info": "test@student.school.ac.th"
    }
    r_rep = requests.post(
        f"{SERVER_URL}/api/report_photo",
        headers={"X-School-PIN": TEST_PIN} if pin_required else {},
        json=report_payload
    )
    assert r_rep.status_code == 200, f"Report ล้มเหลว: {r_rep.text}"
    rep_data = r_rep.json()
    report_id = rep_data["report_id"]
    log(f"   ✅ บันทึกคำร้องขอลบรูปภาพสำเร็จ! Report ID: #{report_id}")

    # 7. ทดสอบ Moderation: ซ่อนรูปภาพและตรวจสอบผล
    log("7️⃣ ทดสอบฟังก์ชัน Moderation ซ่อนรูปภาพ...")
    import db
    conn = db.get_connection("data/faces.db")
    db.update_report_status(conn, report_id, status="approved", hide_photo=True)
    log("   ✅ อนุมัติคำขอลบและซ่อนรูปภาพในฐานข้อมูลเรียบร้อย")

    # ตรวจสอบว่ารูปที่ซ่อนถูกกรองออกจากผลการค้นหาหรือไม่
    with open(TEST_SELFIE, "rb") as f:
        r_after = requests.post(
            f"{SERVER_URL}/api/search",
            headers={"X-School-PIN": TEST_PIN},
            files={"selfie": f},
            data={"threshold": 0.35}
        )
    after_ids = [res["photo_id"] for res in r_after.json()["results"]]
    assert sample_pid not in after_ids, "รูปที่ถูกสั่งซ่อนต้องไม่ปรากฏในผลค้นหา"
    log(f"   ✅ ยืนยัน: รูป ID #{sample_pid} ไม่แสดงในผลการค้นหาอีกต่อไป")

    # คืนสถานะรูปภาพกลับมาเพื่อให้ข้อมูลทดสอบสมบูรณ์
    db.set_photo_hidden(conn, sample_pid, is_hidden=0)
    db.update_report_status(conn, report_id, status="rejected", hide_photo=False)
    log(f"   ♻️ คืนสถานะรูป ID #{sample_pid} ให้พร้อมใช้งานเหมือนเดิมเรียบร้อย")

    print("\n" + "=" * 70)
    print("🎉 ผลการทดสอบ: ผ่านฉลุย 100% ทุกรายการ! ระบบ Production พร้อมเปิดใช้งานจริง")
    print("=" * 70)


if __name__ == "__main__":
    test_suite()
