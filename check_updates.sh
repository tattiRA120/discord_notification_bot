#!/bin/bash
# システムアップデート検知 & Discord通知スクリプト (cron用)

# ==========================================
# 設定項目
# ==========================================
# 環境変数ファイル (.env) から設定を読み込む
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [ -f "${SCRIPT_DIR}/.env" ]; then
  source "${SCRIPT_DIR}/.env"
elif [ -f "${SCRIPT_DIR}/../.env" ]; then
  source "${SCRIPT_DIR}/../.env"
fi

# サーバー名（通知で識別しやすくするため）
SERVER_NAME="GCE-DiscordBot-Server"
# ==========================================

send_discord_notification() {
  local message="$1"
  if [ -n "$DISCORD_WEBHOOK_URL" ] && [ "$DISCORD_WEBHOOK_URL" != "YOUR_DISCORD_WEBHOOK_URL_HERE" ]; then
    # JSONのエスケープ処理
    local escaped_msg=$(echo "$message" | sed 's/"/\\"/g' | sed ':a;N;$!ba;s/\n/\\n/g')
    curl -s -H "Content-Type: application/json" -X POST -d "{\"content\": \"$escaped_msg\"}" "$DISCORD_WEBHOOK_URL" > /dev/null
  fi
}

# パッケージリスト更新
sudo apt-get update -y > /dev/null 2>&1

# アップグレード可能なパッケージ数を取得
# LC_ALL=C を指定することで、確実に出力を英語にしてパースしやすくします
UPGRADE_LINE=$(LC_ALL=C apt-get -s upgrade | grep -E "^[0-9]+ upgraded" || true)
UPGRADABLE_COUNT=$(echo "$UPGRADE_LINE" | awk '{print $1}' || echo "0")

if [ -n "$UPGRADABLE_COUNT" ] && [ "$UPGRADABLE_COUNT" -gt 0 ]; then
  # アップグレード可能パッケージの詳細リストを取得 (最大10件を表示)
  UPGRADABLE_LIST=$(apt list --upgradable 2>/dev/null | grep -v -E "Listing...|一覧表示中..." | head -n 10 || true)
  
  NOTIFICATION_MSG="🔔 [${SERVER_NAME}] サーバーに適用可能なシステムアップデートがあります。\n\n**アップデート可能なパッケージ数**: ${UPGRADABLE_COUNT} 件\n\n\`\`\`\n${UPGRADABLE_LIST}\n\`\`\`\n※上記は一部のみ表示されている場合があります。\n\n本番サーバーにログインし、以下のコマンドを実行してアップデートを適用してください：\n\`\`\`bash\n~/update_system.sh\n\`\`\`"
  send_discord_notification "$NOTIFICATION_MSG"
fi
