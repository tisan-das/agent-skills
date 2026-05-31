#!/usr/bin/env bash
# triage_syslog.sh — one-command sweep of a Teradata `messages` or Salt `minion`
# syslog for CE triage. These files run 40k+ lines and viewers truncate the
# middle, so this greps the high-signal anchors (with line numbers), groups them,
# and then lets you open a window around any line.
#
# Pure grep/sed/awk — no dependencies beyond a POSIX shell. Read-only on input.
#
# Usage:
#   bash triage_syslog.sh <file>                 # full anchor sweep
#   bash triage_syslog.sh <file> --ip 10.0.2.201 # sweep + a section of lines for that node
#   bash triage_syslog.sh <file> --window 36750  # print lines ~around 36750 (context)
#
# Workflow: run the sweep, find the transition line (where state changed) in the
# output, then re-run with --window <that line number> to read the surrounding
# sequence.

set -u

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "usage: bash triage_syslog.sh <messages|minion file> [--ip <addr>] [--window <lineno>]" >&2
  exit 2
fi
shift || true

IP=""
WINDOW=""
while [ $# -gt 0 ]; do
  case "$1" in
    --ip)     IP="${2:-}"; shift 2 ;;
    --window) WINDOW="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

# ---- window mode: just show context around a line and exit ------------------
if [ -n "$WINDOW" ]; then
  if ! printf '%s' "$WINDOW" | grep -qE '^[0-9]+$'; then
    echo "--window expects a line number" >&2; exit 2
  fi
  CTX=40
  START=$(( WINDOW > CTX ? WINDOW - CTX : 1 ))
  END=$(( WINDOW + CTX ))
  echo "== context: lines ${START}-${END} (around ${WINDOW}) of ${FILE} =="
  sed -n "${START},${END}p" "$FILE"
  exit 0
fi

TOTAL=$(wc -l < "$FILE" | tr -d ' ')
echo "== ${FILE} : ${TOTAL} lines =="
echo "   (find the transition line below, then: bash triage_syslog.sh ${FILE} --window <lineno>)"

# Helper: titled, line-numbered grep for an extended regex, capped for sanity.
group() {
  local title="$1" regex="$2" cap="${3:-40}"
  echo
  echo "--- ${title} ---"
  grep -nE "$regex" "$FILE" 2>/dev/null | head -n "$cap" || true
}

group "DBS / PDE state"        "HARDSTOP|DOWN/HARDSTOP|DOWN/TDMAINT|PDE is not operational|Cannot open PDE device"
group "Reconfigure / fatal events" "Event 13912|Event 13895|failed reconcile|run_tpareconfig|tpareconfig|is_normal|persistently failing"
group "vconfig / healthcheck"  "Vconfig GDO|vconfig|expand vconfig|expand|healthcheck"
group "BYNET"                  "BYNET|lost contact|eth0-udp-1033|eth0-udp-1034"
group "Salt orchestration"     "salt-master|salt-minion|tdinfo|vprocmanager|node_num|retcode 61"
group "Autoscaler / scale"     "ce-autoscaler|scale-up|scale_up|sustained"

# Optional: a dedicated section of lines mentioning a specific node IP. This is
# additive (it does NOT filter the groups above), because key events like
# "Event 13912" often don't carry the node IP.
if [ -n "$IP" ]; then
  echo
  echo "--- lines mentioning node ${IP} ---"
  grep -nF "$IP" "$FILE" 2>/dev/null | head -n 60 || true
fi

# Known-noise section, called out separately so it isn't mistaken for the cause.
echo
echo "--- known secondary noise (verify before blaming) ---"
grep -nE "Unable to locate credentials|unrecognised disk label|unrecognized disk label|Org not found" "$FILE" 2>/dev/null | head -n 20 || true
