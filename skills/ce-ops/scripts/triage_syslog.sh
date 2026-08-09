#!/usr/bin/env bash
# triage_syslog.sh — one-command sweep of a `messages` / Salt `minion` log.
#
# The syslog runs 40k+ lines and every viewer truncates the middle, so the
# only safe read strategy is: sweep anchors → pick a transition line →
# read a sed window around it. This script automates the sweep and prints
# the exact window command to run next.
#
# Usage:
#   bash triage_syslog.sh FILE                      # anchor sweep
#   bash triage_syslog.sh FILE --ip 10.0.2.201      # sweep, node-filtered
#   bash triage_syslog.sh FILE --window 18342       # sed window around line
#   bash triage_syslog.sh FILE --window 18342 --ctx 80
#
# Read-only: never modifies the input file. No dependencies beyond
# grep / sed / awk / wc (present on any triage box).

set -euo pipefail

FILE="${1:-}"
[[ -z "$FILE" || ! -f "$FILE" ]] && {
  echo "usage: $0 FILE [--ip IP] [--window LINENO] [--ctx N]" >&2; exit 1;
}
shift || true

IP="" ; WINDOW="" ; CTX=40
while [[ $# -gt 0 ]]; do
  case "$1" in
    --ip)     IP="$2";     shift 2 ;;
    --window) WINDOW="$2"; shift 2 ;;
    --ctx)    CTX="$2";    shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 1 ;;
  esac
done

TOTAL=$(wc -l < "$FILE")
echo "# file: $FILE  (${TOTAL} lines)"

# ---- window mode ------------------------------------------------------------
if [[ -n "$WINDOW" ]]; then
  START=$(( WINDOW > CTX ? WINDOW - CTX : 1 ))
  END=$(( WINDOW + CTX ))
  echo "# window: lines ${START}..${END} (center ${WINDOW}, ctx ${CTX})"
  sed -n "${START},${END}p" "$FILE"
  exit 0
fi

# ---- sweep mode -------------------------------------------------------------
# Anchor groups mirror Reference C §"grep/sed anchors" in the skill.
declare -A ANCHORS=(
  [DB_STATE]='DOWN/HARDSTOP|DOWN/TDMAINT|PDE is not operational|Cannot open PDE device'
  [RECONFIG]='tosstate|run_tpareconfig|tpareconfig|is_normal|Event 13912|Event 13895|failed reconcile'
  [VCONFIG]='Vconfig GDO|expand vconfig|node_num|retcode 61|vprocmanager|tdinfo'
  [BYNET]='BYNET|lost contact|eth0-udp-1033|eth0-udp-1034'
  [SALT]='salt-master|salt-minion|healthcheck'
  [AUTOSCALE]='ce-autoscaler|scale-up'
  [NOISE]='Unable to locate credentials|unrecognised disk label|Org not found'
)
ORDER=(DB_STATE RECONFIG VCONFIG BYNET SALT AUTOSCALE NOISE)

show_group() {
  local name="$1" pattern="$2" matches count
  if [[ -n "$IP" ]]; then
    matches=$(grep -nE "$pattern" "$FILE" | grep -F "$IP" || true)
  else
    matches=$(grep -nE "$pattern" "$FILE" || true)
  fi
  count=$(printf '%s' "$matches" | grep -c . || true)
  echo ""
  echo "== ${name}  (${count} matches) =="
  if [[ "$count" -eq 0 ]]; then return 0; fi
  # First 3 and last 3 matches, trimmed — enough to spot the transition line.
  if [[ "$count" -le 6 ]]; then
    printf '%s\n' "$matches" | cut -c1-200
  else
    {
      printf '%s\n' "$matches" | head -3
      echo "  ... ($((count - 6)) more)"
      printf '%s\n' "$matches" | tail -3
    } | cut -c1-200
  fi
}

for g in "${ORDER[@]}"; do
  show_group "$g" "${ANCHORS[$g]}"
done

echo ""
echo "# NOISE group lines are usually NOT the root cause - see TROUBLESHOOTING.md."
echo "# Next: pick the transition line number L (state change / first HARDSTOP /"
echo "#       Event 13912) and run:"
echo "#   bash $0 $FILE --window L"
