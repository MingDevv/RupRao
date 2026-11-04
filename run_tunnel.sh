#!/bin/bash
# run_tunnel.sh — เปิดเซิร์ฟเวอร์ RupRao สู่โลกภายนอกด้วย Cloudflare Tunnel (ฟรี 100% มี HTTPS)
# โดยไม่ต้องสมัครบัญชี ไม่ต้องฟอร์เวิร์ดพอร์ตเราเตอร์ และไม่ต้องจ่ายค่าโฮสติ้ง

set -e
export PATH="$HOME/.local/bin:$PATH"

PORT=${1:-8000}

echo "=========================================================="
echo "🌐 RupRao Cloudflare Tunnel Launcher"
echo "=========================================================="

if ! command -v cloudflared &> /dev/null; then
    echo "⚠️  ยังไม่พบโปรแกรม cloudflared ในเครื่อง"
    echo "💡 กำลังตรวจสอบคำสั่งติดตั้ง..."
    if command -v brew &> /dev/null; then
        echo "🍺 พบ Homebrew! สั่ง: brew install cloudflared"
        brew install cloudflared
    else
        echo "❌ กรุณาติดตั้ง cloudflared ก่อนใช้งาน หรือดาวน์โหลดจาก:"
        echo "   https://github.com/cloudflare/cloudflared/releases"
        exit 1
    fi
fi

echo "🚀 กำลังเปิด Tunnel เชื่อมโยงพอร์ต $PORT สู่ HTTPS สาธารณะ..."
echo "📌 เพื่อนๆ ในโรงเรียนสามารถเข้าใช้งานผ่านลิงก์ HTTPS ที่ปรากฏด้านล่างนี้ได้ทันที:"
echo "----------------------------------------------------------"
cloudflared tunnel --url "http://localhost:$PORT"
