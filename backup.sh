#!/bin/bash
# =============================================================================
# backup.sh — สคริปต์สำรองข้อมูลฐานข้อมูลและ FAISS Index ของ RupRao
# =============================================================================
set -e

BASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="${BASE_DIR}/backups/${TIMESTAMP}"

echo "📦 กำลังเริ่มสำรองข้อมูล RupRao ไปที่: ${BACKUP_DIR} ..."
mkdir -p "${BACKUP_DIR}"

if [ -f "${BASE_DIR}/data/faces.db" ]; then
  cp "${BASE_DIR}/data/faces.db" "${BACKUP_DIR}/faces.db"
  echo "  ✅ สำรอง SQLite Database (faces.db) สำเร็จ"
fi

if [ -f "${BASE_DIR}/data/index.faiss" ]; then
  cp "${BASE_DIR}/data/index.faiss" "${BACKUP_DIR}/index.faiss"
  echo "  ✅ สำรอง FAISS Index (index.faiss) สำเร็จ"
fi

if [ -d "${BASE_DIR}/data/thumbs" ]; then
  mkdir -p "${BACKUP_DIR}/thumbs"
  cp -r "${BASE_DIR}/data/thumbs/"* "${BACKUP_DIR}/thumbs/" 2>/dev/null || true
  echo "  ✅ สำรอง Thumbnails สำเร็จ"
fi

echo "✨ สำรองข้อมูลเรียบร้อยแล้วทั้งหมดที่: ${BACKUP_DIR}"
