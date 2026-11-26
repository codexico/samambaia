#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"

mkdir -p "$HOME/.local/bin"
mkdir -p "$HOME/.config/samambaia"
mkdir -p "$HOME/.config/systemd/user"
mkdir -p "$HOME/.local/share/applications"

chmod +x "$PROJECT_DIR/bin/samambaia"
chmod +x "$PROJECT_DIR/bin/aaaia"
chmod +x "$PROJECT_DIR/bin/samambaia-selection-translate"

ln -sf "$PROJECT_DIR/bin/samambaia" "$HOME/.local/bin/samambaia"
ln -sf "$PROJECT_DIR/bin/aaaia" "$HOME/.local/bin/aaaia"
ln -sf "$PROJECT_DIR/bin/samambaia-selection-translate" "$HOME/.local/bin/samambaia-selection-translate"

# Render systemd user units with current checkout path
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/samambaia.service.in" \
  > "$HOME/.config/systemd/user/samambaia.service"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/samambaia-zk-sync.service.in" \
  > "$HOME/.config/systemd/user/samambaia-zk-sync.service"
cp -f "$PROJECT_DIR/systemd/samambaia-zk-sync.timer" "$HOME/.config/systemd/user/samambaia-zk-sync.timer"

# Render desktop launchers
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/desktop/samambaia-webui.desktop.in" \
  > "$HOME/.local/share/applications/samambaia-webui.desktop"
sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/desktop/samambaia-translate-selection.desktop.in" \
  > "$HOME/.local/share/applications/samambaia-translate-selection.desktop"

# Register non-conflicting KDE Plasma 6 shortcut (Meta+Alt+T) if KDE is present
if command -v kwriteconfig6 >/dev/null 2>&1; then
  kwriteconfig6 --file kglobalshortcutsrc \
    --group services \
    --group samambaia-translate-selection.desktop \
    --key _launch "Meta+Alt+T,Meta+Alt+T,Samambaia: Translate or Explain Selection"
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

systemctl --user daemon-reload
systemctl --user enable --now samambaia.service
systemctl --user enable --now samambaia-zk-sync.timer

echo "✔ Installed CLI commands: ~/.local/bin/samambaia and ~/.local/bin/aaaia"
echo "✔ Installed desktop launcher: Samambaia (Open WebUI)"
echo "✔ Registered KDE global shortcut: Super + Alt + T (Meta+Alt+T)"
echo "✔ Enabled systemd user services: samambaia.service & samambaia-zk-sync.timer"
