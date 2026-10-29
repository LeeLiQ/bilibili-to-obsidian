#!/usr/bin/env bash
# macOS：注册 launchd 定时任务，每 N 分钟拉一次收藏夹。
# launchd 不继承 shell 的环境变量，所以 API key 从钥匙串读，不写进任何文件：
#   security add-generic-password -a "$USER" -s bili-triage-llm -w '<你的 key>'
# 用法（项目根目录）：  bash scripts/install-launchd.sh [分钟=30] [key 的环境变量名=OPENAI_API_KEY]
set -euo pipefail

MIN="${1:-30}"
KEY_ENV="${2:-OPENAI_API_KEY}"
PROJ="$(cd "$(dirname "$0")/.." && pwd)"
PY="$(command -v python3)"
[ -x "$PROJ/.venv/bin/python" ] && PY="$PROJ/.venv/bin/python"
HOME_DIR="$HOME/.bili-triage"
WRAP="$HOME_DIR/run-fav.sh"
PLIST="$HOME/Library/LaunchAgents/local.bili-triage.fav.plist"
mkdir -p "$HOME_DIR" "$HOME/Library/LaunchAgents"

cat > "$WRAP" <<EOF
#!/usr/bin/env bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
export $KEY_ENV="\$(security find-generic-password -a "\$USER" -s bili-triage-llm -w)"
cd "$PROJ"
exec "$PY" -m bili_triage fav
EOF
chmod +x "$WRAP"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>local.bili-triage.fav</string>
  <key>ProgramArguments</key><array><string>$WRAP</string></array>
  <key>StartInterval</key><integer>$((MIN * 60))</integer>
  <key>RunAtLoad</key><true/>
  <key>StandardOutPath</key><string>$HOME_DIR/fav.log</string>
  <key>StandardErrorPath</key><string>$HOME_DIR/fav.log</string>
</dict></plist>
EOF

launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "已注册 local.bili-triage.fav，每 $MIN 分钟一次。日志：$HOME_DIR/fav.log"
echo "停用：launchctl bootout gui/\$(id -u) $PLIST"
