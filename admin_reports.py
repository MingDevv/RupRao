# -*- coding: utf-8 -*-
"""
admin_reports.py — เครื่องมือจัดการคำขอลบรูปภาพ (PDPA Removal Requests & Moderation)
=============================================================================
คำสั่งที่ใช้งาน:
  python admin_reports.py list              -> ดูรายการคำขอลบรูปทั้งหมด
  python admin_reports.py list pending      -> ดูเฉพาะคำขอที่รอการตรวจสอบ
  python admin_reports.py approve <id>      -> อนุมัติคำขอลบ (ซ่อนรูปออกจากระบบค้นหาทันที)
  python admin_reports.py reject <id>       -> ปฏิเสธคำขอลบ
  python admin_reports.py hide <photo_id>   -> สั่งซ่อนรูปภาพทันที
  python admin_reports.py unhide <photo_id> -> ยกเลิกการซ่อนรูปภาพ
"""
import sys
from pathlib import Path
import db

BASE = Path(__file__).resolve().parent
DB_PATH = BASE / "data" / "faces.db"


def show_help():
    print("""
📌 การใช้งาน admin_reports.py:
  python admin_reports.py list [pending|approved|rejected]
  python admin_reports.py approve <report_id>
  python admin_reports.py reject <report_id>
  python admin_reports.py hide <photo_id>
  python admin_reports.py unhide <photo_id>
    """)


def main():
    if len(sys.argv) < 2:
        show_help()
        return

    cmd = sys.argv[1].lower()
    conn = db.get_connection(DB_PATH)

    if cmd == "list":
        status_filter = sys.argv[2] if len(sys.argv) > 2 else None
        reports = db.get_reports(conn, status_filter)
        if not reports:
            print(f"✨ ไม่พบคำขอลบรูปภาพ{' (สถานะ: ' + status_filter + ')' if status_filter else ''}")
            return

        print(f"\n📋 รายการคำขอลบรูปภาพทั้งหมด ({len(reports)} รายการ):")
        print("─" * 80)
        for r in reports:
            status_icon = "⏳" if r["status"] == "pending" else ("✅" if r["status"] == "approved" else "❌")
            hidden_tag = " [🚫 ซ่อนอยู่]" if r["is_hidden"] else ""
            print(f"ID #{r['id']} | สถานะ: {status_icon} {r['status']}{hidden_tag}")
            print(f"  รูปภาพ: ID {r['photo_id']} ({r['event_name']} / {r['rel_path']})")
            print(f"  เหตุผล: {r['reason']}")
            if r["contact_info"]:
                print(f"  ผู้ติดต่อ: {r['contact_info']}")
            print(f"  เวลาที่ส่ง: {r['created_at']}")
            print("─" * 80)

    elif cmd == "approve":
        if len(sys.argv) < 3:
            print("❌ กรุณาระบุ report_id เช่น: python admin_reports.py approve 1")
            return
        report_id = int(sys.argv[2])
        success = db.update_report_status(conn, report_id, status="approved", hide_photo=True)
        if success:
            print(f"✅ อนุมัติคำขอ #{report_id} เรียบร้อยแล้ว (รูปภาพถูกซ่อนจากการค้นหาและดาวน์โหลดแล้ว)")
        else:
            print(f"❌ ไม่พบคำขอ #{report_id}")

    elif cmd == "reject":
        if len(sys.argv) < 3:
            print("❌ กรุณาระบุ report_id เช่น: python admin_reports.py reject 1")
            return
        report_id = int(sys.argv[2])
        success = db.update_report_status(conn, report_id, status="rejected", hide_photo=False)
        if success:
            print(f"✅ ปฏิเสธคำขอ #{report_id} เรียบร้อยแล้ว")
        else:
            print(f"❌ ไม่พบคำขอ #{report_id}")

    elif cmd == "hide":
        if len(sys.argv) < 3:
            print("❌ กรุณาระบุ photo_id เช่น: python admin_reports.py hide 5")
            return
        photo_id = int(sys.argv[2])
        success = db.set_photo_hidden(conn, photo_id, is_hidden=1)
        if success:
            print(f"🚫 สั่งซ่อนรูปภาพ #{photo_id} สำเร็จ (จะไม่แสดงในผลการค้นหาอีกต่อไป)")
        else:
            print(f"❌ ไม่พบรูปภาพ #{photo_id}")

    elif cmd == "unhide":
        if len(sys.argv) < 3:
            print("❌ กรุณาระบุ photo_id เช่น: python admin_reports.py unhide 5")
            return
        photo_id = int(sys.argv[2])
        success = db.set_photo_hidden(conn, photo_id, is_hidden=0)
        if success:
            print(f"👁️ ยกเลิกการซ่อนรูปภาพ #{photo_id} สำเร็จ")
        else:
            print(f"❌ ไม่พบรูปภาพ #{photo_id}")

    else:
        show_help()


if __name__ == "__main__":
    main()
