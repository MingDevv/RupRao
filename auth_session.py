# -*- coding: utf-8 -*-
"""
auth_session.py — ล็อกอินด้วยบัญชี Google ของโรงเรียนผ่าน Firebase Authentication
==============================================================================
ลำดับการทำงาน:
1. หน้าเว็บให้ผู้ใช้ล็อกอิน Google ผ่าน Firebase JS SDK → ได้ "ID token"
2. หน้าเว็บส่ง ID token มาที่ POST /api/auth/session
3. เซิร์ฟเวอร์ตรวจลายเซ็น token กับกุญแจสาธารณะของ Google (ไม่ต้องใช้ service account)
   + ตรวจว่าอีเมลยืนยันแล้ว และลงท้ายด้วยโดเมนโรงเรียนเท่านั้น
4. ผ่านแล้ว → ออก session cookie ของเราเอง (ลงลายเซ็น HMAC) ใช้ต่อ 7 วัน

กติกาเหล็ก: ห้ามเชื่อการตรวจโดเมนฝั่งหน้าเว็บ — ตรวจที่เซิร์ฟเวอร์เท่านั้น
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from pathlib import Path
from typing import Optional

SESSION_COOKIE = "ruprao_session"
SESSION_MAX_AGE = 60 * 60 * 24 * 7   # 7 วัน

BASE = Path(__file__).resolve().parent
_SECRET_FILE = BASE / "data" / ".session_secret"
_secret_cache: Optional[bytes] = None


# ── การตั้งค่า (อ่านจาก .env ทุกครั้ง เพื่อให้ server.py โหลด dotenv ก่อนได้) ──

def firebase_project_id() -> str:
    return os.getenv("RUPRAO_FIREBASE_PROJECT_ID", "").strip()


def enabled() -> bool:
    """เปิดโหมดล็อกอินด้วยเมลโรงเรียนเมื่อตั้ง RUPRAO_FIREBASE_PROJECT_ID แล้ว"""
    return bool(firebase_project_id())


def allowed_domain() -> str:
    return os.getenv("RUPRAO_ALLOWED_EMAIL_DOMAIN", "tesaban6.ac.th").strip().lower().lstrip("@")


def admin_emails() -> set:
    raw = os.getenv("RUPRAO_ADMIN_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def web_config() -> dict:
    """ค่าที่หน้าเว็บต้องใช้เริ่ม Firebase SDK (ค่าพวกนี้ไม่ใช่ความลับ อยู่ในหน้าเว็บอยู่แล้ว)"""
    pid = firebase_project_id()
    return {
        "apiKey": os.getenv("RUPRAO_FIREBASE_API_KEY", "").strip(),
        "authDomain": os.getenv("RUPRAO_FIREBASE_AUTH_DOMAIN", f"{pid}.firebaseapp.com").strip(),
        "projectId": pid,
        "appId": os.getenv("RUPRAO_FIREBASE_APP_ID", "").strip(),
    }


def is_admin_email(email: Optional[str]) -> bool:
    return bool(email) and email.lower() in admin_emails()


# ── ตรวจ ID token จาก Firebase ──

class AuthError(Exception):
    """ข้อความใน exception นี้แสดงให้ผู้ใช้เห็นได้"""


def verify_firebase_id_token(token: str) -> dict:
    """ตรวจ ID token → คืน {email, name} ถ้าผ่านทุกเงื่อนไข ไม่งั้น raise AuthError"""
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token as google_id_token

    if not token or not isinstance(token, str):
        raise AuthError("ไม่พบข้อมูลการเข้าสู่ระบบ")
    try:
        # ตรวจลายเซ็น, วันหมดอายุ, issuer และ audience (= project ของเรา) ในคำสั่งเดียว
        claims = google_id_token.verify_firebase_token(
            token, google_requests.Request(), audience=firebase_project_id()
        )
    except Exception:
        raise AuthError("ยืนยันตัวตนไม่สำเร็จ กรุณาเข้าสู่ระบบใหม่อีกครั้ง")

    if not claims:
        raise AuthError("ยืนยันตัวตนไม่สำเร็จ กรุณาเข้าสู่ระบบใหม่อีกครั้ง")

    provider = (claims.get("firebase") or {}).get("sign_in_provider")
    if provider != "google.com":
        raise AuthError("กรุณาเข้าสู่ระบบด้วยบัญชี Google ของโรงเรียน")

    email = str(claims.get("email") or "").strip().lower()
    if not email or claims.get("email_verified") is not True:
        raise AuthError("บัญชีนี้ยังไม่ได้ยืนยันอีเมล")

    domain = allowed_domain()
    if not email.endswith("@" + domain):
        raise AuthError(f"ใช้ได้เฉพาะบัญชี @{domain} ของโรงเรียนเท่านั้น")

    return {"email": email, "name": str(claims.get("name") or "")[:80]}


# ── session cookie ที่ลงลายเซ็นเอง (กันปลอม cookie) ──

def _secret() -> bytes:
    """ใช้ RUPRAO_SESSION_SECRET ถ้าตั้งไว้ ไม่งั้นสุ่มครั้งแรกแล้วเก็บไว้ที่ data/.session_secret"""
    global _secret_cache
    if _secret_cache:
        return _secret_cache
    env_secret = os.getenv("RUPRAO_SESSION_SECRET", "").strip()
    if env_secret:
        _secret_cache = env_secret.encode()
        return _secret_cache
    if _SECRET_FILE.exists():
        _secret_cache = _SECRET_FILE.read_bytes().strip()
    else:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        value = secrets.token_hex(32).encode()
        _SECRET_FILE.write_bytes(value)
        try:
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
        _secret_cache = value
    return _secret_cache


def _sign(payload_b64: str) -> str:
    return hmac.new(_secret(), payload_b64.encode(), hashlib.sha256).hexdigest()


def make_session_value(email: str, name: str = "") -> str:
    payload = {"email": email.lower(), "name": name, "exp": int(time.time()) + SESSION_MAX_AGE}
    payload_b64 = base64.urlsafe_b64encode(json.dumps(payload, ensure_ascii=False).encode()).decode().rstrip("=")
    return f"{payload_b64}.{_sign(payload_b64)}"


def read_session_value(value: Optional[str]) -> Optional[dict]:
    """คืน {email, name} ถ้า cookie ถูกต้อง ยังไม่หมดอายุ และยังอยู่ในโดเมนที่อนุญาต ไม่งั้นคืน None"""
    if not value or "." not in value:
        return None
    payload_b64, sig = value.rsplit(".", 1)
    if not hmac.compare_digest(sig, _sign(payload_b64)):
        return None
    try:
        padded = payload_b64 + "=" * (-len(payload_b64) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()))
    except Exception:
        return None
    if int(payload.get("exp", 0)) < time.time():
        return None
    email = str(payload.get("email", ""))
    # ตรวจโดเมนซ้ำอีกชั้น เผื่อเปลี่ยน RUPRAO_ALLOWED_EMAIL_DOMAIN ทีหลัง
    if not email.endswith("@" + allowed_domain()):
        return None
    return {"email": email, "name": payload.get("name", "")}


def session_from_request(request) -> Optional[dict]:
    if not enabled():
        return None
    return read_session_value(request.cookies.get(SESSION_COOKIE))


def is_https(request) -> bool:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto.split(",")[0].strip() == "https"
