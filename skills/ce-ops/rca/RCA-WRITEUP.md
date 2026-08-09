# RCA Writeup — turn a concluded investigation into durable artifacts

> **Provenance.** Merged from two sources: the Jira/SOP artifacts (steps S8–S10,
> §9–§11 of `references/CE-DEBUG-SOP.md`, the registry's maintenance and
> verdict-hazard rules, and the triage W6 handoff contract), and the standalone
> rca-writeup skill this module absorbed — which contributed the shareable
> document template, the house-style conventions, the build procedure, the
> mistakes list, and the two worked examples in this directory.

An investigation that ends in a meeting is paid for twice — 58% of closed CE issues
never recorded what was wrong. This module exists to reverse that: every concluded
investigation leaves **three artifacts** — the RCA writeup itself, a
signature-registry row, and an observability-gap ticket.

Strong forensic work is also worthless if it evaporates in a chat window. The person
who approves the fix, the teammate who owns the upstream service, and the manager who
needs the one-paragraph version were none of them in the logs with you.

## Entry condition — do you actually have a root cause?

Write an RCA only when you can name the cause class (R1 timing/race · R2 stale state ·
R3 code · R4 capacity · R5 config drift · R6 infra/network · R7 not-a-defect) **and**
point at deciding evidence. If you cannot, you have a hypothesis: write a status
update instead — *"suspected R1, unconfirmed, next check = X"* — never a verdict.
Labelled speculation is fine (it appears in 38% of *fast* threads); unlabelled
speculation is what makes threads slow.

If the investigation is not finished, **stop and say so** — list what is still
unknown and offer to run the triage pipeline. A half-finished RCA that papers over
the gap with a guess is worse than no RCA.

**Honesty rule that overrides formatting:** never invent an identifier, a timestamp,
an ARN, or an event to fill a slot in a template. If the timeline has a gap, show the
gap. If ownership is unproven, say "owner unconfirmed — verify in CloudTrail for
account X". The credibility of the whole document rests on every concrete claim
being real.

## Inputs (the handoff contract from triage W6 / SOP S6)

- Verdict in one line, and the R-class
- The UTC timeline table (time · source · verbatim line · what it proves)
- The deciding evidence, quoted verbatim — never paraphrased
- The secondary-noise list (what logged errors but was off the critical path)
- Fix / workaround, owner, and where it lands (repo / BOM)
- The attempt ratio from S1 (drives the verification sample size)
- Caveats: anything `[inferred]`, anything unverified on this ticket's own CE
- Identity: CE config ID, site ID, environment, region/AZ, account

If something is missing, ask for it or mark it explicitly as unknown — do not paper
over it.

## Which deliverable?

| Situation | Produce |
|---|---|
| Closing a ticket; the audience is the CE team and the record | **Artifact 1 — the Jira comment.** The default. |
| The audience includes people who weren't in the logs — a manager, an upstream service owner, Confluence, Slack, a customer-facing summary | **Artifact 1b — the standalone document**, then Artifact 1 links to it |
| Formal or executive deliverable (.docx/.pdf) | Write Artifact 1b, then hand it to `report-builder` — see *Rendering* |

Both carry the same evidence and the same house style. Artifact 1 is terse and
ticket-shaped; 1b is self-contained and skimmable. Never let them disagree.

## Artifact 1 — the RCA comment (SOP §11.1 template, use exactly)

```markdown
## Root cause — <site / CE config / cluster>
**Verdict (1 line):**
**Class:** R1 timing · R2 stale state · R3 code · R4 capacity · R5 config · R6 infra · R7 not-a-defect

### Timeline (UTC)
| time | source | evidence | proves |

### Root cause
<deciding evidence, quoted verbatim>

### Not the cause
<errors that logged but were off the critical path>

### Fix / workaround
<what changes, where, who owns it>

### Observability gap
<the log line or metric that would have made this 10× faster>
```

Rules that make the template work:

- **Timeline rows carry verbatim lines with UTC timestamps** — `file:line` for syslog,
  `[index] + UTC` for parser output, log-group + timestamp for CloudWatch. A row
  without a source is an opinion.
- **"Not the cause" is mandatory.** Naming the noise is what lets the next reader
  trust the verdict — and what stops the same red herrings being re-investigated
  (the registry's *Secondary noise* section lists the usual ones).
- **The observability gap is the highest-leverage habit available.** Name the one
  missing log line or metric, then **file it as a ticket** — a gap that lives only in
  the RCA text is never fixed.
- Cite by qualified IDs (registry #N, KB gap D#, domain D#, lifecycle defect N,
  Jira keys) per the namespace table in SKILL.md.

## Artifact 1b — the standalone shareable document

Produce sections in this order. Sections marked *(optional)* may be omitted when
there is nothing real to put in them — but never delete a section to hide a gap; if
it's relevant and unknown, keep it and write "unknown".

```markdown
# RCA: <one-line failure description> — <CE_ID>

| Field | Value |
|---|---|
| CE ID | CEAM<CE_ID> |
| Site ID | TDICAM<SITE_ID> |
| Environment | preprod |
| Region / AZ | us-west-2 / usw2-az2 |
| Account | <account> |
| Severity | <your org's scale, e.g. SEV3> |
| Status | Investigating · Identified · Mitigated · Resolved · Monitoring |
| Window (UTC) | 2026-05-28 07:22 – 07:48 |
| Author | <name> |

## Summary
One or two sentences: the verdict. The reader who only reads this line should
walk away knowing what failed and why.

## Impact
What was affected, for whom, for how long. For a single stuck CE this may be one
line; for a fleet-wide event, quantify it.

## Timeline (UTC)
| Time (UTC) | Event | What it proves |
|---|---|---|
Each row pairs an event with its significance — not just "what the log said" but
"what it establishes". Normalize every timestamp to UTC.

## Root cause
State it plainly in one or two sentences, then give the deciding evidence (the
exact error code / event number / log location). No hedging once the evidence
supports the verdict. Name the R-class.

## Supporting evidence   (optional, if not already clear from the timeline)
## Contributing factors   (optional)
Conditions that made the failure more likely or worse but were not the trigger.

## Secondary issues
Real problems observed that did NOT cause this failure. Keep them visibly separate
so no one mistakes a logged-but-benign error for the trigger.

## Recommended next steps
Ordered by authority of evidence: confirm in the authoritative source first (e.g.
CloudTrail in the owning account), then resolve the specific role/ARN, then "ask the
owning team" only when the logs can't settle it. Name an owner where known.

## Caveats — what the logs cannot prove
The honest limits. Any conclusion resting on a load-bearing assumption the logs
can't fully prove is flagged here with where to verify it.

## Appendix   (optional)
Key raw excerpts, ARNs, IDs, code locations.
```

Default filename: `rca-<CE_ID>-<YYYY-MM-DD>.md`.

## House style — the conventions that make a writeup trustworthy

These are exactly the things a generic model flattens. Each one is load-bearing, and
they apply to both artifacts.

- **Name exact identifiers.** Account IDs, ARNs, `vpce-svc-…`/`vpc-…`/`subnet-…` IDs,
  error codes (`AWS-AssignSlot-Error`), event numbers (`Event 13912`), retcodes
  (`retcode 61`), CE/site IDs, code locations (`ce_manager.go:122`), the actual
  TXT-record name. Never write "a permissions error" when the log gives you the code;
  never write "the network service" when you can name the account. Specificity is
  what lets a reader verify you.
- **Distinguish "what failed" from "what merely logged an error."** The single most
  important discipline. A failure log is noisy; most error lines did not cause
  anything. Cause goes in *Root cause* / *Not the cause* respectively, in plain words.
- **Timestamps are precise and in UTC.** Services carry different local offsets; a
  timeline in mixed offsets silently lies.
- **State the verdict plainly; hedge only where evidence runs out.** Say it flatly,
  then put genuine uncertainty in *Caveats* rather than smearing hedges through it.
- **Flag load-bearing assumptions for empirical testing.** When a conclusion hinges on
  something the logs can't fully prove — cross-account behaviour only visible in
  CloudTrail, a TXT record's actual propagation, a slot still detaching — say it's
  load-bearing and point at where to confirm it.
- **Name the owning account/service.** Naming the owner turns an analysis into an
  actionable ticket.
- **Write for two readers at once.** A manager reads *Summary → Impact → Next steps*
  and stops. An engineer reads *Timeline → Root cause → Appendix*. Both paths must work.
- **Severity and status are explicit.** Use the org's scale; don't invent one.
- **Carry the `[as-deployed]` / `[current]` / `[inferred]` markers** through from the
  corpus into the writeup — see the epistemics section in SKILL.md.

## How to build it from a finished triage

1. **Fill the header first** — identity, environment, region, account, UTC window
   straight from the triage. This frames everything.
2. **Write the Summary last but place it first.** The cleanest verdict sentence is
   obvious only once the timeline is laid out.
3. **Lift the timeline and add the "what it proves" column.** The triage already has
   the ordered events; the value you add is the significance of each — the column
   that turns a log dump into an argument.
4. **State the root cause in one sentence, then cite the deciding line.** Name the
   R-class alongside it.
5. **Quarantine the noise.** Move every logged-but-benign error to *Not the cause* /
   *Secondary issues* with an explicit "did not cause / did not block" note.
6. **Order remediation by authority.** Authoritative source → resolve the specific
   role/ARN → ask the owning team. Attach an owner where known.
7. **Write the caveats honestly**, then name the observability gap and file it.

## Worked examples

Two complete writeups, one per layer. Handing the agent a real past writeup anchors
output to this standard far better than abstract rules — prefer adding your own
actual RCAs here over relying on these alone.

- `example-privatelink-dns-timeout.md` — control plane (domain D1). Clean root cause,
  a real-but-off-path secondary issue, caveats that refuse to pick among three causes
  the logs can't separate.
- `example-pde-hardstop.md` — on-host (domain D2). Timeline-driven, and the stuck
  `expand vconfig` marker correctly framed as a downstream symptom.

## Common mistakes to avoid

- **Burying the verdict.** The root cause belongs in the first sentence.
- **Merging secondary noise into the root cause.** The ServiceNow "Org not found" and
  the stuck `expand vconfig` marker are the textbook traps — both real, neither the
  cause.
- **Fabricating to fill the template.** An empty slot is fine; an invented ARN or
  timestamp destroys trust in every other claim.
- **Vague identifiers.** "a permissions error", "the network service", "around 7am".
- **Local timestamps or mixed offsets.**
- **Over-confident causes the logs can't support.**
- **Writing only for the engineer (or only for the manager).**

## Pre-publish checklist — the verdict hazards (registry, verbatim failures)

Run this before posting; each item has produced a confidently-wrong RCA in the corpus:

1. **Does every piece of evidence carry this ticket's own CE ID?** If the proof came
   from a different engine, the verdict is `Cause not verified`, not `Duplicate`.
2. **Was anything mitigated before snapshotting?** If evidence was destroyed, say so
   explicitly; do not reconstruct it from memory.
3. **Same window ≠ same cause.** Merge tickets only when A's fault makes B's symptom
   *unavoidable*; opposite failure directions are always different bugs.
4. **LMO/CloudWatch TTLs**: state which evidence is already unrecoverable (24 h / 7 d).
5. **No secrets** in the comment — keys or credentials pasted earlier get rotated and
   raised separately.

## Artifact 2 — verification + closure (SOP S8/S9)

Attach the verification contract to the fix, not to hope:

```
Fixed in      : <BOM / repo + version>
Verified by   : <owner>
Test          : <the exact repro from S1>
Sample size   : N attempts on M sites   — N must beat the original failure ratio
Clouds        : AWS ☐  Azure ☐
CE types      : dedicated ☐  pooled ☐  autoscale ☐
Adversarial   : for R1 fixes — stop-right-after-start, slow DDL, concurrency
Regression    : <link to the automated test that now covers this>
```

Close with a resolution that ships an artifact: `Code Change` needs BOM + verification;
`Duplicate` needs the link **and** your evidence moved to the master; `Not a Problem` /
`Functions as Expected` ships a doc or test change; `Cannot Reproduce` states attempt
count on the *original* build. Never close with an unexplained state change.

## Artifact 3 — the registry row (SOP §11.2)

Append to `references/CE-SIGNATURE-REGISTRY.md` (and its workspace original):
signature (grep-able strings) · symptom · mechanism proven · class · confirm step ·
Jira / fix. Numbering is **append-only and stable** — take the next number even if it
lands out of section order; letter-suffix (#16a/#16b) when one signature turns out to
have a second mechanism; a recurrence records **both** Jira keys — recurrences are the
strongest possible argument for a regression test.

## Rendering

The default deliverable is the Jira comment, pasted inline; Markdown is the default
for the standalone document, because it drops cleanly into Slack, Confluence, a GitHub
issue, or a ticket. For a formal shareable document (.docx/.pdf with TOC, styled
tables, charts), hand the finished content to the **`report-builder`** skill — do not
rebuild document plumbing here.
