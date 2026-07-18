#!/bin/bash
# システムアップデート一括実行 & Discord通知スクリプト

# ==========================================
# 設定項目
# ==========================================
# 環境変数ファイル (.env) から設定を読み込む
# スクリプトと同じディレクトリ、または親ディレクトリの .env を探します
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

echo "=== System Update Started ==="
send_discord_notification "🔄 [${SERVER_NAME}] サーバーのシステムアップデートを開始しました。"

# パッケージリスト更新
sudo apt-get update -y

# インタラクティブなプロンプトを出さずにアップグレードを強制実行
# 設定ファイルの競合が発生した場合は、既存のファイルを維持する設定（--force-confold）にしています
sudo DEBIAN_FRONTEND=noninteractive apt-get dist-upgrade -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"

# 不要パッケージ・キャッシュのクリーンアップ
sudo apt-get autoremove -y
sudo apt-get clean

# 1. OSの再起動が必要か確認
REBOOT_REQUIRED=false
if [ -f /var/run/reboot-required ]; then
  REBOOT_REQUIRED=true
fi

# 2. needrestartによる再起動が必要なプロセスのチェック
NEEDRESTART_SERVICES=""
if command -v needrestart >/dev/null 2>&1; then
  # 再起動が必要なサービスをリスト表示 (-r l: list mode)
  # 警告や不要な出力を抑えるため標準エラーは捨てる
  NEEDRESTART_SERVICES=$(sudo needrestart -r l 2>/dev/null | grep -E "Required|Service" || true)
fi

echo "=== System Update Completed ==="

# 通知メッセージの構築
NOTIFICATION_MSG="✅ [${SERVER_NAME}] サーバーのシステムアップデートが完了しました。\n"

if [ "$REBOOT_REQUIRED" = true ]; then
  NOTIFICATION_MSG="${NOTIFICATION_MSG}\n⚠️ **OSの再起動が必要です！**\n以下のコマンドを実行してサーバーを再起動してください。\n\`\`\`bash\nsudo reboot\n\`\`\`"
elif [ -n "$NEEDRESTART_SERVICES" ]; then
  NOTIFICATION_MSG="${NOTIFICATION_MSG}\nℹ️ **一部のサービス・プロセスで再起動が必要です:**\n\`\`\`\n${NEEDRESTART_SERVICES}\n\`\`\`\n\`needrestart\` コマンドなどを用いて、該当サービスを再起動してください。"
else
  NOTIFICATION_MSG="${NOTIFICATION_MSG}\n✨ OSやサービスの再起動は必要ありません。最新の状態です。"
fi

send_discord_notification "$NOTIFICATION_MSG"
