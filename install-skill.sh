#!/usr/bin/env bash
# Phlox-md-reader -- install only the files the skill needs (30 files, ~380 KB).
# Usage:
#   bash install-skill.sh                                  # -> ~/.dsh/skills/phlox-md-reader
#   bash install-skill.sh -t /opt/skills/phlox-md-reader
#   bash install-skill.sh -m gitee                         # mirror (mainland China)
set -u

TARGET="$HOME/.dsh/skills/phlox-md-reader"
MIRROR="github"

while getopts "t:m:" opt; do
  case "$opt" in
    t) TARGET="$OPTARG" ;;
    m) MIRROR="$OPTARG" ;;
    *) echo "usage: bash install-skill.sh [-t target-dir] [-m github|gitee]"; exit 2 ;;
  esac
done

if [ "$MIRROR" = "gitee" ]; then
  BASE="https://gitee.com/cric_qin/phlox-md-reader/raw/master/"
  FALLBACK="https://raw.githubusercontent.com/qinpai/phlox-md-reader/master/"
else
  BASE="https://raw.githubusercontent.com/qinpai/phlox-md-reader/master/"
  FALLBACK="https://gitee.com/cric_qin/phlox-md-reader/raw/master/"
fi

fetch() {  # fetch <relpath> ; tries primary then mirror, 3 attempts each
  rel="$1"; dest="$TARGET/$rel"
  mkdir -p "$(dirname "$dest")"
  for url in "$BASE$rel" "$FALLBACK$rel"; do
    for i in 1 2 3; do
      if curl -fsSL --retry 2 --connect-timeout 20 -o "$dest" "$url"; then return 0; fi
      sleep 2
    done
  done
  return 1
}

echo "Phlox-md-reader skill installer"
echo "  source : $BASE"
echo "  target : $TARGET"
echo

MANIFEST="$(curl -fsSL --retry 3 "$BASEskill-files.txt" || curl -fsSL --retry 3 "${FALLBACK}skill-files.txt")"
if [ -z "$MANIFEST" ]; then echo "cannot fetch skill-files.txt"; exit 1; fi

total=$(printf '%s\n' "$MANIFEST" | grep -c .)
n=0; failed=""
while IFS= read -r rel; do
  [ -z "$rel" ] && continue
  n=$((n+1))
  printf '  [%2d/%2d] %s\n' "$n" "$total" "$rel"
  fetch "$rel" || failed="$failed $rel"
done <<EOF
$MANIFEST
EOF

echo
if [ -z "$failed" ]; then
  echo "Done: $total files -> $TARGET"
  echo
  echo "Next steps:"
  echo "  cd \"$TARGET\""
  echo "  pip install -r requirements.txt"
  echo "  python mdread.py doctor"
  echo
  echo "Optional chemistry (structures from SMILES):"
  echo "  python install_indigo.py --install"
else
  echo "Failed files:$failed"
  exit 1
fi