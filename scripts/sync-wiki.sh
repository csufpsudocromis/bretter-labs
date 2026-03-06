#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIKI_REMOTE="${WIKI_REMOTE:-https://github.com/csufpsudocromis/bretter-labs.wiki.git}"
WIKI_BRANCH="${WIKI_BRANCH:-master}"
SOURCE_WIKI_DIR="$ROOT_DIR/docs/wiki"
ARCHITECTURE_SOURCE="$ROOT_DIR/docs/architecture.md"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [ ! -d "$SOURCE_WIKI_DIR" ]; then
  fail "Missing source wiki directory: $SOURCE_WIKI_DIR"
fi

if [ ! -f "$ARCHITECTURE_SOURCE" ]; then
  fail "Missing architecture source file: $ARCHITECTURE_SOURCE"
fi

if ! command -v git >/dev/null 2>&1; then
  fail "git is required"
fi

auth_remote="$WIKI_REMOTE"
if [ -n "${GITHUB_PAT:-}" ] && [[ "$WIKI_REMOTE" == https://github.com/* ]]; then
  auth_remote="${WIKI_REMOTE/https:\/\/github.com\//https:\/\/x-access-token:${GITHUB_PAT}@github.com/}"
fi

work_dir="$(mktemp -d /tmp/bretter-wiki-sync.XXXXXX)"
cleanup() {
  rm -rf "$work_dir"
}
trap cleanup EXIT

echo "Cloning wiki repo..."
git clone --quiet --branch "$WIKI_BRANCH" "$auth_remote" "$work_dir"

echo "Syncing wiki pages..."
cp "$ARCHITECTURE_SOURCE" "$work_dir/Architecture.md"
for page in "$SOURCE_WIKI_DIR"/*.md; do
  cp "$page" "$work_dir/$(basename "$page")"
done

sidebar_file="$work_dir/_Sidebar.md"
sidebar_entry="- [VM Image Formats](VM-Image-Formats)"
if [ -f "$sidebar_file" ] && ! grep -Fq -- "$sidebar_entry" "$sidebar_file"; then
  awk -v entry="$sidebar_entry" '
    { print }
    $0 ~ /\[Storage and Image Pipeline\]/ { print entry }
  ' "$sidebar_file" > "${sidebar_file}.tmp"
  mv "${sidebar_file}.tmp" "$sidebar_file"
fi

cd "$work_dir"
git add Architecture.md *.md

if git diff --cached --quiet; then
  echo "No wiki changes to push."
  exit 0
fi

git config user.name "${GIT_AUTHOR_NAME:-bbenson}"
git config user.email "${GIT_AUTHOR_EMAIL:-bbenson@local}"

commit_message="${WIKI_COMMIT_MESSAGE:-docs: sync wiki from repository docs}"
git commit -m "$commit_message"

echo "Pushing wiki updates..."
git push origin "$WIKI_BRANCH"
echo "Wiki sync complete."
