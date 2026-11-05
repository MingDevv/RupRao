"""ทดสอบโหมดล็อกอินเมลโรงเรียน (เปิด server ด้วย .env ที่ตั้ง Firebase ไว้ก่อน แล้วรัน: venv312/bin/python test_auth.py)"""
import sys
import time

from pathlib import Path
BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
from dotenv import load_dotenv
load_dotenv(BASE / ".env")

import requests
import auth_session

S = "http://localhost:8000"
SELFIE = BASE / "data" / "raw" / "CRMS6_Students" / "1124927876848212.jpg"
ok = True


def check(label, got, want):
    global ok
    mark = "✅" if got == want else "❌"
    if got != want:
        ok = False
    print(f"{mark} {label}: ได้ {got} (ต้องการ {want})")


def cookie(email):
    return {auth_session.SESSION_COOKIE: auth_session.make_session_value(email, "test")}


st = requests.get(f"{S}/api/auth/status").json()
check("auth_mode", st["auth_mode"], "firebase")
check("admin_mode", st["admin_mode"], "email")
check("ส่ง firebase config ให้หน้าเว็บ", st["firebase"]["projectId"], "ruprao-55170")

print("\n— ไม่ล็อกอิน —")
check("ค้นหา", requests.post(f"{S}/api/search", files={"selfie": open(SELFIE, "rb")}).status_code, 401)
check("thumbnail", requests.get(f"{S}/api/thumb/76").status_code, 401)
check("รูปต้นฉบับ", requests.get(f"{S}/api/photo/76").status_code, 401)
check("ZIP", requests.get(f"{S}/api/download?ids=76").status_code, 401)
check("หน้าแอดมิน API", requests.get(f"{S}/api/admin/overview").status_code, 401)

print("\n— cookie ปลอม / token ปลอม —")
forged = auth_session.make_session_value("std016588@tesaban6.ac.th")[:-4] + "abcd"
check("cookie แก้ลายเซ็น", requests.get(f"{S}/api/thumb/76", cookies={auth_session.SESSION_COOKIE: forged}).status_code, 401)
check("cookie อีเมลนอกโรงเรียน (@gmail.com)", requests.get(f"{S}/api/thumb/76", cookies=cookie("someone@gmail.com")).status_code, 401)
r = requests.post(f"{S}/api/auth/session", json={"idToken": "eyJhbGciOiJSUzI1NiJ9.fake.fake"})
check("ID token ปลอม", r.status_code, 403)
print("   ข้อความ:", r.json().get("error"))
check("ไม่ส่ง token", requests.post(f"{S}/api/auth/session", json={}).status_code, 403)

print("\n— นักเรียนทั่วไปที่ล็อกอินแล้ว —")
student = cookie("std999999@tesaban6.ac.th")
r = requests.post(f"{S}/api/search", files={"selfie": open(SELFIE, "rb")}, cookies=student)
check("ค้นหา", r.status_code, 200)
print("   พบ", r.json().get("total_found"), "รูป")
check("thumbnail", requests.get(f"{S}/api/thumb/76", cookies=student).status_code, 200)
check("status รู้ว่าเป็นใคร", requests.get(f"{S}/api/auth/status", cookies=student).json()["user"]["email"], "std999999@tesaban6.ac.th")
check("นักเรียนเข้าแอดมิน", requests.get(f"{S}/api/admin/overview", cookies=student).status_code, 403)
check("นักเรียนลองใช้ PIN login", requests.post(f"{S}/api/admin/login", json={"pin": "x"}, cookies=student).status_code, 400)

print("\n— แอดมินตามรายชื่อ —")
admin = cookie("STD016588@tesaban6.ac.th")   # ตัวพิมพ์ใหญ่ก็ต้องผ่าน
check("แอดมินเข้า overview", requests.get(f"{S}/api/admin/overview", cookies=admin).status_code, 200)
check("status is_admin", requests.get(f"{S}/api/auth/status", cookies=admin).json()["is_admin"], True)
check("หน้า /admin โหลดได้", requests.get(f"{S}/admin").status_code, 200)

print("\n— ออกจากระบบ —")
r = requests.post(f"{S}/api/auth/logout", cookies=admin)
check("logout", r.status_code, 200)
check("logout สั่งลบ cookie", "ruprao_session=" in r.headers.get("set-cookie", "") and "Max-Age=0" in r.headers.get("set-cookie", ""), True)

print("\n— cookie หมดอายุ —")
orig = auth_session.SESSION_MAX_AGE
auth_session.SESSION_MAX_AGE = -10
expired = cookie("std016588@tesaban6.ac.th")
auth_session.SESSION_MAX_AGE = orig
check("cookie หมดอายุ", requests.get(f"{S}/api/thumb/76", cookies=expired).status_code, 401)

print("\nผลรวม:", "ผ่านทั้งหมด 🎉" if ok else "มีข้อที่ไม่ผ่าน ❌")
sys.exit(0 if ok else 1)
