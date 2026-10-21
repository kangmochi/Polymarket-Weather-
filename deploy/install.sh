#!/usr/bin/env bash
# Install this paper-bot clone: virtualenv, data directory, and a systemd timer.
# The timer runs `weatherbot scan` every 30 minutes. It does not place live orders.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INSTALL_DIR="${INSTALL_DIR:-$ROOT}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"

if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)'; then
  echo "Python 3.11 or newer is required." >&2
  exit 1
fi

if [[ ! -f "$INSTALL_DIR/pyproject.toml" ]]; then
  echo "No project at $INSTALL_DIR." >&2
  echo "Clone the repository, then run this script from that clone." >&2
  exit 1
fi

if [[ "${WEATHERBOT_INSTALL_CHECK_ONLY:-}" == "1" ]]; then
  echo "Install directory: $INSTALL_DIR"
  exit 0
fi

python3 -m venv "$INSTALL_DIR/.venv"
"$INSTALL_DIR/.venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/.venv/bin/pip" install -e "$INSTALL_DIR"
mkdir -p "$INSTALL_DIR/data"

sed_escape() {
  printf '%s' "$1" | sed -e 's/[\\&|]/\\&/g'
}

render_unit() {
  local name="$1"
  local rendered
  rendered="$(mktemp)"
  sed \
    -e "s|@INSTALL_DIR@|$(sed_escape "$INSTALL_DIR")|g" \
    -e "s|@SERVICE_USER@|$(sed_escape "$SERVICE_USER")|g" \
    "$INSTALL_DIR/deploy/$name" > "$rendered"
  if [[ "$(id -u)" -eq 0 ]]; then
    cp "$rendered" "/etc/systemd/system/$name"
  else
    sudo cp "$rendered" "/etc/systemd/system/$name"
  fi
  rm -f "$rendered"
}

render_unit polymarket-weather-bot.service
render_unit polymarket-weather-bot.timer
if [[ "$(id -u)" -eq 0 ]]; then
  systemctl daemon-reload
  systemctl enable --now polymarket-weather-bot.timer
else
  sudo systemctl daemon-reload
  sudo systemctl enable --now polymarket-weather-bot.timer
fi

echo "Installed in $INSTALL_DIR"
echo "First scan:"
if [[ "$(id -un)" == "$SERVICE_USER" ]]; then
  "$INSTALL_DIR/.venv/bin/weatherbot" scan \
    --stations "$INSTALL_DIR/config/stations.yaml" \
    --paper "$INSTALL_DIR/config/paper.yaml"
else
  runuser -u "$SERVICE_USER" -- "$INSTALL_DIR/.venv/bin/weatherbot" scan \
    --stations "$INSTALL_DIR/config/stations.yaml" \
    --paper "$INSTALL_DIR/config/paper.yaml"
fi
echo "Timer log: journalctl -u polymarket-weather-bot.service -n 50"
