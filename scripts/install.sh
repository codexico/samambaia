#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")/.." && pwd)"
WITH_ZK=false
for arg in "$@"; do
  if [[ "$arg" == "--with-zk" ]]; then
    WITH_ZK=true
  fi
done

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

# Read configured container names and KDE shortcut (falling back to defaults)
read -r OLLAMA_CONTAINER OPENWEBUI_CONTAINER KDE_SHORTCUT ZK_ENABLED < <(
  "$PROJECT_DIR/.venv/bin/python" -c '
from samambaia.config import load_config
c = load_config()
print(c.ollama_container_name, c.openwebui_container_name, c.kde_selection_shortcut, str(c.zettelkasten.enabled).lower())
'
)

# Render core systemd daemon unit
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__OLLAMA_CONTAINER__|$OLLAMA_CONTAINER|g" \
    -e "s|__OPENWEBUI_CONTAINER__|$OPENWEBUI_CONTAINER|g" \
  "$PROJECT_DIR/systemd/samambaia.service.in" \
  > "$HOME/.config/systemd/user/samambaia.service"

# Render desktop launchers
sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
  "$PROJECT_DIR/desktop/samambaia-webui.desktop.in" \
  > "$HOME/.local/share/applications/samambaia-webui.desktop"

sed -e "s|__PROJECT_DIR__|$PROJECT_DIR|g" \
    -e "s|__KDE_SHORTCUT__|$KDE_SHORTCUT|g" \
  "$PROJECT_DIR/desktop/samambaia-translate-selection.desktop.in" \
  > "$HOME/.local/share/applications/samambaia-translate-selection.desktop"

if command -v kwriteconfig6 >/dev/null 2>&1; then
  kwriteconfig6 --file kglobalshortcutsrc \
    --group services \
    --group samambaia-translate-selection.desktop \
    --key _launch "${KDE_SHORTCUT},${KDE_SHORTCUT},Samambaia: Translate or Explain Selection"
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$HOME/.local/share/applications" || true
command -v kbuildsycoca6 >/dev/null 2>&1 && kbuildsycoca6 --noincremental >/dev/null 2>&1 || true

systemctl --user daemon-reload
systemctl --user enable --now samambaia.service

# Optional Zettelkasten timer (only enabled when --with-zk is passed or zettelkasten.enabled=true)
if [[ "$WITH_ZK" == "true" || "$ZK_ENABLED" == "true" ]]; then
  sed "s|__PROJECT_DIR__|$PROJECT_DIR|g" "$PROJECT_DIR/systemd/samambaia-zk-sync.service.in" \
    > "$HOME/.config/systemd/user/samambaia-zk-sync.service"
  cp -f "$PROJECT_DIR/systemd/samambaia-zk-sync.timer" "$HOME/.config/systemd/user/samambaia-zk-sync.timer"
  systemctl --user daemon-reload
  systemctl --user enable --now samambaia-zk-sync.timer
  echo "✔ Optional Zettelkasten sync timer enabled (samambaia-zk-sync.timer)"
fi

echo "✔ Installed CLI commands: ~/.local/bin/samambaia and ~/.local/bin/aaaia"
echo "✔ Installed desktop launcher: Samambaia (Open WebUI)"
echo "✔ Registered KDE global shortcut: $KDE_SHORTCUT"
echo "✔ Enabled systemd user service: samambaia.service"
