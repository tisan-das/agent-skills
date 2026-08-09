#!/usr/bin/env python3
"""
parse_cloudwatch.py — stdlib-only parser for CloudWatch Logs Insights JSON
exports (logs-insights-results__NN_.json) used in Teradata CE triage.

Design constraints — do NOT "improve" these away:
  * stdlib only. The triage box has no jq, no pip, no network.
  * `@message` arrives as a dict OR a plain string in the same export.
    text_of() is the single normalization point; every view goes through it.
  * Bulk `List*` inventory entries (ListComputeEngineConfigs response,
    count: 214, huge `items` array) mention every CE and site in the fleet.
    They MUST be skipped before ID filtering or they produce false positives.
  * Console exports are reverse-chronological. Records are ALWAYS re-sorted
    to chronological order before display.
  * CE/site dual-filtering runs against the raw serialized record, so it
    catches IDs buried anywhere (nested fields, free-text strings, ARNs).

Views (mutually exclusive; default is --stats):
  --stats             summary: counts, span, level histogram, top ce_siteids
  --timeline          per-record chronological event lines
  --errors            ERROR/FATAL levels + error-pattern matches
  --grep PATTERN      case-insensitive regex over the raw record text
  --metering          decode the triple-nested SQS→SNS→Message envelope;
                      flag healthy/critical flaps and redelivery
  --show N            dump full JSON of record [N] from the current listing

Filters (combine with any view):
  --ce CE_ID          keep records mentioning this CE (e.g. CEAM<CE_ID>)
  --site SITE_ID      keep records mentioning this site (e.g. TDICAM<SITE_ID>)
                      --ce and --site together match EITHER (dual-filter, OR)
  --no-skip           disable the bulk List* skip (debug only)

Typical calls:
  python3 parse_cloudwatch.py export.json --ce CEAM<CE_ID> --timeline
  python3 parse_cloudwatch.py export.json --ce CEAM<CE_ID> --errors
  python3 parse_cloudwatch.py export.json --grep 'INVALID_ARGUMENT|/infrastructure'
  python3 parse_cloudwatch.py export.json --metering
  python3 parse_cloudwatch.py export.json --ce CEAM<CE_ID> --show 12

Diagnostics go to stderr; view output goes to stdout (safe to pipe).
Exit codes: 0 ok, 1 usage/load error, 2 no records survived the filters.
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone

# Triage boxes run under cp1252 (Windows) or LC_ALL=C, where the em-dashes and
# ellipses below raise UnicodeEncodeError mid-report and lose the output.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="backslashreplace")
    except (AttributeError, ValueError):
        pass

# --- Skip / classification patterns -----------------------------------------

# Bulk inventory noise: "ListComputeEngineConfigs response", "ListSites request"…
BULK_RE = re.compile(r"\bList[A-Z][A-Za-z]*\s*(?:request|response)\b")
# Belt-and-braces: anything enormous carrying an items array is inventory too.
BULK_SIZE_BYTES = 50_000

ERROR_TEXT_RE = re.compile(
    r"(?i)\b(error|fail(?:ed|ure)?|timed?[ _-]?out|exception|denied|"
    r"invalid_argument|hardstop|panic|fatal)\b"
)
ERROR_LEVELS = {"ERROR", "FATAL", "CRITICAL", "PANIC"}

METERING_HINT_RE = re.compile(r"ApproximateReceiveCount|sqsEvent|TopicArn")

TS_KEYS = ("@timestamp", "timestamp", "@ingestionTime", "time")
LEVEL_KEYS = ("level", "@level", "severity", "log.level")
MSG_KEYS = ("msg", "message", "@message_text", "event")
EXTRA_KEYS = ("error_code", "connectivity", "state", "stage", "status_code", "count")

TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S.%f",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
)

EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


# --- Core helpers ------------------------------------------------------------

def text_of(msg):
    """Grep-able string for @message, whether it arrived as dict or string.

    This is THE dict-vs-string normalization point. Every filter and view
    must go through it; bypassing it is how parsers crash on real exports.
    """
    if isinstance(msg, dict):
        return json.dumps(msg, separators=(",", ":"), default=str)
    if msg is None:
        return ""
    return str(msg)


def as_dict(msg):
    """Best-effort structured view of @message.

    Returns the dict itself, or json.loads() of a string that looks like
    JSON, or None. Never raises — a triage parser must not die on one
    malformed record out of 100k.
    """
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, str):
        s = msg.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                d = json.loads(s)
                return d if isinstance(d, dict) else None
            except (json.JSONDecodeError, ValueError):
                return None
    return None


def parse_ts(raw):
    """Parse a CloudWatch timestamp into an aware UTC datetime.

    Unparseable/absent timestamps sort to the epoch (start of listing) and
    are flagged in --stats, rather than crashing or being dropped.
    """
    if raw is None:
        return EPOCH
    if isinstance(raw, (int, float)):  # epoch millis or seconds
        v = float(raw)
        if v > 1e12:
            v /= 1000.0
        try:
            return datetime.fromtimestamp(v, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return EPOCH
    s = str(raw).strip()
    zs = s[:-1] + "+00:00" if s.endswith("Z") else s
    for fmt in TS_FORMATS:
        for candidate in (zs, s):
            try:
                dt = datetime.strptime(candidate, fmt)
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)
            except ValueError:
                continue
    return EPOCH


def fmt_ts(dt, raw):
    if dt == EPOCH and raw is not None:
        return f"??[{str(raw)[:23]}]"
    return dt.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + "Z"


def load_records(path):
    """Load and normalize an export into a list of dict records.

    Accepts the three shapes seen in the wild:
      1. [ {"@timestamp": ..., "@message": ...}, ... ]           (dict rows)
      2. {"results": [...]} / {"records": [...]} wrappers
      3. [ [ {"field": "@timestamp", "value": ...}, ... ], ... ] (field/value rows)
    """
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        data = json.load(f)

    if isinstance(data, dict):
        for key in ("results", "records", "logEvents", "events"):
            if isinstance(data.get(key), list):
                data = data[key]
                break
        else:
            raise SystemExit(
                f"error: unrecognized top-level object; keys={list(data)[:8]}"
            )
    if not isinstance(data, list):
        raise SystemExit("error: expected a JSON array of records")

    recs, dropped = [], 0
    for entry in data:
        if isinstance(entry, list):  # shape 3 → fold field/value pairs to a dict
            folded = {}
            for fv in entry:
                if isinstance(fv, dict) and "field" in fv:
                    folded[fv["field"]] = fv.get("value")
            entry = folded
        if isinstance(entry, dict) and entry:
            recs.append(entry)
        else:
            dropped += 1
    if dropped:
        print(f"note: dropped {dropped} non-dict/empty entries", file=sys.stderr)
    return recs


def build_rows(recs, ce, site, skip_bulk):
    """Normalize → bulk-skip → dual-filter → chronological sort.

    Returns (rows, n_bulk_skipped, n_filtered_out, was_reverse, n_bad_ts).
    Each row: dict(ts, ts_raw, msg, text).
    """
    rows, n_bulk, n_filtered, n_bad_ts = [], 0, 0, 0
    for rec in recs:
        raw_ts = next((rec[k] for k in TS_KEYS if k in rec), None)
        msg = rec.get("@message", rec)
        text = text_of(msg)

        if skip_bulk and (
            BULK_RE.search(text)
            or (len(text) > BULK_SIZE_BYTES and '"items"' in text)
        ):
            n_bulk += 1
            continue

        # Dual-filter on the RAW serialized record: whole record, not just
        # @message, so IDs inside @logStream / ARNs / extra fields still match.
        blob = text if msg is rec else text_of(rec)
        if (ce or site) and not (
            (ce and ce in blob) or (site and site in blob)
        ):
            n_filtered += 1
            continue

        ts = parse_ts(raw_ts)
        if ts == EPOCH and raw_ts is not None:
            n_bad_ts += 1
        rows.append({"ts": ts, "ts_raw": raw_ts, "msg": msg, "text": text})

    # Detect input ordering before we destroy the evidence, then re-sort.
    stamped = [r["ts"] for r in rows if r["ts"] != EPOCH]
    was_reverse = len(stamped) >= 2 and stamped[0] > stamped[-1]
    rows.sort(key=lambda r: r["ts"])  # stable: ties keep input order
    return rows, n_bulk, n_filtered, was_reverse, n_bad_ts


# --- Line rendering ----------------------------------------------------------

def one_line(row, width):
    d = as_dict(row["msg"])
    if d is not None:
        level = str(next((d[k] for k in LEVEL_KEYS if d.get(k)), "-")).upper()
        core = str(next((d[k] for k in MSG_KEYS if d.get(k)), "")).strip()
        op = d.get("operation") or ""
        status = d.get("status") or ""
        opstat = f"{op}/{status}".strip("/")
        extras = " ".join(
            f"{k}={d[k]}" for k in EXTRA_KEYS if d.get(k) not in (None, "")
        )
        if core or opstat:
            parts = [p for p in (level.ljust(5), opstat, core) if p and p.strip()]
            line = "  ".join(parts)
            if extras:
                line += f"  [{extras}]"
        else:  # structured but none of the known fields (e.g. SQS envelope)
            line = f"{level.ljust(5)}  " + re.sub(r"\s+", " ", row["text"]).strip()
    else:
        line = "-      " + re.sub(r"\s+", " ", row["text"]).strip()
    if width and len(line) > width:
        line = line[: width - 1] + "…"
    return f"{fmt_ts(row['ts'], row['ts_raw'])}  {line}"


def level_of(row):
    d = as_dict(row["msg"])
    if d is not None:
        return str(next((d[k] for k in LEVEL_KEYS if d.get(k)), "")).upper()
    return ""


# --- Views -------------------------------------------------------------------

def view_stats(rows, width):
    if not rows:
        return
    levels, sites = {}, {}
    for r in rows:
        lv = level_of(r) or "(string)"
        levels[lv] = levels.get(lv, 0) + 1
        d = as_dict(r["msg"])
        if d and d.get("ce_siteid"):
            sites[d["ce_siteid"]] = sites.get(d["ce_siteid"], 0) + 1
    print(f"records kept : {len(rows)}")
    print(f"span (UTC)   : {fmt_ts(rows[0]['ts'], rows[0]['ts_raw'])} -> "
          f"{fmt_ts(rows[-1]['ts'], rows[-1]['ts_raw'])}")
    print("levels       : " + ", ".join(
        f"{k}={v}" for k, v in sorted(levels.items(), key=lambda x: -x[1])))
    if sites:
        top = sorted(sites.items(), key=lambda x: -x[1])[:10]
        print("top ce_siteid: " + ", ".join(f"{k}({v})" for k, v in top))
    print("next         : --timeline | --errors | --grep P | --metering")


def view_timeline(rows, width, limit):
    shown = rows[:limit] if limit else rows
    for i, r in enumerate(shown):
        print(f"[{i:>4}] {one_line(r, width)}")
    if limit and len(rows) > limit:
        print(f"… {len(rows) - limit} more (raise --limit or add --grep)",
              file=sys.stderr)


def view_errors(rows, width, limit):
    hits = [
        (i, r) for i, r in enumerate(rows)
        if level_of(r) in ERROR_LEVELS or ERROR_TEXT_RE.search(r["text"])
    ]
    shown = hits[:limit] if limit else hits
    for i, r in shown:
        print(f"[{i:>4}] {one_line(r, width)}")
    print(f"# {len(hits)} error-like records of {len(rows)} kept",
          file=sys.stderr)


def view_grep(rows, pattern, width, limit):
    try:
        rx = re.compile(pattern, re.IGNORECASE)
    except re.error as e:
        raise SystemExit(f"error: bad --grep regex: {e}")
    hits = [(i, r) for i, r in enumerate(rows) if rx.search(r["text"])]
    shown = hits[:limit] if limit else hits
    for i, r in shown:
        print(f"[{i:>4}] {one_line(r, width)}")
    print(f"# {len(hits)} matches of {len(rows)} kept", file=sys.stderr)


def view_show(rows, n):
    if not 0 <= n < len(rows):
        raise SystemExit(f"error: --show {n} out of range 0..{len(rows) - 1}")
    r = rows[n]
    print(f"# record [{n}] @ {fmt_ts(r['ts'], r['ts_raw'])}")
    print(json.dumps(r["msg"], indent=2, default=str)
          if isinstance(r["msg"], dict) else r["text"])


def decode_metering_record(msg):
    """Two json.loads hops: Records[].body → TopicArn+Message → payload."""
    d = as_dict(msg)
    if not isinstance(d, dict):
        return []
    records = None
    for container in (d.get("sqsEvent"), d):
        if isinstance(container, dict) and isinstance(container.get("Records"), list):
            records = container["Records"]
            break
    if records is None:
        return []
    out = []
    for r in records:
        if not isinstance(r, dict):
            continue
        attrs = r.get("attributes") or {}
        body = r.get("body") or r.get("Body")
        try:
            body_d = json.loads(body) if isinstance(body, str) else (body or {})
        except (json.JSONDecodeError, ValueError):
            body_d = {}
        inner = body_d.get("Message")
        try:
            inner_d = json.loads(inner) if isinstance(inner, str) else (inner or {})
        except (json.JSONDecodeError, ValueError):
            inner_d = {}
        out.append({
            "ce_siteid": inner_d.get("ce_siteid"),
            "status": inner_d.get("status"),
            "receive_count": attrs.get("ApproximateReceiveCount"),
            "sender": attrs.get("SenderId"),
            "topic": body_d.get("TopicArn"),
        })
    return out


def view_metering(rows, width):
    per_site = {}
    for r in rows:
        if not METERING_HINT_RE.search(r["text"]):
            continue
        for ev in decode_metering_record(r["msg"]):
            site = ev["ce_siteid"] or "(unknown)"
            per_site.setdefault(site, []).append((r["ts"], r["ts_raw"], ev))
    if not per_site:
        print("# no metering envelopes found in the kept records", file=sys.stderr)
        return
    for site, events in sorted(per_site.items()):
        transitions = 0
        print(f"== {site} — {len(events)} health events ==")
        prev = None
        for ts, ts_raw, ev in events:
            status = ev["status"] or "?"
            flags = []
            if prev is not None and status != prev:
                transitions += 1
                flags.append("<- FLAP")
            try:
                if int(ev["receive_count"] or 1) > 1:
                    flags.append(f"redelivered x{ev['receive_count']}")
            except (TypeError, ValueError):
                pass
            print(f"  {fmt_ts(ts, ts_raw)}  {status:<9} {' '.join(flags)}".rstrip())
            prev = status
        verdict = ("FLAPPING — see signature S4 (publisher-side; verify EC2 "
                   "uptime before blaming the CE)" if transitions >= 2
                   else "stable")
        print(f"  -- transitions: {transitions} -> {verdict}")


# --- Entry point -------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="CE-triage parser for CloudWatch Logs Insights JSON exports."
    )
    ap.add_argument("file", help="logs-insights-results__NN_.json")
    ap.add_argument("--ce", help="CE ID filter (e.g. CEAM<CE_ID>)")
    ap.add_argument("--site", help="site ID filter (e.g. TDICAM<SITE_ID>)")
    ap.add_argument("--no-skip", action="store_true",
                    help="disable bulk List* skipping (debug only)")
    ap.add_argument("--width", type=int, default=240,
                    help="max line width (0 = unlimited)")
    ap.add_argument("--limit", type=int, default=0,
                    help="max lines printed (0 = unlimited)")
    view = ap.add_mutually_exclusive_group()
    view.add_argument("--stats", action="store_true")
    view.add_argument("--timeline", action="store_true")
    view.add_argument("--errors", action="store_true")
    view.add_argument("--grep", metavar="PATTERN")
    view.add_argument("--metering", action="store_true")
    view.add_argument("--show", type=int, metavar="N")
    args = ap.parse_args(argv)

    recs = load_records(args.file)
    rows, n_bulk, n_filtered, was_reverse, n_bad_ts = build_rows(
        recs, args.ce, args.site, skip_bulk=not args.no_skip
    )

    print(
        f"# loaded {len(recs)} records; skipped {n_bulk} bulk List* entries; "
        f"{n_filtered} filtered out by --ce/--site; kept {len(rows)}"
        + ("; input was reverse-chronological -> re-sorted" if was_reverse else "")
        + (f"; {n_bad_ts} unparseable timestamps sorted to top" if n_bad_ts else ""),
        file=sys.stderr,
    )
    if not rows:
        print("# zero records kept — check the ID spelling, try --site as well "
              "as --ce, or retry with --no-skip to rule out over-skipping",
              file=sys.stderr)
        return 2

    if args.timeline:
        view_timeline(rows, args.width, args.limit)
    elif args.errors:
        view_errors(rows, args.width, args.limit)
    elif args.grep is not None:
        view_grep(rows, args.grep, args.width, args.limit)
    elif args.metering:
        view_metering(rows, args.width)
    elif args.show is not None:
        view_show(rows, args.show)
    else:
        view_stats(rows, args.width)
    return 0


if __name__ == "__main__":
    sys.exit(main())
