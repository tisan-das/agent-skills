# WORKFLOWS — Executable procedures and session state protocol

Load this file when executing any triage step in detail, or when starting or
resuming a session. Each workflow specifies **preconditions, procedure,
expected outputs, recovery, and validation** so it can run without human
clarification mid-task. Triage is read-only: "recovery" here always means
recovering the *investigation* from a wrong turn, never mutating logs, AWS
resources, or CE state.

Contents:

- [State and checkpointing (read first in long sessions)](#state)
- [W1 — Evidence intake and inventory](#w1)
- [W2 — Control-plane timeline (CloudWatch JSON)](#w2)
- [W3 — On-host timeline (syslog sweep)](#w3)
- [W4 — Cross-layer reconciliation](#w4)
- [W5 — Signature match and confirmation](#w5)
- [W6 — Conclusion, handoff, session close](#w6)

---

<a name="state"></a>
## State and checkpointing for long agentic sessions

Long sessions lose context to compaction; the checkpoint files are the ground
truth that survives it. Maintain them from W1 onward and treat them as more
authoritative than your own recollection of earlier turns.

### Files

```
./.triage/<CE_ID>/STATE.md     # resume point — overwrite-in-place at step boundaries
./.triage/<CE_ID>/VERIFY.log   # append-only audit trail — never rewrite
./.triage/<CE_ID>/timeline.txt # saved parser/sweep outputs (evidence snapshots)
```

### STATE.md template

```markdown
# Triage state — <CE_ID> / <SITE_ID>
env: <preprod|prod|sit>        reported_state: <exact string>
window_utc: <start> .. <end>
files:
  - path: <p>  lines: <wc -l>  sha256: <first 12 hex>  format: <cw-json|syslog>
current_step: <W1..W6 + substep>
timeline: .triage/<CE_ID>/timeline.txt
signatures:
  ruled_in:  [<S# — deciding evidence pointer>]
  ruled_out: [<S# — why>]
  pending:   [<S# — confirmation still to run>]
open_questions: [<what the logs cannot yet settle>]
verdict: <empty until W6>
```

### VERIFY.log format (append-only, one line per verification)

```
<UTC ISO> | <step> | <command> | expected: <…> | observed: <…> | PASS|FAIL
```

Log every load-bearing check: file identity, filter hit-counts, signature
confirmations, sequence-order checks. A FAIL line is kept, never deleted — the
audit trail must show wrong turns and their corrections.

### Update and resume protocol

1. **Update** STATE.md at every step boundary (end of W1, W2, … and after any
   signature is ruled in/out).
2. **On resume** (after compaction, a new session, or model handoff): read
   STATE.md first; re-run the file-identity checks (`wc -l`, `sha256sum`) and
   compare against the recorded values before trusting any cached conclusion.
3. If identities match, continue from `current_step`; do not restart from
   scratch and do not re-derive settled conclusions.
4. If identities do NOT match (file replaced/extended), log a FAIL line,
   invalidate `timeline.txt` and any signature decisions derived from that
   file, and re-run W1 for that file only.
5. **Citation discipline** — every claim promoted into STATE.md carries an
   evidence pointer: `file:line` for syslog, `[index] + UTC timestamp` for
   parser output. Uncited claims are hypotheses and must be labeled as such.

---

<a name="w1"></a>
## W1 — Evidence intake and inventory

**Preconditions.** A CE ID and/or site ID is known; at least one candidate
evidence file exists (or the user must be asked *before starting*, not
mid-task). Working directory is writable for `./.triage/`.

**Procedure.**

1. Create the state directory and initialize STATE.md from the template:
   `mkdir -p .triage/<CE_ID>`.
2. Record the four basics: CE ID + site ID, exact reported state, environment,
   UTC window. If any is unknown, record `unknown` explicitly — do not guess.
3. For every evidence file, capture identity without loading it:
   `wc -l <f>; head -5 <f>; echo ---; tail -5 <f>; sha256sum <f>`.
4. Classify each file's format from the boundaries: JSON array / wrapped JSON →
   control plane (W2); syslog lines → on-host (W3).
5. If the node is live, capture ground truth:
   `curl -s http://localhost:22222/provisioning-status` (record verbatim).

**Expected outputs.** STATE.md populated (basics + files table with line
counts, hashes, formats); one VERIFY.log line per file
(`expected: readable+classified | observed: <format>,<lines> | PASS`).

**Recovery.** Misclassified file (JSON treated as syslog or vice versa):
correct the `format` field, log a FAIL→PASS pair, and discard any derived
output. Missing/garbled file: record it in `open_questions` and proceed with
the remaining evidence — never fabricate a layer you have no file for.

**Validation.** Every file in STATE.md has all four identity fields; formats
are consistent with `head`/`tail` samples; no file was ever `cat`-ed whole
into context.

---

<a name="w2"></a>
## W2 — Control-plane timeline (CloudWatch JSON)

**Preconditions.** W1 complete; at least one file classified `cw-json`;
`python3` available (stdlib only — no pip, no network needed).

**Procedure.**

1. Orient: `python3 scripts/parse_cloudwatch.py <f>` (stats view). Record kept
   count, span, and the skipped-bulk count from stderr.
2. Build the engine timeline with the **dual filter** — always pass both IDs
   when both are known, because some records carry only one:
   `python3 scripts/parse_cloudwatch.py <f> --ce <CE> --site <SITE> --timeline | tee .triage/<CE_ID>/timeline.txt`.
3. Extract the error skeleton: same command with `--errors`.
4. Chase specifics with `--grep` (anchors in REFERENCE.md §C.6) and inspect
   any pivotal record in full with `--show N` before citing it.
5. If metering/health is in scope, run `--metering` and record the per-site
   verdict (stable vs FLAPPING).
6. Promote each pivotal event into STATE.md as
   `[index] <UTC> — <event> — proves: <what>`.

**Expected outputs.** `timeline.txt` saved; STATE.md gains the ordered pivotal
events, each with an index+timestamp pointer and a "proves" clause; VERIFY.log
lines for the stats counts and for each `--show` inspection.

**Recovery.** Zero records kept (parser exits 2): check ID spelling, add the
other ID, then try `--no-skip` to rule out over-skipping — log each attempt.
Wrong engine filtered (prefix-collision IDs): re-run with the full exact ID
and regenerate `timeline.txt`; log FAIL→PASS. Parser anomalies: consult
"Script troubleshooting" in TROUBLESHOOTING.md before editing the script.

**Validation.** stderr reports either chronological input or
"re-sorted" (never trust an unsorted listing); kept + skipped + filtered ≈
loaded; every timeline claim in STATE.md has an `[index]` that `--show` can
reproduce.

---

<a name="w3"></a>
## W3 — On-host timeline (syslog sweep)

**Preconditions.** W1 complete; at least one file classified `syslog`;
`bash`, `grep`, `sed` available.

**Procedure.**

1. Sweep: `bash scripts/triage_syslog.sh <messages>` (add `--ip <node-ip>` on
   multi-node logs). Record per-group match counts.
2. Pick the **transition line** — the first line where state changed (first
   `DOWN/HARDSTOP`, `Event 13912`, first `lost contact`), not the last retry
   echo.
3. Window it: `bash scripts/triage_syslog.sh <messages> --window <L> [--ctx 80]`
   and append the window to `timeline.txt` with its line range noted.
4. Locate the previous **successful** run of the same orchestration and window
   it too — diffing failed vs successful sequence is the strongest evidence.
5. Promote pivotal lines into STATE.md as `<file>:<line> <UTC> — <event> —
   proves: <what>`, normalizing timestamps to UTC (note the host's offset).

**Expected outputs.** Sweep summary (group→count) in STATE.md; windows
appended to `timeline.txt`; VERIFY.log lines recording the chosen transition
line and the control-run comparison.

**Recovery.** All groups zero: verify the file is actually a syslog
(`head -5`) and drop `--ip`. Wrong window chosen (transition was earlier):
re-window — windows are cheap; log the correction. NOISE-group lines looking
causal: check the noise catalog in TROUBLESHOOTING.md before promoting them.

**Validation.** The transition line's timestamp falls inside the incident
window from STATE.md; every promoted line is reproducible by `sed -n 'L p'`;
timestamps were converted to UTC before entering the shared timeline.

---

<a name="w4"></a>
## W4 — Cross-layer reconciliation

**Preconditions.** Timelines exist for both layers (W2 and W3) — skip this
workflow (and say so in STATE.md) if the incident is single-layer.

**Procedure.**

1. Merge the pivotal events from both layers into one UTC-ordered list in
   STATE.md.
2. Locate where the control-plane status **stopped advancing** and check what
   the on-host layer shows at that same moment (the canonical pattern: status
   frozen because PDE crashed during `expand vconfig` — S2 causing S5).
3. Walk the merged list against the state machines (REFERENCE.md §B) and name
   the transition that did not fire and the service/account that owns it.
4. Record the causal direction explicitly: which layer's event *precedes and
   explains* the other's.

**Expected outputs.** A single merged timeline in STATE.md; the missing
transition named with its owning service/account; a one-sentence causal chain.

**Recovery.** If events appear simultaneous or out of order, suspect timezone
skew first — re-check each source's UTC conversion (W3 step 5) before
inferring causality. If the layers genuinely don't intersect in time, record
that as an open question rather than forcing a link.

**Validation.** The causal chain reads strictly forward in UTC; the "missing
transition" exists in the state machine diagrams; ownership names a specific
service AND account, verified against ARNs in the logs (not just the topology
table).

---

<a name="w5"></a>
## W5 — Signature match and confirmation

**Preconditions.** A divergence point exists (from W2/W3/W4);
TROUBLESHOOTING.md is loaded.

**Procedure.**

1. Compare the divergence point against the S1–S7 index table **and grep
   `references/CE-SIGNATURE-REGISTRY.md`** for the error code / distinctive log
   phrase (cross-map in `triage/TRIAGE.md`); shortlist every plausible signature
   from both, not just the first hit.
2. For each shortlisted signature, **run its "Confirm" step verbatim** and log
   the result — matching the symptom is never sufficient (a stuck
   `expand vconfig` is S5's symptom but usually S2's fault; an
   `INVALID_ARGUMENT` 400 is only S7 if the upstream timeout beat precedes it).
3. Rule signatures in or out in STATE.md, each with its deciding evidence
   pointer; anything unconfirmable goes to `pending` with the blocking reason.
4. If nothing matches, say so explicitly and characterize the failure from
   first principles using the state machines — then, once confirmed, **file it
   as a new registry row** (`rca/RCA-WRITEUP.md`, Artifact 3, append-only
   numbering); add a deep-dive S-entry to this playbook only when the full
   symptom/cause/confirm/fix procedure earns it (that is exactly how S7 entered).

**Expected outputs.** STATE.md `signatures` block fully populated; one
VERIFY.log PASS/FAIL line per confirmation actually run.

**Recovery.** Confirmation contradicts the match: rule it out, log the FAIL,
and return to the shortlist — do not soften the signature to fit. Two
signatures both confirm: the one whose event is *earliest* in the causal chain
is the root cause; the other is downstream (state both).

**Validation.** No signature is `ruled_in` without an executed confirmation in
VERIFY.log; downstream signatures are explicitly marked as symptoms of the
upstream one.

---

<a name="w6"></a>
## W6 — Conclusion, handoff, session close

**Preconditions.** W5 complete (or an explicit no-match characterization
exists); STATE.md has no unlabeled hypotheses.

**Procedure.**

1. Write the verdict block into STATE.md: one-line verdict; the UTC timeline;
   root cause with its deciding evidence; secondary issues kept separate
   ("logged errors, not on the critical path"); next steps ordered by
   authority of evidence; a caveats line for what the logs cannot prove.
2. Audit pass: re-check that every claim in the verdict has an evidence
   pointer that still resolves (`--show N` / `sed -n 'L p'`), and log one
   summary VERIFY.log line for the pass.
3. If the user wants a document, hand the verdict block to the
   **RCA module** (`rca/RCA-WRITEUP.md`) — this module produces the *conclusion*, that one
   produces the *artifact*.
4. If W5 produced a candidate new signature, surface it to the user with a
   ready-to-paste TROUBLESHOOTING.md entry.
5. Leave `./.triage/<CE_ID>/` in place — it is the durable audit record of the
   session.

**Expected outputs.** Completed STATE.md (verdict populated); final VERIFY.log
audit line; optionally an RCA-module invocation and/or a new-signature
proposal.

**Recovery.** If the audit pass finds an unsupported claim, downgrade it to an
open question and adjust the verdict's confidence — never ship a verdict whose
citations don't resolve.

**Validation.** A reader who was not in the logs can follow verdict →
evidence pointer → raw record for every load-bearing claim; secondary noise
is explicitly separated; the caveats line exists even when confidence is high.
