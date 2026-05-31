#!/usr/bin/env python3
"""
parse_cloudwatch.py — offline parser for CloudWatch Logs Insights JSON exports
(logs-insights-results__NN_.json) used in CE log triage.

Why this exists: the exports routinely run ~40 MB, `jq` is often absent on the
box, and the host frequently has no network. This script depends only on the
Python standard library so it runs anywhere `python3` does.

Input shape: a JSON array of entries, each like
    {"@timestamp": "...Z", "@message": <dict|str>}
`@message` is sometimes a structured dict (level/msg/ce_siteid/operation/...)
and sometimes a plain string. Both are handled. Bulk inventory entries such as
a "ListComputeEngineConfigs response" (big `items` array) are skipped.

Views:
  --timeline   per-CE chronological events (default if no view is chosen)
  --errors     entries that errored (ERROR level, or fail/timeout in the text)
  --metering   decode the triple-nested SQS -> SNS -> Message envelope, then
               flag healthy/critical flaps and high ApproximateReceiveCount

Filter:
  --ce <ID>    keep only entries that mention this CE/site id anywhere

Usage:
  python3 parse_cloudwatch.py logs-insights-results__42_.json --ce CEAMGPSCTEAM0009R --timeline
  python3 parse_cloudwatch.py logs-insights-results__43_.json --errors
  python3 parse_cloudwatch.py metering.json --metering
"""

import argparse
import json
import sys

# Fields worth surfacing from a structured @message, in display order.
INTEREST = ["operation", "status", "connectivity", "error_code", "stage",
            "state", "ce_siteid", "config_id", "compute_engine_config_id"]


def load_entries(path):
    """Load the top-level JSON array. Fail clearly if the file isn't that shape."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        sys.exit(f"error: file not found: {path}")
    except json.JSONDecodeError as exc:
        sys.exit(f"error: {path} is not valid JSON ({exc}); is it a Logs Insights export?")
    if isinstance(data, dict):                # some exports wrap the array
        data = data.get("results") or data.get("events") or [data]
    if not isinstance(data, list):
        sys.exit("error: expected a JSON array of {@timestamp,@message} entries")
    return data


def as_message_dict(msg):
    """Coerce @message to a dict when possible; return None if it's a plain string."""
    if isinstance(msg, dict):
        return msg
    if isinstance(msg, str):
        s = msg.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                parsed = json.loads(s)
                return parsed if isinstance(parsed, dict) else None
            except json.JSONDecodeError:
                return None
    return None


def is_bulk(msgd):
    """True for inventory/list noise we never want in a timeline."""
    if not msgd:
        return False
    text = str(msgd.get("msg", ""))
    if "ListComputeEngineConfigs" in text:
        return True
    count = msgd.get("count")
    items = msgd.get("items")
    if isinstance(count, int) and count > 50 and isinstance(items, list):
        return True
    return False


def mentions(entry, needle):
    """Case-insensitive substring match over the whole stringified entry."""
    if not needle:
        return True
    return needle.lower() in json.dumps(entry, default=str).lower()


def ts_of(entry):
    return entry.get("@timestamp", "") or ""


def fmt_fields(msgd):
    parts = []
    for k in INTEREST:
        if k in msgd and msgd[k] not in (None, ""):
            parts.append(f"{k}={msgd[k]}")
    return "  ".join(parts)


def view_timeline(entries, needle):
    rows = []
    for e in entries:
        if not mentions(e, needle):
            continue
        msgd = as_message_dict(e.get("@message"))
        if is_bulk(msgd):
            continue
        ts = ts_of(e)
        if msgd is None:                       # plain-string @message
            line = str(e.get("@message", "")).strip().replace("\n", " ")
            rows.append((ts, f"{ts}  [str] {line[:200]}"))
        else:
            level = msgd.get("level", "")
            text = str(msgd.get("msg", "")).strip()
            extra = fmt_fields(msgd)
            rows.append((ts, f"{ts}  [{level or '-'}] {text}" + (f"   ({extra})" if extra else "")))
    rows.sort(key=lambda r: r[0])
    print(f"# timeline — {len(rows)} event(s)" + (f" matching {needle!r}" if needle else ""))
    for _, line in rows:
        print(line)


def view_errors(entries, needle):
    hits = 0
    print("# errors — ERROR level or fail/timeout in the text"
          + (f", matching {needle!r}" if needle else ""))
    for e in sorted(entries, key=ts_of):
        if not mentions(e, needle):
            continue
        msgd = as_message_dict(e.get("@message"))
        blob = (json.dumps(msgd) if msgd else str(e.get("@message", ""))).lower()
        level = (msgd.get("level", "") if msgd else "").upper()
        if level == "ERROR" or any(t in blob for t in ("fail", "error", "timed out", "timeout", "exception")):
            ts = ts_of(e)
            text = (str(msgd.get("msg", "")) if msgd else str(e.get("@message", ""))).strip()
            extra = fmt_fields(msgd) if msgd else ""
            print(f"{ts}  [{level or 'str'}] {text[:200]}" + (f"   ({extra})" if extra else ""))
            hits += 1
    if not hits:
        print("(none)")


def _account_of(arn):
    """ARN field 4 (0-based) is the account id: arn:aws:svc:region:ACCOUNT:..."""
    parts = str(arn).split(":")
    return parts[4] if len(parts) > 4 else "?"


def view_metering(entries, needle):
    print("# metering — decoded SQS -> SNS -> Message envelope"
          + (f", matching {needle!r}" if needle else ""))
    per_ce = {}          # ce_siteid -> list of (ts, status)
    redeliveries = []    # (ts, ce, count)
    rows = 0
    for e in sorted(entries, key=ts_of):
        if not mentions(e, needle):
            continue
        msgd = as_message_dict(e.get("@message"))
        if not msgd:
            continue
        sqs = msgd.get("sqsEvent") or msgd.get("Sqs") or {}
        records = sqs.get("Records") if isinstance(sqs, dict) else None
        if not records:
            continue
        ts = ts_of(e)
        for r in records:
            try:
                attrs = r.get("attributes", {}) or {}
                sender = attrs.get("SenderId", "?")
                recv = int(attrs.get("ApproximateReceiveCount", "1"))
                src_arn = r.get("eventSourceARN", "")
                body = r.get("body")
                body = json.loads(body) if isinstance(body, str) else (body or {})
                topic = body.get("TopicArn", "")
                inner = body.get("Message")
                inner = json.loads(inner) if isinstance(inner, str) else (inner or {})
                ce = inner.get("ce_siteid", "?")
                status = inner.get("status", "?")
            except (ValueError, AttributeError, KeyError) as exc:
                print(f"{ts}  [decode-skip] {exc}")
                continue
            print(f"{ts}  ce={ce}  status={status}  recv={recv}  "
                  f"sender={sender}  queue_acct={_account_of(src_arn)}  topic_acct={_account_of(topic)}")
            per_ce.setdefault(ce, []).append((ts, status))
            if recv > 1:
                redeliveries.append((ts, ce, recv))
            rows += 1
    if not rows:
        print("(no metering/SQS records found)")
        return
    print("\n## per-CE status sequence (flap detection)")
    for ce, seq in per_ce.items():
        statuses = {s for _, s in seq}
        flap = "  <-- FLAP (both healthy and critical)" if {"healthy", "critical"} <= statuses else ""
        order = " -> ".join(s for _, s in seq)
        print(f"  {ce}: {order}{flap}")
    if redeliveries:
        print("\n## redeliveries (ApproximateReceiveCount > 1)")
        for ts, ce, n in redeliveries:
            flag = "  <-- high" if n >= 4 else ""
            print(f"  {ts}  {ce}  recv={n}{flag}")


def main():
    ap = argparse.ArgumentParser(description="Offline CloudWatch Logs Insights JSON parser for CE triage.")
    ap.add_argument("file", help="path to logs-insights-results__NN_.json")
    ap.add_argument("--ce", metavar="ID", help="filter to entries mentioning this CE/site id")
    ap.add_argument("--timeline", action="store_true", help="chronological event timeline")
    ap.add_argument("--errors", action="store_true", help="error / failure / timeout entries")
    ap.add_argument("--metering", action="store_true", help="decode metering SQS->SNS->Message, flag flaps")
    args = ap.parse_args()

    entries = load_entries(args.file)
    print(f"# loaded {len(entries)} entries from {args.file}\n")

    if args.errors:
        view_errors(entries, args.ce)
        print()
    if args.metering:
        view_metering(entries, args.ce)
        print()
    if args.timeline or not (args.errors or args.metering):
        view_timeline(entries, args.ce)


if __name__ == "__main__":
    main()
