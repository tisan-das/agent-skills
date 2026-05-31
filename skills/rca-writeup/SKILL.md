---
name: rca-writeup
description: >
  Turn a COMPLETED log/incident investigation into a structured, shareable
  root-cause document. Use this skill whenever an analysis has reached a
  conclusion and the user wants it written up — triggers include "write this
  up", "draft an RCA", "write a postmortem", "incident report", "document this
  for the ticket", "summarize the root cause for the team / for my manager", or
  any request to make a finding legible to people who were not in the logs.
  It is the natural second step after the `ce-log-triage` skill: that skill
  reaches the conclusion, this skill formats it, so offer it proactively the
  moment a triage lands on a cause. Reach for it even when the user does not say
  the word "RCA" — if they have a root cause and an audience, this is the skill.
  Two hard boundaries: (1) this skill does NOT perform the investigation — if the
  analysis is not actually finished, do not invent a root cause; say what is
  still missing and offer to run `ce-log-triage` first. (2) Never fabricate
  identifiers, timestamps, or evidence to make the document look complete; a
  precise "the logs cannot prove this" is the house style, a confident guess is
  not. Applies to Teradata Compute Engine (CE) failures and the surrounding AWS
  infrastructure (PrivateLink/DNS, EKS/Velero restores, DynamoDB, ECS,
  networking, on-host PDE/TPA/Salt), and the template generalizes to any infra
  incident.
license: internal
---

# RCA Writeup

Strong forensic work is worthless if it evaporates in a chat window. The person
who has to approve the fix, the teammate who owns the upstream service, the
manager who needs the one-paragraph version — none of them were in the logs with
you. This skill exists to convert a finished investigation into a document that
serves all of them: a verdict the manager can skim, a timeline an engineer can
audit, and a remediation list the on-call can act on. It is the artifact that
makes the depth of the analysis *visible*.

This skill is the formatting half of a pair. The **`ce-log-triage`** skill does
the investigation — it reads the CloudWatch JSON or the `messages`/`minion`
syslog, walks the state machine, and identifies the root cause. **`rca-writeup`**
takes those findings and renders them in the house style below. Run them in
sequence: triage to reach the conclusion, then "write this up" to get the
document. Do not duplicate the investigation here; consume its output.

---

## Table of contents

1. [When to use — and when not to](#when-to-use)
2. [Input contract: what a finished investigation must hand over](#input-contract)
3. [The output template (use this exact structure)](#the-template)
4. [House-style voice and conventions](#house-style)
5. [How to build the writeup from a finished triage](#how-to-build)
6. [Worked example 1 — PrivateLink DNS verification timeout (control plane)](#example-1)
7. [Worked example 2 — PDE crash → DOWN/HARDSTOP (on-host)](#example-2)
8. [Common mistakes to avoid](#mistakes)
9. [Output mechanics](#output-mechanics)
10. [Bundled resources](#bundled-resources)

---

<a name="when-to-use"></a>
## 1. When to use — and when not to

**Use it when:**
- An investigation has reached a root cause (or a well-bounded "most likely"
  cause with named evidence) and the user wants a document.
- The user says any of: write it up, RCA, postmortem, incident report, "for the
  team/manager", "make this shareable", "document this for the ticket",
  "summarize the findings".
- A `ce-log-triage` run has just concluded and the natural next move is to
  capture it — offer the writeup without waiting to be asked.

**Do NOT use it when:**
- The investigation is not finished. If there is no defensible root cause yet,
  **stop and say so** — list what is still unknown and offer to run
  `ce-log-triage`. A half-finished RCA that papers over the gap with a guess is
  worse than no RCA.
- The user wants live analysis ("why did this fail?") — that is `ce-log-triage`.

**Honesty rule that overrides formatting:** never invent an identifier, a
timestamp, an ARN, or an event to fill a slot in the template. If the timeline
has a gap, show the gap. If ownership is unproven, say "owner unconfirmed —
verify in CloudTrail for account X". The credibility of the whole document rests
on every concrete claim being real. This skill applies most often to CE
failures, but the same structure fits an EKS/Velero restore, a DynamoDB schema
fault, or any infra incident — the conventions are general even though the
bundled examples are CE-shaped.

---

<a name="input-contract"></a>
## 2. Input contract: what a finished investigation must hand over

Before writing, confirm you have these. If something is missing, ask for it or
mark it explicitly as unknown in the document — do not paper over it.

- **Identity:** CE ID (e.g. `CEAMGPSCTEAM0009N`), site ID (e.g.
  `TDICAM53386PP80`), environment (preprod/prod/sit), region/AZ, account ID.
- **The reported failure state** as observed (`provisioning_failed`,
  `down/hardstop`, `PRIVATE_LINK_DEPLOY: FAILED`, `NOT_PROVISIONED`, …).
- **A UTC timeline** of the load-bearing events, each with the evidence that
  supports it (a log line, an event number, an error code).
- **The root cause** — the single thing that, had it not happened, would have
  prevented the failure — plus its deciding evidence.
- **Secondary issues** observed but off the critical path.
- **The owning account/service** for the fault, if determined.
- **Open questions** the logs could not settle.

This is exactly the shape `ce-log-triage` produces, which is why they compose.

---

<a name="the-template"></a>
## 3. The output template (use this exact structure)

ALWAYS produce the document in this order. Sections marked *(optional)* may be
omitted when there is nothing real to put in them — but never delete a section
just to hide a gap; if it's relevant and unknown, keep it and write "unknown".

```markdown
# RCA: <one-line failure description> — <CE_ID>

| Field | Value |
|---|---|
| CE ID | CEAMGPSCTEAM0009N |
| Site ID | TDICAM53386PP80 |
| Environment | preprod |
| Region / AZ | us-west-2 / usw2-az2 |
| Account | 164607045827 |
| Severity | <your org's scale, e.g. SEV3> |
| Status | Investigating · Identified · Mitigated · Resolved · Monitoring |
| Window (UTC) | 2026-05-28 07:22 – 07:48 |
| Author | <name> |

## Summary
One or two sentences: the verdict. The reader who only reads this line should
walk away knowing what failed and why.

## Impact
What was affected, for whom, for how long. (For a single stuck CE this may be a
single line; for a fleet-wide event, quantify it.)

## Timeline (UTC)
| Time (UTC) | Event | What it proves |
|---|---|---|
| 07:22:39 | … | … |
Each row pairs an event with its significance — not just "what the log said" but
"what it establishes". Normalize every timestamp to UTC.

## Root cause
State it plainly in one or two sentences, then give the deciding evidence
(the exact error code / event number / log location). No hedging once the
evidence supports the verdict.

## Supporting evidence   (optional, if not already clear from the timeline)
The specific lines/codes that pin the root cause — quoted or cited exactly.

## Contributing factors   (optional)
Conditions that made the failure more likely or worse but were not the trigger
(e.g. an aggressive timeout, sustained CPU before a scale-up).

## Secondary issues
Real problems observed in the logs that did NOT cause this failure. Keep them
visibly separate from the root cause so no one mistakes a logged-but-benign
error for the trigger.

## Recommended next steps
Ordered by authority of evidence: confirm in the authoritative source first
(e.g. CloudTrail in the owning account), then resolve the specific role/ARN,
then "ask the owning team" only when the logs can't settle it. Each step is
actionable; name an owner where known.

## Caveats — what the logs cannot prove
The honest limits. Any conclusion that rests on a load-bearing assumption the
logs can't fully prove is flagged here with where to verify it.

## Appendix   (optional)
Key raw excerpts, ARNs, IDs, code locations — for the engineer who wants to
re-walk the evidence.
```

---

<a name="house-style"></a>
## 4. House-style voice and conventions

These are the things that make a writeup *trustworthy*, and exactly the things a
generic model flattens. They are not decoration — each one is load-bearing.

- **Name exact identifiers.** Account IDs, ARNs, `vpce-svc-…`/`vpc-…`/`subnet-…`
  IDs, error codes (`AWS-AssignSlot-Error`), event numbers (`Event 13912`),
  retcodes (`retcode 61`), CE/site IDs, code locations (`ce_manager.go:122`),
  the actual TXT-record name. Never write "a permissions error" when the log
  gives you the code; never write "the network service" when you can name the
  account. Specificity is what lets a reader verify you.

- **Distinguish "what failed" from "what merely logged an error."** This is the
  single most important discipline. A failure log is noisy; most error lines did
  not cause anything. Put the cause in *Root cause* and everything else in
  *Secondary issues*, and say which is which in plain words.

- **Timestamps are precise and in UTC.** Logs from different services carry
  different local offsets; normalize to UTC immediately and label it. A timeline
  in mixed offsets silently lies.

- **State the verdict plainly; hedge only where evidence runs out.** Once the
  evidence supports the root cause, say it flatly ("Root cause: VPC endpoint
  service private DNS verification timed out"). Save the hedging for genuine
  unknowns, and put those in *Caveats*, not smeared through the verdict.

- **Flag load-bearing assumptions for empirical testing.** When a conclusion
  hinges on something the logs can't fully prove — cross-account behavior only
  visible in CloudTrail, a TXT record's actual propagation, a slot still
  detaching — say it's load-bearing and point to where to confirm it, rather
  than trusting the spec.

- **Name the owning account/service.** Every state transition lives in a specific
  service in a specific account; naming the owner is what turns an analysis into
  an actionable ticket.

- **Write for two readers at once.** A manager reads *Summary → Impact → Next
  steps* and stops. An engineer reads *Timeline → Root cause → Appendix*. Order
  and phrase the document so the skim path and the deep path both work.

- **Severity and status are explicit.** Use the org's severity scale (don't
  invent one); set status to one of Investigating / Identified / Mitigated /
  Resolved / Monitoring so the reader knows how settled this is.

---

<a name="how-to-build"></a>
## 5. How to build the writeup from a finished triage

A short procedure for turning triage output into the document above:

1. **Fill the header first.** Pull identity, environment, region, account, and
   the UTC window straight from the triage. This frames everything.
2. **Write the Summary last but place it first.** The cleanest verdict sentence
   is usually obvious only after the timeline is laid out — draft it once the
   root cause is fixed, then put it at the top.
3. **Lift the timeline and add the "what it proves" column.** The triage already
   has the ordered events; the value you add here is the significance of each —
   the column that turns a log dump into an argument.
4. **State the root cause in one sentence, then cite the deciding line.** If the
   evidence isn't self-evident from the timeline, add the *Supporting evidence*
   subsection.
5. **Quarantine the noise.** Move every logged-but-benign error to *Secondary
   issues* with an explicit "did not cause / did not block" note.
6. **Order remediation by authority.** Authoritative source (CloudTrail, the
   owning service's own log) → resolve the specific role/ARN → ask the owning
   team. Attach an owner to each step where known.
7. **Write the caveats honestly.** Name what the logs can't prove and the
   concrete check that would settle it. This section is what makes the rest
   believable.

---

<a name="example-1"></a>
## 6. Worked example 1 — PrivateLink DNS verification timeout (control plane)

This is the canonical example: a control-plane failure where the root cause is
clean, the secondary issue (a ServiceNow "Org not found") is real but off the
critical path, and the conclusion carries honest caveats. Study how identifiers
are named exactly and how the secondary issue is kept separate.

```markdown
# RCA: PrivateLink deploy failed — private DNS verification timeout — CEAMGPSCTEAM0009N

| Field | Value |
|---|---|
| CE ID | CEAMGPSCTEAM0009N (tisan-standard-ce-opt-issue-recreate-001) |
| Site ID | TDICAM53386PP80 |
| Environment | preprod |
| Region / AZ | us-west-2 / usw2-az2 |
| Account | 164607045827 |
| Severity | SEV3 (single CE, no customer impact) |
| Status | Identified |
| Window (UTC) | 2026-05-28 07:22:39 – 07:48:20 |
| Author | <name> |

## Summary
Provisioning failed because the VPC endpoint service's **private DNS
verification timed out**. The operation `PRIVATE_LINK_DEPLOY` returned
`AWS-AssignSlot-Error` after the system waited ~10 minutes for AWS to verify
domain ownership and the verification never completed.

## Impact
One CE (`CEAMGPSCTEAM0009N`) left in `NOT_PROVISIONED` with connectivity
`FAILED`. No customer-facing impact; this was a recreate/test CE.

## Timeline (UTC) — 2026-05-28
| Time | Event | What it proves |
|---|---|---|
| 07:22:39 | POST to create the private link for site `TDICAM53386PP80` / CE `CEAMGPSCTEAM0009N` (initiated by `gg5338630` via a TD-Ops service-account role; allowed accounts `649897665836`, `862579450314`; DEDICATED, 1X) | Provisioning request accepted; config is well-formed |
| 07:22:39 | Metadata service registered the config, granted IDP access, called the network service — and ServiceNow returned **400 "Org not found"** for org `GPSCTEAM` | A secondary CMDB-sync failure occurred here; it did **not** block the private-link flow |
| 07:22:55 | Network service found no existing slot, created a new NLB `TDICAM53386PP80-046a7c493335-nlb` in `vpc-0c775cb134c335152`, `subnet-09f9d4323310cbdc7` | Network setup progressed normally |
| 07:25:53 | Allowed accounts updated on endpoint service `vpce-svc-0ae02c5903baed7d7`; private DNS enabled for `ceamgpscteam0009n.ce.preprod.qateradatacloud.com` | Endpoint service configured; DNS step begins |
| 07:25:54 | TXT record created (`_tk7bmpatocjfca1qvfem` → `vpce:0geVaDIWve7gV74ULWEh`) to prove domain ownership | The ownership-proof record was written |
| 07:26:00 | Began waiting for AWS private DNS verification (AWS looks up the TXT record) | The AWS-side check started |
| 07:36:03 | After ~10 min, **timed out**; error raised from `ce_manager.go:122`; status set `FAILED`, metadata patched | The verification window elapsed without success — the failure point |
| 07:36:04 | Status record `PRIVATE_LINK_DEPLOY: FAILED`; PATCH to metadata returned 200 | Failure recorded cleanly downstream |
| 07:36:20–07:48:20 | Periodic GET polls (~30–60s) for the CE config returned `NOT_PROVISIONED` / connectivity `FAILED` | The CE settled into the failed terminal state; nothing retried it |

## Root cause
AWS never completed **private DNS domain-ownership verification** for the
endpoint service within the ~10-minute window (07:26:00 → 07:36:03), so slot
assignment failed with `AWS-AssignSlot-Error`, raised from `ce_manager.go:122`.

## Contributing factors
- The verification window is ~10 minutes, which is tight for AWS private DNS
  verification — under regional load or with a high-TTL/complex delegation chain,
  propagation can exceed it.

## Secondary issues
- **ServiceNow `Org not found` (400) for org `GPSCTEAM`** at 07:22:39. This means
  the CE was not tracked in ServiceNow's CMDB — an operational blind spot and a
  metadata↔ServiceNow data-sync problem. It is **not** related to the DNS
  timeout and did not block provisioning.

## Recommended next steps
1. Verify the TXT record `_tk7bmpatocjfca1qvfem.ceamgpscteam0009n.ce.preprod.qateradatacloud.com`
   actually resolves in the Route 53 hosted zone that serves
   `ce.preprod.qateradatacloud.com` (confirm it's the correct zone, not a
   sibling). — *Network Service owner, acct 656901843610*
2. If the record is correct and the zone is right, increase the DNS verification
   timeout in `ce_manager.go` and retry the deployment.
3. Fix the ServiceNow org mapping for `GPSCTEAM` to stop future CMDB-sync gaps.

## Caveats — what the logs cannot prove
The logs show the timeout but not *why* AWS didn't verify. The three live
hypotheses — DNS propagation delay, wrong hosted zone, or too-aggressive timeout
— are not distinguishable from these logs alone. Confirm by checking actual TXT
resolution against the hosted zone before assuming the timeout value is the
problem; that assumption is load-bearing.
```

Note what this example does: every ARN/ID is exact, the ServiceNow error is
quarantined in *Secondary issues* with an explicit "did not block", and the
*Caveats* section refuses to pick among the three causes the logs can't separate.

---

<a name="example-2"></a>
## 7. Worked example 2 — PDE crash → DOWN/HARDSTOP (on-host)

The on-host layer. Here the timeline is the star — the failure is a precise
chain of events on the node, and the root cause is a reconcile failure during an
expansion. Note how the stuck "configuring/expand vconfig" marker is correctly
framed as a *downstream symptom*, not the cause.

```markdown
# RCA: Database DOWN/HARDSTOP after failed online reconfiguration — <CE_ID>

| Field | Value |
|---|---|
| CE ID | <CE_ID> |
| Environment | <env> |
| Severity | SEV2 (database down, not self-recovering) |
| Status | Identified |
| Window (UTC) | 06:40 – 07:21 |
| Author | <name> |

## Summary
An autoscaler-driven node expansion triggered an online reconfiguration that
**failed to reconcile** (PDE Event 13912). PDE forced a TPA restart (Event
13895), the database went **DOWN/HARDSTOP**, and it never recovered.

## Impact
Database unavailable from 06:44:23 onward; the node remained stuck reporting
`expand vconfig / configuring` because the orchestration's completion step was
never reached.

## Timeline (UTC)
| Time | Event | What it proves |
|---|---|---|
| 06:40:47 | `ce-autoscaler` triggered a scale-up after sustained CPU (~85–91%) | The expansion was demand-driven, not spurious |
| 06:43:16 | `state.sls expand vconfig` ran; "2408 bytes successfully written to Vconfig GDO"; healthcheck → `stage:"expand vconfig", state:"configuring"` | The vconfig write succeeded; the operation was underway |
| 06:43:19 | `tdops.run_tpareconfig` set `tosstate RECONFIG`, ran `/usr/pde/bin/tpareconfig` (reported success) | Reconfigure was invoked |
| 06:43:22 | `wait_normal_system_state` poll #1 → `is_normal=False` | Expected post-reconfigure settling |
| 06:43:23 | **PDE Event 13912: "Node needed for online reconfiguration failed reconcile"** → **Event 13895: forced TPA restart** | The reconcile failed — the trigger of the outage |
| 06:43:27 | `/usr/pde/bin/tosstate: PDE is not operational - Cannot open PDE device` (repeats 12× at 5s) | PDE had crashed, not merely "still settling" |
| 06:44:22 | `run_tpareconfig` gave up: "tosstate persistently failing after 12 attempts while waiting for NORMAL" | The orchestration abandoned recovery |
| 06:44:23 | **DBS → DOWN/HARDSTOP** | The database went down and stayed down |
| 06:44:35+ | Retry cycles failed: `tdinfo: can't find node_num 34, byn001-02, in vconfig GDO`; `vprocmanager: PDE is down ... Operation not permitted (retcode 61)` | The GDO did not yet include the new node; retries couldn't proceed |

## Root cause
The new follower node could not reconcile with the leader during the online
reconfiguration (PDE **Event 13912**) — most likely because the **BYNET was
still processing the detachment of a previously decommissioned node at the same
slot**, and the **vconfig GDO had not yet been updated** to include the new node
(`node_num 34, byn001-02` is reported missing). The reconcile failure forced a
TPA restart, which produced a DOWN/HARDSTOP the system never recovered from.

## Secondary issues
- The stuck `expand vconfig / configuring` state is a **downstream symptom**, not
  a cause: the orchestration's success path (which resets the healthcheck to
  `completed/configured`, as it did on the earlier successful expansion at
  05:56:03) was never reached because PDE crashed mid-reconfigure.

## Recommended next steps
1. Recover the **leader database first** — DOWN/HARDSTOP does not self-heal.
2. Verify the slot/`node_num 34` is clean (the prior decommission has fully
   detached on BYNET) before re-running the expansion.
3. Re-run the expansion orchestration once the system is NORMAL; confirm the
   healthcheck returns to `completed/configured`.

## Caveats — what the logs cannot prove
The reconcile failure's precise cause (slot still detaching vs. GDO update
ordering) is inferred from the missing `node_num` and the timing; confirm
against the leader's reconfigure logs and the decommission record for that slot.
This is the load-bearing assumption — verify it before re-adding the node.
```

---

<a name="mistakes"></a>
## 8. Common mistakes to avoid

- **Burying the verdict.** The root cause belongs in the *Summary*'s first
  sentence, not discovered three sections in.
- **Merging secondary noise into the root cause.** The ServiceNow "Org not
  found" and the stuck `expand vconfig` marker are the textbook traps — both are
  real, neither is the cause. Keep them in *Secondary issues*.
- **Fabricating to fill the template.** An empty slot is fine; an invented ARN or
  timestamp destroys trust in every other claim. If it's unknown, say "unknown".
- **Vague identifiers.** "a permissions error", "the network service", "around
  7am" — replace with the code, the account, the UTC timestamp.
- **Local timestamps or mixed offsets.** Always UTC, always labeled.
- **Over-confident causes the logs can't support.** Put genuine uncertainty in
  *Caveats* with a concrete way to resolve it; don't smear hedges through the
  verdict.
- **Writing only for the engineer (or only for the manager).** Serve both paths.

---

<a name="output-mechanics"></a>
## 9. Output mechanics

- **Default to Markdown**, written to a file named
  `rca-<CE_ID>-<YYYY-MM-DD>.md` (e.g. `rca-CEAMGPSCTEAM0009N-2026-05-28.md`).
  Markdown drops cleanly into Slack, Confluence, a GitHub issue, or a ticket,
  which is where these documents actually live.
- For a **formal or executive deliverable**, the same Markdown can be rendered to
  `.docx` or `.pdf` on request — but Markdown is the default; don't reach for a
  heavier format unless the user signals they need one.
- Keep the **metadata header table** at the very top; it makes the document
  legible at a glance and is good postmortem hygiene.
- If the investigation produced raw log excerpts worth preserving, put them in
  the *Appendix*, not inline in the timeline (which should stay scannable).

---

<a name="bundled-resources"></a>
## 10. Bundled resources

- `references/rca-example-privatelink-timeout.md` — the gold-standard worked
  example (the CEAMGPSCTEAM0009N case in Section 6), kept as a standalone
  reference. Handing the agent a real past writeup anchors output to this
  standard far better than abstract rules.
- `references/rca-example-hardstop.md` — the on-host DOWN/HARDSTOP example
  (Section 7), for the node-layer shape.
- `references/rca-template.md` — the bare template from Section 3, ready to copy.

> These references are described here so the skill is complete on its own; if
> they are not yet present in `references/`, generate them as a follow-up (each
> is just the corresponding section extracted to its own file, lightly scrubbed
> of any sensitive identifiers). The skill works from the inlined versions above
> either way. Real past writeups in your own voice are the most effective anchor,
> so prefer adding one or two of your actual RCAs here over relying on the
> synthetic template alone.

---

## Composition note

`ce-log-triage` → `rca-writeup` is the intended pipeline: triage produces the
findings (summary, UTC timeline, root cause, secondary issues, next steps,
caveats), and this skill renders them in the house style. If you arrive here
without those findings, return to `ce-log-triage` first rather than guessing —
the quality of the document is capped by the quality of the investigation behind
it.
