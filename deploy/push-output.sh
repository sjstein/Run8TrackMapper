#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# push-output.sh — deploy regenerated map output to the live droplet (Path A).
#
# Run from the Windows dev box in Git Bash, after you've regenerated locally:
#     python output_generator.py config-socal.ini --production
#     deploy/push-output.sh
#
# It tars output/socal (plus db/areas if you ask), scp's it to the droplet, and
# extracts it into the served app dir as the run8map user. No service restart is
# needed — serve.py serves the files fresh (Cache-Control: no-cache).
#
# See deploy/UPDATING.md for the full picture (Path A vs Path B).
#
# Options:
#   --db            also push db_railvehicles.db   (only if it changed)
#   --areas         also push areas_socal.ini      (only if it changed)
#   --all           push output/socal + db + areas
#   --host U@H      override the SSH target (default: josh@www.b2fengineering.com)
#   --app-dir DIR   override the remote app dir     (default: /opt/run8map/app)
#   --dry-run       print what would happen, do nothing
#   -h, --help      this help
#
# Env overrides: RUN8_HOST, RUN8_APP_DIR, RUN8_SERVICE_USER
# ---------------------------------------------------------------------------
set -euo pipefail

HOST="${RUN8_HOST:-josh@www.b2fengineering.com}"
APP_DIR="${RUN8_APP_DIR:-/opt/run8map/app}"
SERVICE_USER="${RUN8_SERVICE_USER:-run8map}"

want_db=0 want_areas=0 dry_run=0

while [[ $# -gt 0 ]]; do
	case "$1" in
		--db) want_db=1 ;;
		--areas) want_areas=1 ;;
		--all) want_db=1; want_areas=1 ;;
		--host) HOST="$2"; shift ;;
		--app-dir) APP_DIR="$2"; shift ;;
		--dry-run) dry_run=1 ;;
		-h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		*) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
	esac
	shift
done

# Resolve the repo root from this script's location, so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Build the list of repo-relative paths to ship (always the generated output).
paths=(output/socal)
[[ $want_db -eq 1 ]] && paths+=(db_railvehicles.db)
[[ $want_areas -eq 1 ]] && paths+=(areas_socal.ini)

# Verify each exists locally before we bother the droplet.
for p in "${paths[@]}"; do
	if [[ ! -e "$REPO_ROOT/$p" ]]; then
		echo "ERROR: $p not found under $REPO_ROOT — did you regenerate?" >&2
		exit 1
	fi
done

PUBLIC_HOST="${HOST#*@}"                 # strip user@ for the verify URL
STAMP="$(date +%Y%m%d-%H%M%S)"
REMOTE_TAR="/tmp/run8map-output-$STAMP.tgz"
LOCAL_TAR="$(mktemp -t run8map-output-XXXXXX).tgz"

echo "Deploying to : $HOST:$APP_DIR"
echo "Including     : ${paths[*]}"

if [[ $dry_run -eq 1 ]]; then
	echo "[dry-run] tar -C $REPO_ROOT ${paths[*]}  ->  scp -> $HOST:$REMOTE_TAR"
	echo "[dry-run] ssh -t $HOST: sudo tar xzf $REMOTE_TAR -C $APP_DIR ; sudo chown -R $SERVICE_USER:$SERVICE_USER $APP_DIR"
	rm -f "$LOCAL_TAR"
	exit 0
fi

echo "==> packing"
tar czf "$LOCAL_TAR" -C "$REPO_ROOT" "${paths[@]}"
echo "    $(du -h "$LOCAL_TAR" | cut -f1) tarball"

echo "==> uploading"
scp -q "$LOCAL_TAR" "$HOST:$REMOTE_TAR"

echo "==> extracting on the droplet (you may be prompted for your sudo password)"
# -t: allocate a tty so the remote sudo can prompt. Extract, fix ownership, clean up.
ssh -t "$HOST" "sudo tar xzf '$REMOTE_TAR' -C '$APP_DIR' \
	&& sudo chown -R '$SERVICE_USER:$SERVICE_USER' '$APP_DIR' \
	&& rm -f '$REMOTE_TAR' \
	&& echo '    extracted into $APP_DIR'"

rm -f "$LOCAL_TAR"

echo "==> verifying"
curl -sS --max-time 20 "https://$PUBLIC_HOST/api/ping" && echo
echo "Done. Reload https://$PUBLIC_HOST/ (a normal refresh revalidates; no restart needed)."

# NOTE: tar extract overwrites/adds files but does NOT delete ones removed since the
# last deploy. If you renamed/removed regions and want a clean slate, first run on the
# droplet:  sudo rm -rf /opt/run8map/app/output/socal   then re-run this script.
