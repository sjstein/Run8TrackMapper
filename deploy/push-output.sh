#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# push-output.sh — deploy regenerated map output to the live droplet (Path A).
#
# Run from the Windows dev box in Git Bash, after you've regenerated locally.
# Pass the SAME config you generated with — the paths to ship are read from it,
# so this is not tied to any one route (#84):
#     python output_generator.py config-snb-socal.ini --production
#     deploy/push-output.sh config-snb-socal.ini
#
# It reads [output] output_dir from the config and tars that directory (plus the
# config's db/areas if you ask), scp's it to the droplet, and extracts it into the
# served app dir as the run8map user. No service restart is needed — serve.py
# serves the files fresh (Cache-Control: no-cache).
#
# See deploy/UPDATING.md for the full picture (Path A vs Path B).
#
# Usage: push-output.sh <config.ini> [options]
#
# Options:
#   --db            also push the config's [visualization] railvehicle_db
#   --areas         also push the config's [visualization] areas_file(s)
#   --all           push output dir + db + areas
#   --host U@H      override the SSH target (default: josh@www.b2fengineering.com)
#   --app-dir DIR   override the remote app dir     (default: /opt/run8map/app)
#   --dry-run       print what would happen, do nothing
#   -h, --help      this help
#
# The output dir, areas file(s) and railvehicle db are all resolved from the
# config (relative to its directory) and must live under the repo root.
#
# Env overrides: RUN8_CONFIG, RUN8_HOST, RUN8_APP_DIR, RUN8_SERVICE_USER
# ---------------------------------------------------------------------------
set -euo pipefail

HOST="${RUN8_HOST:-josh@www.b2fengineering.com}"
APP_DIR="${RUN8_APP_DIR:-/opt/run8map/app}"
SERVICE_USER="${RUN8_SERVICE_USER:-run8map}"

want_db=0 want_areas=0 dry_run=0
CONFIG="${RUN8_CONFIG:-}"

while [[ $# -gt 0 ]]; do
	case "$1" in
		--db) want_db=1 ;;
		--areas) want_areas=1 ;;
		--all) want_db=1; want_areas=1 ;;
		--host) HOST="$2"; shift ;;
		--app-dir) APP_DIR="$2"; shift ;;
		--dry-run) dry_run=1 ;;
		-h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
		--*) echo "Unknown option: $1 (try --help)" >&2; exit 2 ;;
		*)  # positional: the config file to deploy from
			if [[ -n "$CONFIG" ]]; then
				echo "Unexpected extra argument: $1 (config already set to $CONFIG)" >&2; exit 2
			fi
			CONFIG="$1" ;;
	esac
	shift
done

if [[ -z "$CONFIG" ]]; then
	echo "ERROR: no config file given. Usage: push-output.sh <config.ini> [options]" >&2
	echo "       (or set RUN8_CONFIG). See --help." >&2
	exit 2
fi
if [[ ! -f "$CONFIG" ]]; then
	echo "ERROR: config file not found: $CONFIG" >&2
	exit 1
fi

# Resolve the repo root from this script's location, so it works from any CWD.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# Derive the repo-relative paths to ship from the config, so this script is not
# tied to any one route (#84). Python reads the config the same way the project
# does (plain configparser + strip): [output] output_dir is the generated output
# dir; [visualization] areas_file / railvehicle_db feed --areas / --db. Each is
# resolved relative to the config's directory, then re-expressed relative to the
# repo root (what we tar). A path outside the repo is an error.
derived_out="$(python - "$CONFIG" "$REPO_ROOT" <<'PY'
import sys, configparser
from pathlib import Path

cfg_path = Path(sys.argv[1]).resolve()
repo = Path(sys.argv[2]).resolve()
if not cfg_path.is_file():
    sys.stderr.write(f"config not found: {cfg_path}\n"); sys.exit(3)

cp = configparser.ConfigParser()
cp.read(cfg_path, encoding='utf-8')

def rel(raw):
    p = (cfg_path.parent / raw).resolve()
    try:
        return p.relative_to(repo).as_posix()
    except ValueError:
        sys.stderr.write(f"path is not under repo root ({repo}): {p}\n"); sys.exit(3)

out = cp.get('output', 'output_dir', fallback='').strip()
if not out:
    sys.stderr.write("[output] output_dir is missing from the config\n"); sys.exit(3)
print("OUTPUT\t" + rel(out))

areas = cp.get('visualization', 'areas_file', fallback='').strip()
for a in (x.strip() for x in areas.split(',') if x.strip()):
    print("AREAS\t" + rel(a))

db = cp.get('visualization', 'railvehicle_db', fallback='').strip()
if db:
    print("DB\t" + rel(db))
PY
)" || { echo "ERROR: could not read paths from $CONFIG" >&2; exit 1; }

out_path=""; areas_paths=(); db_path=""
while IFS=$'\t' read -r kind val; do
	kind="${kind%$'\r'}"; val="${val%$'\r'}"   # Windows python stdout adds CR to each line
	[[ -z "$kind" ]] && continue
	case "$kind" in
		OUTPUT) out_path="$val" ;;
		AREAS)  areas_paths+=("$val") ;;
		DB)     db_path="$val" ;;
	esac
done <<< "$derived_out"

paths=("$out_path")
if [[ $want_db -eq 1 ]]; then
	if [[ -z "$db_path" ]]; then
		echo "ERROR: --db requested but [visualization] railvehicle_db is not set in $CONFIG" >&2; exit 1
	fi
	paths+=("$db_path")
fi
if [[ $want_areas -eq 1 ]]; then
	if [[ ${#areas_paths[@]} -eq 0 ]]; then
		echo "ERROR: --areas requested but [visualization] areas_file is not set in $CONFIG" >&2; exit 1
	fi
	paths+=("${areas_paths[@]}")
fi

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
# last deploy. If you renamed/removed regions and want a clean slate, first remove the
# deployed output dir on the droplet (e.g. sudo rm -rf $APP_DIR/<output dir>, where the
# output dir is the [output] output_dir from your config), then re-run this script.
