---
name: ce-ops
description: Teradata VantageCloud Lake Compute Engine (CE) ops - knowledge base, log triage, and RCA writeup. Use when a CE or site is stuck, failed, degraded, or won't start (FAILED_PROVISIONING, NOT_PROVISIONED, down/hardstop, stuck Starting/Stopping, expand vconfig), when analyzing CloudWatch Logs Insights exports, messages syslog or Salt minion logs, when you need the root cause of a CE incident or Jira, and when writing it up as an RCA, postmortem, incident report, or ticket summary. Also use when asked how CE architecture, pooled/dedicated provisioning, autoscale, OMS/QueryGrid/PrivateLink or state semantics work, or why the console, API and engine disagree on status. Triggers on GCS/cog-global-compute, accp-metadata-service, LMO, SCOrch, ce-autoscaler, CE/site IDs (CEAM*/CEAZ*, TDICAM*/TDICAZ*), error codes (4529/9710, 7825, ARM 409, Event 13912), even a bare CE ID plus a state word. Bundles tested log parsers - use them, never parse inline. Not for Teradata SQL/DBA work or CE feature development.
license: internal
---

# CE Ops — know it, triage it, write it up

This skill is one pipeline with three capabilities, built from **232 coding-agent
sessions**, **535 Jira issues / 3,462 comments**, and a verification pass against the
service repos. Nothing here comes from READMEs (repeatedly proven stale — evidence only).

| Capability | Module | Job |
|---|---|---|
| **KNOW** | `references/` — seven corpus documents (map below) | Architecture, flows, state semantics, APIs, runbook, engine internals, known defects |
| **TRIAGE** | `triage/TRIAGE.md` + `scripts/` | Root-cause a stuck/failed CE from CloudWatch JSON exports, `messages` syslog, Salt `minion` logs |
| **WRITE** | `rca/RCA-WRITEUP.md` + `rca/example-*.md` | Turn a concluded investigation into the RCA comment or a shareable document, plus verification contract and registry row |

They compose in that order: knowledge frames the question, triage produces the
conclusion, the RCA module makes it durable. **Dedup comes before all of it** — most
"new" failures are one of ~120 known problems; grep the signature registry before doing
anything expensive.

## 30-second orientation (read once, keep in mind)

A user asks **GCS** (`cog-global-compute`) to start a CE. GCS reads the config from the
**Metadata Service** and hands it to **SCOrch** (dedicated) or the **Pooling Service**
(pooled). Those bring up nodes running the baked engine image, which configure themselves
with **Salt**. Once compute is up, GCS asks **LMO** to run post-provisioning (QueryGrid +
Viewpoint + STC) while OMS registration happens on its own track. GCS reconciles into two
stores — its own DynamoDB row and the metadata `state` the console renders. Meanwhile an
on-node **metering agent** publishes health to SNS every 5 minutes, and the Metadata
Service **writes that straight back into the same `state` field**. Most production
incidents are consequences of that last sentence.

Identifiers you will see in every log and ticket:

| ID | Shape | Meaning |
|---|---|---|
| site ID | `TDICAM00000EX01` (AWS), `TDICAZ…`/`ESAZ…` (Azure) | The VantageCloud Lake site; partition key for most data |
| CE config ID | `CEAMEXAMPLE10001X` — `CE` + `AM`/`AZ` + 8-char org + base36 counter | Minted by Metadata Service; the CE's primary key |
| cluster/component ID | UUIDv7 (pooled) or `comp_xxx` (SCOrch) | The realised cluster |
| run_id | UUID | An **LMO** workflow run — **history TTL is 24 h, capture early** |

Vocabulary trap: there is no single "status". Five vocabularies coexist — GCS DynamoDB
`status`, pooling PostgreSQL `cluster.state`, metadata `state` (what the customer sees),
the on-engine `cluster:state` Salt grain (autoscale; cloud-invisible), and per-resource
sub-statuses (`oms_status`, `qg_status`, …). When someone says "the CE is X", first
establish **which store** says X. Divergence between stores is itself a finding.

## The knowledge corpus (`references/`)

The seven documents cross-reference each other **by these exact filenames** — do not
rename them. Commands quoted *inside* them use bare sibling filenames
(`grep -in "9710" CE-SIGNATURE-REGISTRY.md`); prefix `references/` when you run them.
The files are large; **grep for anchors, IDs, and error strings rather than reading
whole files** (`grep -n "^## \|^### " <file>` gives a live table of contents).

| File | Role | Open when |
|---|---|---|
| `CE-KNOWLEDGE-BASE.md` (~2,700 lines) | **The umbrella.** Orientation, architecture diagrams, environments, flows F1–F20, state models §5, cross-cutting auth §6, incident casebook §7, operational runbook §8 (accounts, log groups, DynamoDB tables, CLI recipes, on-node commands, triage tree), design-gap + code-defect register §9 (KB gaps D1–D15), glossary | Default destination for any conceptual or operational question |
| `CE-DEBUG-SOP.md` | **The workflow.** S1 CAPTURE → S10 REGISTER pipeline, fault domains D1–D7, root-cause classes R1–R7, evidence rankings, routing rules, verification contract, severity time-boxes | Any debugging/triage/RCA task — follow it, don't improvise |
| `CE-SIGNATURE-REGISTRY.md` | **The lookup table.** 60+ grep-able failure signatures with proven mechanisms, confirm steps and Jira keys; secondary-noise list; environment gotchas; verdict hazards | Pipeline step S2 (dedup) and whenever an error code / log phrase appears |
| `CE-PROVISIONING-DEPROVISIONING.md` | **Provisioning deep dive.** Repo-by-repo walkthrough, auth model, full inter-service API matrix, deploy container + CloudFormation phases, image build pipeline, data stores, per-repo quick reference | How provisioning/teardown is implemented, which API calls what, CFN/Salt/Packer detail |
| `CE-LIFECYCLE-AND-AUTOSCALE-ANALYSIS.md` | **Lifecycle deep dive.** Complete notification/event catalogue (verbatim payloads), the six state machines, START/STOP walkthroughs, autoscaler internals, the autoscale "degraded→STOPPED" root cause with `file:line` evidence, lifecycle defects 1–27, who-writes-which-field (Appendix A), vocabulary mismatch matrix (Appendix B) | Events/notifications, state-machine transitions, autoscale, status-divergence bugs |
| `CE-DATA-PLANE.md` | **Inside the engine.** Leader/follower vprocs, the map system and why autoscale versions the spool map, the NLB's non-role, the FirstConfig ruleset, OMS/MCS object capture & replay, collections (what `collection_id` names), the database hierarchy, the three roles, the persistence contract, and the documented limitations that mimic D3/D6 defects | Objects or grants missing after a stop/start, "the CE is slow", spool-map/drain questions, role and grant scope, anything in SOP domains D3–D6. **Design-doc sourced, not code-verified** |
| `diagrams-mermaid-code.md` | **Legacy diagram pack.** 8 importable Mermaid diagrams: full architecture, SCOrch + pooled provisioning sequences, network/PrivateLink, auto-suspend, LMO workflows, image build pipeline, data-store/queue map | When a renderable diagram or sequence view is wanted — **but it predates the verification pass**: Provisioning §16 lists **16 verified corrections against this file** (e.g. pooling is ECS Fargate/Litestar/ALB with Ping-JWT, not the Lambda+SQS+IAM shown in Diagram 1). Cross-check every arrow against Provisioning §16; labels contain em-dashes, which break the KB §9.4 pure-ASCII render rule — sanitise before `mmdc` |

Routing by question:

| The user asks… | Go to |
|---|---|
| What is a CE / who's who / mental model | KB §1–§2 (2.3 "who owns which state" is the single most useful diagram) |
| Trace a flow end-to-end (onboarding, start, stop, delete, autoscale, OMS/QG, JWT logon, image build, auto-resume, fleet health, RCA, warm pool…) | KB §4 — flows **F1–F20** |
| What does status X mean / who writes it / why do UI, API and engine disagree | KB §5 + Lifecycle Part 2 + Appendix A/B |
| Why is this CE stuck/failed; analyse this Jira | **Pipeline** (below) + registry + KB §7 casebook + KB §8.3 triage tree |
| Log files in hand (CloudWatch JSON export / `messages` / `minion`) | `triage/TRIAGE.md` + `scripts/` — never parse inline |
| Is this a known issue / what does this error code mean | `grep -in "<code or phrase>" references/CE-SIGNATURE-REGISTRY.md` |
| Account IDs, log groups, DynamoDB tables/keys, CLI one-liners, on-node commands | KB §8.1–8.5 |
| Which API does service A call on service B; auth/token model | Provisioning §4–§5; KB §6.1 |
| What is broken by design / open defects | KB §9 (KB gaps D1–D15 + code-defect table) + Lifecycle Part 6 (lifecycle defects 1–27) + registry open rows |
| Has this behaviour changed since the incident was analysed? | KB §0 verification stamp + **KB §5.6 behaviour changes** |
| Show me a diagram of X / render the architecture | KB §2 diagrams (verified) and §8.3 triage tree first; `diagrams-mermaid-code.md` for sequence/pipeline views — apply Provisioning §16 corrections before citing |
| Recommended fixes — code-level and systemic | Lifecycle Part 7 + SOP §14 programme-level fixes |
| Investigation concluded — write it up / close the ticket | `rca/RCA-WRITEUP.md` (Jira comment + shareable-document templates, house style, verification contract, closure rules, registry row); `rca/example-*.md` for two full worked writeups |
| Objects, grants, views or UDFs missing after a stop/start | `CE-DATA-PLANE.md` §9 persistence contract, then §10 limitations — **before** collecting D3 evidence |
| What runs inside the engine; why a query is slow; what a spool map is | `CE-DATA-PLANE.md` §1–§4 |
| Which cloud account is a thing in / cross-account failures | KB §2.5 boundaries diagram + §8.1 account map |
| Acronym I don't recognise | KB §10.1 glossary |

### ID namespaces — cite unambiguously

Several numbering schemes coexist across the documents, and **three of them collide**.
Use the qualified forms below; the source docs themselves are not fully consistent (the
registry writes both "KB D1/D2/D7/D9" and bare "gaps D1/D2/D7" for the same thing, and
the SOP uses "S4" for a pipeline step and "S4" for a triage signature three lines apart).

| Scheme | Range | Home | Cite as |
|---|---|---|---|
| Pipeline steps | S1–S10 | SOP §1 (CAPTURE…REGISTER) | "step S4" — bare `S#` defaults to this |
| Triage signatures | S1–S7 | `triage/TROUBLESHOOTING.md` | "signature S4" |
| Fault domains | D1–D7 | SOP §4; registry section headers | "domain D2" — bare `D#` defaults to this |
| Design gaps | D1–D15 | KB §9.1 | "KB gap D7" |
| Failure signatures | #1–#62 (+ a/b/c suffixes) | registry | "registry #16b" |
| Lifecycle defects | 1–27 | Lifecycle Part 6 | "lifecycle defect 16" |
| Root-cause classes | R1–R7 | SOP §7 = registry legend | "R2" — consistent everywhere |
| Flows | F1–F20 (+F9b) | KB §4 | "F9b" — unique |

Never write "defect 16", "D7", or "S4" unqualified: registry #16 (Event 13912 PDE
reconcile) and lifecycle defect 16 (pooled OMS wedge) are unrelated; KB gap D7
(Azure-only self-heal) is not fault domain D7 (state/UX) — confusingly, **both** apply
to the same flagship incident, GPSC-3907. The triage playbook's signatures are deep-dive
treatments of specific registry rows — full S↔# cross-map in `triage/TRIAGE.md`
(signature S1 = registry #1; S2 = #16a; **S5/S6/S7 have no registry row yet** and should
be filed as #63+ per the registry's own maintenance rule) — when a signature matches,
cite both IDs.

## The pipeline (condensed SOP — read the full SOP for any real incident)

The corpus finding that motivates everything: **slow investigations aren't harder — they
start with opinions instead of artifacts; a fifth of closures are duplicates or
non-problems, and ~120 distinct problems account for all 535 tickets.** So:

```
S1 CAPTURE → S2 DEDUP → S3 CLASSIFY → S4 EVIDENCE → S5 TIMELINE → S6 CAUSE
                                                                      │
S10 REGISTER ← S9 CLOSE ← S8 VERIFY ← S7 ROUTE/FIX ←──────────────────┘
```

1. **CAPTURE** the identifier chain first: cloud/env, BOM, site ID, CE config ID, cluster
   UUID, CE type (dedicated/pooled/autoscale), **UTC incident window (mandatory)**, exact
   error verbatim, and the **attempt ratio** ("3 of 10"), never "sporadic". If the user
   hasn't given these, ask for the missing ones before deep-diving.
2. **DEDUP** — grep the registry for the error code and the distinctive log phrase, and
   suggest the three JQL searches from SOP §3. A registry hit means: run that row's
   **Confirm** step (mandatory — same code, different mechanism is common: `9710` has
   three proven mechanisms, `Event 13912` two). On confirm, point at the master Jira; do
   not open a fresh investigation.
3. **CLASSIFY** into fault domain D1–D7 with the seven ordered questions (first "yes"
   wins): never reached RUNNING → domain D1 control plane; RUNNING but DB dead → D2
   on-host; objects missing/stale → D3 MCS/BCM; `Failure NNNN` → D4 DBS; external data →
   D5 OTF/NOS; logon/token/grant → D6 auth; only the *reported* state is wrong → D7.
   Three traps: a stuck stage is a symptom (`expand vconfig` on top of `DOWN/HARDSTOP`
   is D2, not D1); RUNNING is not a health check (status vs reality disagreement is a
   *second*, separately-filed D7 issue); and **always check whether the CE autoscaled
   inside the window** — autoscale contaminates D3/D4/D5 findings.
4. **EVIDENCE** on day 0, raw, UTC-stamped, outside-in; per-domain collection order in
   SOP §5. **With log files in hand (domains D1/D2), switch to `triage/TRIAGE.md`** and
   its W1–W6 workflows — inventory before loading, `scripts/parse_cloudwatch.py` for
   control-plane JSON, `scripts/triage_syslog.sh` for on-host syslog, checkpoint files
   for long sessions. Start control-plane triage by reading the **two DynamoDB rows**
   (GCS `cluster-provisioning` item, then the metadata config item — KB §8.5 has the
   exact commands); divergence between them is the finding half the time.
5. **TIMELINE** — one table, `UTC · source · verbatim line · what it proves`. Find the
   last transition that fired and the first that didn't; that boundary names the owner.
   End with the explicit noise sentence ("X was the cause; Y and Z logged errors but were
   off the critical path" — the registry's *Secondary noise* section and
   `triage/TROUBLESHOOTING.md` list the usual suspects).
6. **CAUSE** — name the class R1 timing/race · R2 stale state · R3 code · R4 capacity ·
   R5 config drift · R6 infra/network · R7 not-a-defect. If you can't name the class you
   have a hypothesis, not a root cause — say "suspected R2, unconfirmed, next check = X".
7. **ROUTE** on the divergence point with a claim ("X did A at T1; Y did not do B by T2;
   therefore Y or the X→Y contract"). Two teams both saying "not mine" means the defect
   is in the **interface contract** — that pattern (timeout, casing, retry policy) is the
   single largest cause class in the corpus.
8. **VERIFY / CLOSE / REGISTER** — open `rca/RCA-WRITEUP.md`: the RCA comment template,
   the verification contract (sample size must beat the original failure ratio), closure
   resolutions that ship artifacts, the pre-publish verdict-hazard checklist, and the new
   registry row (append-only numbering — never renumber; letter-suffix when one signature
   grows a second mechanism). Always name the **observability gap** — the missing log
   line that would have made this 10× faster — and file it as a ticket.

Severity time-boxes and the full quick-reference card are in SOP §12–§13.

## Epistemics — how much to trust what you read

These rules come from the documents themselves and from real investigation failures
(registry "Verdict hazards"). Apply them to your own answers:

- **Carry the markers.** Claims are tagged `[as-deployed]` (what the incident logs
  showed), `[current]` (verified in the working tree, stamped in KB §0), `[design-doc]`
  (stated in an internal design document — intent, not observed behaviour), or
  `[inferred]` (hypothesis). Preserve the distinction when answering.
- **`CE-DATA-PLANE.md` is the one document that was never code-verified.** It is derived
  from internal design docs, so everything in it is `[design-doc]` unless marked otherwise:
  above `[inferred]`, below `[current]` and `[as-deployed]`. It fills real gaps — the SOP's
  own D3 note calls that domain "least documented" — but when it disagrees with a log or a
  code path, the log or code path wins. Its §12 lists the known conflicts (the OMS and LMO
  acronym expansions among them); do not resolve those silently in an RCA.
- **When the documents disagree, precedence follows verification recency:** KB §0 stamp +
  **KB §5.6 behaviour changes** override the deep docs, whose `[current]` claims override
  their own `[as-deployed]` incident narratives (Provisioning §16 likewise corrects the
  older diagrams/SPEC). `diagrams-mermaid-code.md` sits at the **bottom** of this chain —
  it is the pre-verification snapshot §16 was written against; treat its arrows as
  visual scaffolding, never as evidence. The **triage module's factual claims**
  (architecture, accounts, its "two state machines") are observation-era and sit low in
  the chain too — the corpus has already corrected one (GNS owner account; see the
  precedence section in `triage/TRIAGE.md`) — but its *procedures, checkpointing protocol, and
  scripts* are canonical for log handling regardless. Several fixes **changed the state
  machine after the deep docs were written** — check KB §5.6 before describing pooled
  OMS gating, telemetry guards, or state transitions. Ultimate tie-break: the working
  tree itself.
- **`file.go:NNN` references are grep anchors, not addresses.** Line numbers drift
  across trees; search for the quoted symbol instead.
- **Never trust READMEs/SPECs/docstrings in these repos** — the KB documents specific
  load-bearing lies (`get_status()` claims to debounce and doesn't; `CopySecretForQueryGrid`
  claims to poll and doesn't). Code and logs only.
- **Evidence expires**: LMO run history 24 h (Redis TTL); CloudWatch non-prod retention
  7 days; mitigations destroy evidence (snapshot before unblocking; tag
  `HOLD-FOR-EVIDENCE`). Say this early in any live incident.
- **A verdict needs the ticket's own CE ID.** Duplicates closed on another engine's
  evidence, me-too symptom clustering, and "same weekend so same cause" have all produced
  confidently-wrong RCAs — the Aug-2026 UAT batch had 6 tickets, one window, and four
  unrelated causes. Merge only when A's fault makes B's symptom unavoidable.
- **Triage is read-only.** Nothing in the triage module modifies logs, AWS resources, or
  CE state; the bundled scripts are read-only over their inputs.
- The account map in KB §8.1 was observed, not authoritative — **verify before relying**.

## Answering style

- Use the documents' internal IDs, **qualified per the namespace table above** — flow
  F#, step S#, signature S#, domain D#, KB gap D#, lifecycle defect N, cause class R#,
  registry #N, Jira keys. An answer that says "registry #60, KB gaps D1/D2/D7,
  GPSC-3907" is instantly actionable.
- Quote the deciding evidence (log line, code snippet, table row) rather than
  paraphrasing it; that is the SOP's own standard.
- When the user's question spans docs (e.g. "why did the console show STOPPED during a
  scale-up"), synthesise: mechanism from Lifecycle Part 5, KB gaps from §9.1,
  registry #60, fix direction from Lifecycle Part 7 + SOP §14.
- For a formal shareable RCA document (.docx/.pdf), hand the finished content from
  `rca/RCA-WRITEUP.md` to the external **report-builder** skill — don't rebuild document
  plumbing. That skill is **not bundled here and not in this repository**; if it isn't
  installed, say so and deliver the Markdown from `rca/RCA-WRITEUP.md` instead — Markdown
  is the default deliverable and pastes cleanly into Jira, Slack, or Confluence.

## Boundaries

- **Only paths under this skill directory are in package.** If a citation points outside
  `skills/ce-ops/` (another workspace tree, a service checkout, `/tmp/…`, or a one-off
  script), say that it is not shipped here — do not invent the file or its contents.
  Triage and provisioning coverage that matters for incidents lives in-package: KB §8.3
  (decision tree), KB F4–F6, and Provisioning §7–§10.
- Per-service `SPEC.md` / `STATE.md` / `HEALTH_MONITOR_REQUIREMENTS.md` (and similar)
  cited in the corpus are **source-repo grep anchors**, not bundled docs. They are not
  authoritative on their own — prefer code paths, log lines, and the verified corpus.
- Out of scope (shared vocabulary, different work): writing/reviewing application code
  for the CE services themselves, generic AWS/CloudWatch questions with no CE involved,
  NLB/PrivateLink *design* discussions, and non-CE systems (Kubernetes/EKS, RAG
  pipelines).

## Maintenance

The corpus in `references/` is a **snapshot**. When an investigation produces new
knowledge, follow the docs' own maintenance contracts (KB §10.4, registry "Maintenance",
SOP S10): new flow → KB §4 as F21+; new incident → KB §7; new defect → KB §9; new
endpoint/table/log group → KB §8; behaviour change → KB §5.6 + mark the incident fixed;
confirmed root cause → new registry row. Prefer citing code paths or log lines (never a
README), mark every claim as-deployed/current/inferred, and label diagram arrows with the
call and the reason. Scope `[current]` claims with a **commit SHA** (and deploy env or
version when known) — see KB §0 — not with a git branch name alone.

This SKILL.md carries **derived** content — the condensed pipeline, the routing table,
and the namespace table. If the SOP pipeline, registry conventions, module set, or
document structure change, update those sections here in the same pass; a stale
condensation that contradicts the full SOP is worse than none.

**Open corpus hygiene (not blockers for triage):** triage signatures **S5/S6/S7** are
confirmed root causes with no registry row yet (file as #63+ per the registry maintenance
rule).
