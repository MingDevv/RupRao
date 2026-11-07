// auth.js — ล็อกอินด้วยบัญชี Google ของโรงเรียน (Firebase Authentication)
// ใช้ร่วมกันทั้งหน้าค้นหา (index.html) และหน้าแอดมิน
//
// หลักการ: Firebase ทำหน้าที่แค่ "พิสูจน์ว่าเป็นเจ้าของบัญชี Google" แล้วส่ง ID token ให้เซิร์ฟเวอร์
// เซิร์ฟเวอร์เป็นคนตรวจโดเมน @โรงเรียน และออก session cookie เอง (เชื่อฝั่งเซิร์ฟเวอร์เท่านั้น)

const FIREBASE_SDK = 'https://www.gstatic.com/firebasejs/10.12.2';

let authModule = null;
let firebaseAuth = null;

async function ensureFirebase(config) {
  if (firebaseAuth) return;
  const { initializeApp } = await import(`${FIREBASE_SDK}/firebase-app.js`);
  authModule = await import(`${FIREBASE_SDK}/firebase-auth.js`);
  const app = initializeApp(config);
  firebaseAuth = authModule.getAuth(app);
  // ใช้ sessionStorage: redirect flow ต้องการให้ Firebase จำ "งานล็อกอินที่ค้าง" ข้ามหน้า
  // (inMemoryPersistence จะหายตอนหน้ารีโหลดหลังกลับจาก Google — อ่านผล redirect ไม่ได้)
  // ส่วน session จริงของแอปอยู่ที่ cookie ของเซิร์ฟเวอร์ตามเดิม
  await authModule.setPersistence(firebaseAuth, authModule.browserSessionPersistence);
}

// แอปในตัว LINE / Facebook / Instagram — Google ไม่อนุญาตให้ล็อกอินในเบราว์เซอร์แบบนี้
function isInAppBrowser() {
  const ua = navigator.userAgent || '';
  return /\bLine\/|FBAN|FBAV|FB_IAB|Instagram|MicroMessenger/i.test(ua);
}

function friendlyError(err) {
  const code = (err && err.code) || '';
  if (code === 'auth/popup-closed-by-user' || code === 'auth/cancelled-popup-request') {
    return 'ยกเลิกการเข้าสู่ระบบ';
  }
  if (code === 'auth/popup-blocked') {
    return 'เบราว์เซอร์บล็อกหน้าต่างล็อกอิน กรุณาอนุญาต pop-up แล้วลองใหม่';
  }
  if (code === 'auth/unauthorized-domain') {
    return 'โดเมนนี้ยังไม่ได้รับอนุญาตใน Firebase (Authentication → Settings → Authorized domains)';
  }
  if (code === 'auth/network-request-failed') {
    return 'เชื่อมต่ออินเทอร์เน็ตไม่ได้ กรุณาลองใหม่';
  }
  return (err && err.message) || 'เข้าสู่ระบบไม่สำเร็จ กรุณาลองใหม่';
}

async function signIn(status) {
  if (!status || !status.firebase) throw new Error('ระบบล็อกอินยังไม่เปิดใช้งาน');
  if (isInAppBrowser()) {
    throw new Error('กรุณาเปิดลิงก์นี้ใน Chrome หรือ Safari (แตะเมนู ⋯ แล้วเลือก "เปิดในเบราว์เซอร์") — Google ไม่อนุญาตให้ล็อกอินในแอป LINE / Facebook');
  }
  try {
    await ensureFirebase(status.firebase);
    const provider = new authModule.GoogleAuthProvider();
    // hd = แนะนำให้ Google แสดงเฉพาะบัญชีโรงเรียน (เป็นแค่คำใบ้ เซิร์ฟเวอร์ตรวจซ้ำอีกที)
    provider.setCustomParameters({ hd: status.allowed_domain || '', prompt: 'select_account' });
    // ใช้ redirect แทน popup: ทำงานได้ทั้ง webview / มือถือ และไม่โดน popup blocker
    // หน้าจะถูกพาไปหน้าล็อกอิน Google แล้ววนกลับมาที่ URL เดิม
    // ผลลัพธ์ถูกเก็บด้วย completeRedirectSignIn() ที่ถูกเรียกตอนโหลดหน้า
    await authModule.signInWithRedirect(firebaseAuth, provider);
    return { started: true };
  } catch (err) {
    throw new Error(friendlyError(err));
  }
}

// เรียกตอนโหลดหน้าทุกครั้ง: ถ้าเพิ่งกลับมาจากหน้าล็อกอิน Google
// จะเปลี่ยน ID token เป็น session cookie ให้เอง แล้วคืน { ok: true, data }
// ถ้าไม่มี redirect ค้าง คืน { ok: false } เงียบๆ
async function completeRedirectSignIn() {
  try {
    if (!authModule || !firebaseAuth) return { ok: false };
    const result = await authModule.getRedirectResult(firebaseAuth);
    if (!result || !result.user) return { ok: false };

    const idToken = await result.user.getIdToken();
    const res = await fetch('/api/auth/session', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ idToken }),
    });
    const data = await res.json().catch(() => ({}));
    await authModule.signOut(firebaseAuth).catch(() => {});
    if (!res.ok || !data.success) {
      return { ok: false, error: data.error || data.detail || 'เข้าสู่ระบบไม่สำเร็จ' };
    }
    return { ok: true, data };
  } catch (err) {
    const code = (err && err.code) || '';
    if (code === 'auth/no-auth-event') return { ok: false };
    console.warn('redirect sign-in:', err);
    return { ok: false, error: friendlyError(err) };
  }
}

async function signOutSession() {
  try {
    await fetch('/api/auth/logout', { method: 'POST' });
  } catch (e) {}
}

window.RupRaoAuth = { signIn, completeRedirectSignIn, signOut: signOutSession, isInAppBrowser };
