#!/usr/bin/env bash
# Phlox-md-reader -- install ONLY the files the skill needs (30 files, ~380 KB).
#
#   1) from an unpacked copy of the repo (no network needed):
#        bash install-skill.sh
#   2) from anywhere (downloads the repo zip first, ~1.7 MB):
#        bash install-skill.sh -n
#
# Options: -t <target-dir>   default ~/.dsh/skills/phlox-md-reader
#          -m gitee         use the Gitee mirror for the zip download
#          -l               lite: skip the bundled KaTeX (~550 KB), formulas stay raw
#          -f               overwrite a non-empty target
set -u

TARGET="$HOME/.dsh/skills/phlox-md-reader"
MIRROR="github"
NET=0
FORCE=0
LITE=0

while getopts "t:m:nlf" opt; do
  case "$opt" in
    t) TARGET="$OPTARG" ;;
    m) MIRROR="$OPTARG" ;;
    n) NET=1 ;;
    l) LITE=1 ;;
    f) FORCE=1 ;;
    *) echo "usage: bash install-skill.sh [-t target] [-m github|gitee] [-n] [-l] [-f]"; exit 2 ;;
  esac
done

die() { echo "$*" >&2; exit 1; }

if [ "$NET" -eq 0 ]; then
  SRC="$(cd "$(dirname "$0")" && pwd)"
  if [ ! -f "$SRC/skill-files.txt" ]; then
    echo "skill-files.txt not found here; switching to network mode."
    NET=1
  fi
fi

if [ "$NET" -eq 1 ]; then
  TMPD="$(mktemp -d)"
  ZIP="$TMPD/repo.zip"
  if [ "$MIRROR" = "gitee" ]; then
    URLS="https://gitee.com/cric_qin/phlox-md-reader/repository/archive/master.zip"
  else
    URLS="https://codeload.github.com/qinpai/phlox-md-reader/zip/refs/heads/master https://gitee.com/cric_qin/phlox-md-reader/repository/archive/master.zip"
  fi
  ok=0
  for u in $URLS; do
    echo "  downloading $u"
    if curl -fsSL --retry 3 --connect-timeout 20 -o "$ZIP" "$u" && [ "$(wc -c <"$ZIP")" -gt 100000 ]; then ok=1; break; fi
  done
  [ "$ok" -eq 1 ] || die "cannot download the repo zip (network?). try -m gitee"
  unzip -q "$ZIP" -d "$TMPD/x" || die "unzip failed"
  SRC="$(find "$TMPD/x" -maxdepth 1 -type d -name 'phlox-md-reader*' | head -1)"
  [ -n "$SRC" ] || die "unexpected zip layout"
fi

echo
echo "Phlox-md-reader skill installer"
echo "  source : $SRC"
echo "  target : $TARGET"
if [ "$LITE" -eq 1 ]; then
  MANIFEST="$SRC/skill-files-lite.txt"
  echo "  flavor : lite (no KaTeX, formulas stay raw)"
else
  MANIFEST="$SRC/skill-files.txt"
  echo "  flavor : full (offline KaTeX included)"
fi
[ -f "$MANIFEST" ] || die "manifest not found: $MANIFEST"

if [ -d "$TARGET" ] && [ -n "$(ls -A "$TARGET" 2>/dev/null)" ] && [ "$FORCE" -eq 0 ]; then
  die "target exists and is not empty: $TARGET  (use -f to overwrite, or -t other-dir)"
fi

total=0; copied=0; missing=""
# note: `|| [ -n "$rel" ]` keeps the last manifest line when the file has no trailing newline
while IFS= read -r rel || [ -n "$rel" ]; do
  [ -z "$rel" ] && continue
  total=$((total+1))
  if [ -f "$SRC/$rel" ]; then
    mkdir -p "$TARGET/$(dirname "$rel")"
    cp -f "$SRC/$rel" "$TARGET/$rel"
    copied=$((copied+1))
  else
    missing="$missing $rel"
  fi
done < "$MANIFEST"

echo "  files  : $total"
echo "  copied : $copied"
[ -n "$missing" ] && echo "  missing:$missing"

if [ "$NET" -eq 1 ]; then rm -rf "$TMPD"; fi

echo
echo "Done -> $TARGET"
echo
echo "Next steps:"
echo "  cd \"$TARGET\""
echo "  pip install -r requirements.txt"
echo "  python mdread.py doctor"
echo
echo "Optional chemistry (structures from SMILES):"
echo "  python install_indigo.py --install"