#!/usr/bin/env bash
# One-time setup for desktest.py: installs Playwright into ~/.kip, not into any
# project, so desk tests work against any site regardless of its stack.
#
# Playwright always runs on the Node version pinned in .nvmrc next to this
# script, via nvm, never on the machine's default Node. This may download:
# that Node version (if nvm doesn't have it), @playwright/test from npm, and a
# Chromium build (~150 MB, unless a matching one is cached).
#
#   bash setup.sh                               # latest Playwright, pinned Node
#   PLAYWRIGHT_VERSION=1.63.0 bash setup.sh     # pin Playwright too
set -e
HERE=$(cd "$(dirname "$0")" && pwd -P)
KIP_HOME="${KIP_HOME:-$HOME/.kip}"
NODE_VERSION=$(cat "$HERE/.nvmrc")
VERSION="${PLAYWRIGHT_VERSION:-latest}"
NVM_DIR="${NVM_DIR:-$HOME/.nvm}"

if [ ! -s "$NVM_DIR/nvm.sh" ]; then
  echo "nvm not found at $NVM_DIR; install it (https://github.com/nvm-sh/nvm) or set NVM_DIR" >&2
  exit 1
fi
# nvm is a shell function and isn't safe under `set -u`; load it without the user's profile.
. "$NVM_DIR/nvm.sh"
nvm install "$NODE_VERSION" >/dev/null
NODE=$(nvm which "$NODE_VERSION")
export PATH="$(dirname "$NODE"):$PATH"

mkdir -p "$KIP_HOME"
cd "$KIP_HOME"
[ -f package.json ] || printf '{ "private": true }\n' > package.json
npm install --save-exact --no-fund --no-audit "@playwright/test@$VERSION"
npx playwright install chromium
echo "node $("$NODE" --version) at $NODE"
npx playwright --version
