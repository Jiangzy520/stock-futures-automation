#!/usr/bin/env bash
set -euo pipefail

DEST_DIR="${DEST_DIR:-$HOME/Applications/rustdesk}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
APP_DIR="${APP_DIR:-$HOME/.local/share/applications}"
AUTOSTART_DIR="${AUTOSTART_DIR:-$HOME/.config/autostart}"

mkdir -p "$DEST_DIR" "$BIN_DIR" "$APP_DIR" "$AUTOSTART_DIR"

echo "[1/5] 获取 RustDesk 最新 AppImage 下载链接..."
ASSET_URL="$(
python3 - <<'PY'
import re
import urllib.request

url = "https://github.com/rustdesk/rustdesk/releases/latest"
with urllib.request.urlopen(url, timeout=30) as r:
    html = r.read().decode("utf-8", "ignore")

links = sorted(set(re.findall(r"/rustdesk/rustdesk/releases/download/[^\"']+?\\.AppImage", html)))
if not links:
    raise SystemExit("未找到 AppImage 下载链接")
print("https://github.com" + links[0])
PY
)"

ASSET_NAME="$(basename "$ASSET_URL")"
ASSET_PATH="$DEST_DIR/$ASSET_NAME"
LINK_PATH="$DEST_DIR/rustdesk.AppImage"

echo "[2/5] 下载: $ASSET_NAME"
curl -fL --retry 3 --connect-timeout 15 -o "$ASSET_PATH" "$ASSET_URL"
chmod +x "$ASSET_PATH"
ln -sfn "$ASSET_PATH" "$LINK_PATH"

echo "[3/5] 创建启动脚本..."
cat >"$BIN_DIR/rustdesk-launch" <<EOF
#!/usr/bin/env bash
set -euo pipefail
exec "$LINK_PATH" "\$@"
EOF
chmod +x "$BIN_DIR/rustdesk-launch"

echo "[4/5] 创建桌面图标与开机自启..."
cat >"$APP_DIR/rustdesk.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=RustDesk
Comment=Remote Desktop
Exec=$BIN_DIR/rustdesk-launch
Icon=rustdesk
Terminal=false
Categories=Network;RemoteAccess;
StartupNotify=true
EOF

cat >"$AUTOSTART_DIR/rustdesk.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=RustDesk
Comment=Remote Desktop
Exec=$BIN_DIR/rustdesk-launch --tray
Icon=rustdesk
Terminal=false
Categories=Network;RemoteAccess;
StartupNotify=false
X-GNOME-Autostart-enabled=true
EOF

echo "[5/5] 安装完成，检查版本..."
if "$BIN_DIR/rustdesk-launch" --version >/tmp/rustdesk_version.txt 2>/dev/null; then
  echo "RustDesk 版本: $(cat /tmp/rustdesk_version.txt)"
else
  echo "RustDesk 已安装（图形界面版本命令未返回，可直接从应用菜单启动 RustDesk）。"
fi

echo
echo "启动方式:"
echo "  1) 应用菜单搜索 RustDesk"
echo "  2) 命令行执行: $BIN_DIR/rustdesk-launch"

