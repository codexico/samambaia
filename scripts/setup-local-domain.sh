#!/usr/bin/env bash
# Optional helper (requires sudo) to expose http://samambaia.local on port 80 via systemd-socket-proxyd.
set -euo pipefail

if ! grep -q "samambaia.local" /etc/hosts; then
  echo "127.0.0.1 samambaia.local" | sudo tee -a /etc/hosts >/dev/null
  echo "✔ Added '127.0.0.1 samambaia.local' to /etc/hosts"
fi

mkdir -p "$HOME/.config/systemd/user"
sudo tee /etc/sysctl.d/99-samambaia-port80.conf >/dev/null <<'EOF'
net.ipv4.ip_unprivileged_port_start=80
EOF
sudo sysctl --system >/dev/null

cat > "$HOME/.config/systemd/user/samambaia-local-proxy.socket" <<'EOF'
[Unit]
Description=HTTP port 80 socket for http://samambaia.local

[Socket]
ListenStream=127.0.0.1:80

[Install]
WantedBy=sockets.target
EOF

cat > "$HOME/.config/systemd/user/samambaia-local-proxy.service" <<'EOF'
[Unit]
Description=Proxy http://samambaia.local (80 -> 8080 Open WebUI)
Requires=samambaia-local-proxy.socket
After=samambaia-local-proxy.socket

[Service]
ExecStart=/usr/lib/systemd/systemd-socket-proxyd 127.0.0.1:8080
EOF

systemctl --user daemon-reload
systemctl --user enable --now samambaia-local-proxy.socket
echo "✔ Ready! Open http://samambaia.local in Firefox or Chrome."
