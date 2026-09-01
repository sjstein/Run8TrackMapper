#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# One-time droplet provisioning for the Run8 Track Mapper (P1, single server).
#
# Run on a fresh Ubuntu LTS droplet as a sudo-capable user:
#     sudo bash deploy/bootstrap.sh
#
# It is idempotent-ish (safe to re-run). It:
#   * installs python3, git, caddy
#   * creates the run8map service account and /opt/run8map layout
#   * opens the firewall (22/80/443 only)
# It does NOT deploy code/data or start the service — the RUNBOOK covers those
# steps (they need files copied from your Windows box and your real secrets).
# ---------------------------------------------------------------------------
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
	echo "Run with sudo: sudo bash deploy/bootstrap.sh" >&2
	exit 1
fi

echo "==> apt update + base packages (python3, git, ufw, curl)"
apt-get update -y
apt-get install -y python3 git ufw curl debian-keyring debian-archive-keyring apt-transport-https

echo "==> install Caddy (official apt repo)"
if ! command -v caddy >/dev/null 2>&1; then
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
		| gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
	curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
		> /etc/apt/sources.list.d/caddy-stable.list
	apt-get update -y
	apt-get install -y caddy
else
	echo "    caddy already installed: $(caddy version)"
fi

echo "==> create service account 'run8map' (no login shell)"
if ! id run8map >/dev/null 2>&1; then
	useradd --system --create-home --home-dir /opt/run8map --shell /usr/sbin/nologin run8map
fi

echo "==> /opt/run8map layout"
install -d -o run8map -g run8map /opt/run8map
install -d -o run8map -g run8map /opt/run8map/app       # code + generated output land here
install -d -o run8map -g run8map /opt/run8map/world     # writable upload slot dir

echo "==> seed the env file (edit it with your real token!)"
if [[ ! -f /opt/run8map/run8map.env ]]; then
	TOKEN=$(openssl rand -hex 32 2>/dev/null || echo replace-with-a-long-random-secret)
	printf 'RUN8_UPLOAD_TOKEN=%s\n' "$TOKEN" > /opt/run8map/run8map.env
	chown root:run8map /opt/run8map/run8map.env
	chmod 640 /opt/run8map/run8map.env
	echo "    wrote /opt/run8map/run8map.env with a generated token."
	echo "    >>> copy this token into the Windows agent.ini:  $TOKEN"
else
	echo "    /opt/run8map/run8map.env already exists — leaving it."
fi

echo "==> firewall (allow OpenSSH + 80 + 443; deny the rest)"
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
ufw status verbose

echo "==> log dir for caddy (owned by the caddy service user)"
# Always create it, then chown to the caddy user if that account exists (it does,
# from the caddy install above). The previous "|| install -d" fallback could leave
# the dir root-owned, which makes caddy fail at startup with EACCES on its log file.
install -d -m 755 /var/log/caddy
if id caddy >/dev/null 2>&1; then
	chown caddy:caddy /var/log/caddy
fi

cat <<'DONE'

==> bootstrap complete.

Next (see deploy/RUNBOOK.md for the full walkthrough):
  1. Point DNS: A record www.b2fengineering.com -> this droplet's IP.
  2. Deploy code:   git clone the repo into /opt/run8map/app
  3. Deploy data:   copy output/socal/, db_railvehicles.db, areas_socal.ini from Windows
  4. chown -R run8map:run8map /opt/run8map
  5. Install units: deploy/run8map.service -> /etc/systemd/system, deploy/Caddyfile -> /etc/caddy/Caddyfile
  6. Start:         systemctl enable --now run8map ; systemctl reload caddy
DONE
