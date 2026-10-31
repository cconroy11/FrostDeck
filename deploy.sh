#!/usr/bin/env bash
# Build the plugin and copy it to a Steam Deck over SSH, then restart Decky.
#
#   DECK_HOST=deck@192.168.1.94 ./deploy.sh
#
# Decky re-takes ownership of /home/deck/homebrew/plugins as root whenever it
# starts, so files are first synced to a staging folder the `deck` user owns
# and then moved into place with sudo.
#
# Requirements on the Deck: an SSH password set (`passwd` in Konsole) and
# Decky Loader installed. Requirements here: pnpm 9 (`npx pnpm@9` works).
set -euo pipefail

DECK_HOST="${DECK_HOST:-deck@steamdeck.local}"
PLUGIN_NAME="FrostDeck"
REMOTE_DIR="/home/deck/homebrew/plugins/${PLUGIN_NAME}"
STAGE_DIR="/home/deck/.cache/frostdeck-deploy"
cd "$(dirname "$0")"

if command -v pnpm >/dev/null 2>&1; then
  pnpm run build
else
  npx -y pnpm@9 run build
fi

# Assemble what the Deck gets in a local folder first.
LOCAL_STAGE="$(mktemp -d)"
trap 'rm -rf "${LOCAL_STAGE}"' EXIT
cp -R dist py_modules defaults assets main.py plugin.json package.json LICENSE README.md "${LOCAL_STAGE}/"
find "${LOCAL_STAGE}" -name '__pycache__' -prune -exec rm -rf {} +
# A personal CurseForge key in ./curseforge.key (gitignored) goes to your own Deck
# only. It is kept out of defaults/ because Decky's zip packaging copies that
# folder, which would put the key in a release build.
if [ -f curseforge.key ]; then
  cp curseforge.key "${LOCAL_STAGE}/defaults/curseforge.key"
  echo "Including your CurseForge key from ./curseforge.key"
fi

echo "Syncing to ${DECK_HOST}:${STAGE_DIR}"
if command -v rsync >/dev/null 2>&1; then
  ssh "${DECK_HOST}" "mkdir -p '${STAGE_DIR}'"
  rsync -az --delete "${LOCAL_STAGE}/" "${DECK_HOST}:${STAGE_DIR}/"
else
  # No rsync (Git Bash on Windows): stream a tarball over ssh into a fresh staging folder.
  tar -czf - -C "${LOCAL_STAGE}" . \
    | ssh "${DECK_HOST}" "rm -rf '${STAGE_DIR}' && mkdir -p '${STAGE_DIR}' && tar -xzf - -C '${STAGE_DIR}'"
fi

echo "Installing into ${REMOTE_DIR} and restarting Decky (sudo password needed)"
# Decky is stopped while files move so its plugin watcher cannot race the copy,
# and the result is size-checked against the staging copy before restarting.
ssh -t "${DECK_HOST}" "set -e
  sudo systemctl stop plugin_loader
  sudo rm -rf '${REMOTE_DIR}'
  sudo rm -rf /home/deck/homebrew/plugins/BlizzardDeck   # pre-rename install, if present
  sudo cp -a '${STAGE_DIR}' '${REMOTE_DIR}'
  sudo chown -R root:root '${REMOTE_DIR}'
  for f in main.py plugin.json package.json dist/index.js py_modules/catalog.py; do
    want=\$(stat -c %s '${STAGE_DIR}'/\$f); got=\$(stat -c %s '${REMOTE_DIR}'/\$f)
    if [ \"\$want\" != \"\$got\" ] || [ \"\$got\" = 0 ]; then
      echo \"COPY VERIFY FAILED: \$f is \$got bytes, expected \$want\"; sudo systemctl start plugin_loader; exit 1
    fi
  done
  echo 'Copy verified.'
  sudo systemctl start plugin_loader"
echo "Done. Open the Quick Access menu on the Deck and look for ${PLUGIN_NAME}."
