# Compute Engine — Standard Operating Procedure for Debugging

**Version 2** — rebuilt from a full-corpus analysis: **535 Jira issues** labelled `CE`
(Nov 2024 → Aug 2026), **3,462 comments** (100% of them, including the long tails),
**637 issue links**, across 31 projects.

**Companion documents**
- `CE-SIGNATURE-REGISTRY.md` — 60+ grep-able failure signatures with proven mechanisms. Used at Step 2.
- `triage/TRIAGE.md` (+ `scripts/`) — control-plane + on-host log forensics. Used at Step 4 for domains D1/D2.
- `rca/RCA-WRITEUP.md` — turns a finished investigation into the RCA comment or a shareable document. Used at **S10 — REGISTER** (§11.1), not Step 9.

---

## 0. The four findings this SOP exists to fix

Everything below is derived from the corpus, not from opinion.

**Finding 1 — Most closed issues never state what was wrong.**
267 of 461 closed issues (**58%**) were closed with *neither* a stated root cause *nor* a
statement of what fixed them. A further 65 issues have a fix with no stated cause. Only
141 of 535 threads (26%) ever contain an explicit root-cause statement.
→ *The knowledge produced by an investigation is usually thrown away.*

**Finding 2 — Once a cause is named, the fix is fast. Reaching the cause is the bottleneck.**
File → root cause: median **1 day**. Root cause → fix: median **1 day**.
Yet overall median time-to-resolve is 5 days and p90 is 48 days.
→ *The long tail is not hard engineering. It is threads that never converge on a cause.*

**Finding 3 — Day-0 raw evidence is the single strongest predictor of a fast resolution.**
Among real investigations (≥5 comments, ≥3 people, n=216):

| | Fast third (≤4 days) | Slow third (≥13 days) |
|---|---|---|
| Days until first raw log/evidence paste | **0** | **2** |
| Threads containing speculation ("suspect / looks like / might be") | 38% | **57%** |
| Threads where someone had to ask for information | 11% | **24%** |
| Threads with a team hand-off | 7% | 12% |
| Median comments / people | 7 / 4 | 11 / 5 |

→ *Slow threads are not harder — they start with opinions instead of artifacts.*

**Finding 4 — The issue stream is ~120 problems refiled 535 times.**
21% of closures are `Duplicate` / `Not a Problem` / `Cannot Reproduce` / `Functions as Expected`.
120 explicit duplicate links; one OTF cluster of 22 tickets resolved to a single missing
exponential backoff; `Error 7825` was filed 26 times; `IDR-301` is documented as a
*recurrence of IDR-278*.
→ *Dedup before investigation is worth more than any individual debugging skill.*

---

## 1. The pipeline

```
 S1 CAPTURE ─▶ S2 DEDUP ─▶ S3 CLASSIFY ─▶ S4 EVIDENCE ─▶ S5 TIMELINE ─▶ S6 CAUSE
   10 min       5 min        5 min          day 0!         30-60 min      name the class
                                                                              │
 S10 REGISTER ◀─ S9 CLOSE ◀─ S8 VERIFY ◀─ S7 ROUTE/FIX ◀──────────────────────┘
```

**The one rule that matters most:** *raw evidence must be in the ticket on day 0.*
Everything else in this SOP is scaffolding around that rule.

---

## 2. S1 — CAPTURE: the evidence pack (10 minutes)

The identifier chain — the join keys that let one person follow a request across six
services and two clouds. Corpus completeness today: site ID 57%, CE config ID 47%,
BOM 37%, **timestamp only 19%**, error code 25%.

```
### Environment
Cloud / env      : AWS | Azure   ·  dev | sit | preprod | prod
BOM / build      : AWS-VCE-CE-2.0.0.0.02
Site ID          : TDICAM00000EX01 · TDICAZ00000EX02 · ESAM00000EXAM01
CE config ID     : CEAMEXAMPLE10001X · CEAZEXAMPLE10001A
Cluster UUID/num : <COMPONENT_UUID>  ("cluster 61")
DNS name         : 019fa30b….ce.preprod.<NONPROD_CLOUD_DOMAIN>
CE type / size   : dedicated | pooled | autoscale · warm pool y/n · 2x/8x/32x

### Incident
UTC window       : 2026-07-27T08:13:00Z – 08:20:00Z      ← MANDATORY
Reported state   : verbatim ("Failed stopping", "down/hardstop", "RUNNING but no logon")
Error code(s)    : Failure 4529 (SubCode 9710) · Error 7825 · ARM 409 · HTTP 400/504
Attempt ratio    : "3 of 10 deployments"   ← never write "sporadic" alone
Blast radius     : one CE | all CEs on site | all sites in env
Last known good  : BOM/date that worked

### Reproduction
Exact statement  : the SQL **with its query-band tag** (/*nsephase2_select_validate_8*/) or API call + payload
Access           : console user, or attached logs, or Grafana/CloudWatch permalink
```

> **Why the ratio matters.** `COG-15345` was described as "happening sporadically" and ran
> for two weeks. `REGULUS-3080` was described as "happens every so often… we need 10+
> deployments to be sure" and had a shipped fix in 5 days. The ratio tells the fixer how
> many runs verification needs.

---

## 3. S2 — DEDUP: search before you debug (5 minutes)

```bash
grep -in "<error code>" CE-SIGNATURE-REGISTRY.md
grep -in "<distinctive log phrase>" CE-SIGNATURE-REGISTRY.md
```
```jql
labels = CE AND text ~ "<error code>"            ORDER BY created DESC
labels = CE AND text ~ "<distinctive log string>" ORDER BY created DESC
labels = CE AND text ~ "<site id>" AND created > -30d
```

Outcomes:
- **Registry hit** → apply the confirm step in that row. If it matches, comment your
  evidence on the master Jira and **increment the recurrence count**. Do not open a new ticket.
- **Open Jira with the same signature** → link `duplicates`, add evidence, stop.
- **Same signature, different subsystem** → new ticket, link `relates to`, state *how* it differs.
- **Nothing** → continue.

> **Warning — same code, different mechanism.** `SubCode: 9710` has *three* proven
> mechanisms (registry #22a/b/c): a disconnected MCS system + null deref, a router abort on
> RepMetadata for untracked objects, and plain DB instability during autoscale. An error
> code is a starting point, never a conclusion.

---

## 4. S3 — CLASSIFY: fault domain in 5 minutes

First "yes" wins. Ordered outermost → innermost, which is how causes stack here.

| # | Question | Domain | Corpus |
|---|---|---|---|
| Q1 | Did the CE ever reach RUNNING? | **D1 Control plane / provisioning** | 51 |
| Q2 | It says RUNNING — is the database actually up (logon + `pdestate -a` NORMAL)? | **D2 On-host / PDE / Salt** | 52 |
| Q3 | DB up — are objects missing/stale after stop-start, or in a global DB? | **D3 Metadata / MCS / BCM** | 51 |
| Q4 | Objects exist — does the query fail with a Teradata `Failure NNNN`? | **D4 DBS / query engine** | 52 |
| Q5 | Failure is in external data (Iceberg/Delta/Unity/Glue/NOS/S3/Blob)? | **D5 Data access** | 229 |
| Q6 | Failure is logon / token / grant / role? | **D6 Auth** | 76 |
| Q7 | Everything works but the *reported state* is wrong (UI ≠ API ≠ engine)? | **D7 State/UX** | 10 |

**Three traps, each proven by the corpus:**

1. **A stuck stage is a symptom.** `configuring / expand vconfig` on top of `DOWN/HARDSTOP`
   is D2, not D1.
2. **RUNNING is not a health check.** COG-15659: UI *and* Global Compute API both reported
   RUNNING while the RDBMS was crashed and in recovery. If status and reality disagree you
   have **two** issues — file the D7 state defect separately.
3. **Autoscale contaminates every other domain.** The corpus repeatedly shows an autoscale
   expansion/contraction happening *underneath* an apparently unrelated query failure
   (REGULUS-3580, REGULUS-3676, REGULUS-3211, IDR-730). **Always check whether the CE
   resized during your incident window** before classifying as D3/D4/D5.

---

## 5. S4 — EVIDENCE: get artifacts into the ticket on day 0

Collect outside-in; stop at the first layer with an error you can time-correlate. Paste
raw excerpts with UTC timestamps — never paraphrase a log line.

### Which evidence actually cracks cases
Measured on the 102 threads where a root cause was declared, by what appeared in or
immediately before the deciding comment:

| Rank | Evidence type | Appears in the deciding comment |
|---|---|---|
| 1 | SQL error text / `Failure NNNN` with SubCode | 21 |
| 2 | Java `Caused by:` chain (OTF/NOS) — **keep the whole chain** | 13 |
| 3 | Cloud API error with status (ARM 409, HTTP 400/403/504) | 11 |
| 4 | DBS streams / PDE event / dump / `gdb` backtrace | 10 |
| 5 | DBQL / `dbc` query output | 10 |
| 6 | MCS streams (`MCSObjectLookup`, `replayItemWithDeps`, `SEQ/CTL/RD` lines) | 5 |
| 7 | Control-plane service log (pool-manager, GCS, Scorch) | 4 |
| 8 | Host output (`du`, `df`, `mount`, `dmesg`) | 3 |

Screenshots rank last. A screenshot of a status field has never resolved an issue in this corpus.

### Per-domain collection order

**D1 — Control plane** → use `triage/TRIAGE.md` (state machine, account map, signatures S1/S4/S6).
`GET` cluster state → CloudWatch Insights filtered to the CE ID → DynamoDB `cluster-provisioning-*`
item → Network service / Route 53 TXT / endpoint-service verification → ECS `pool-manager`
logs (Grafana) and `docker logs router-restapi-restapi-1` on OMS → Azure ARM activity log.

**D2 — On-host** → use `triage/TRIAGE.md` (signatures S2/S3/S5) + `scripts/triage_syslog.sh`.
`curl localhost:22222/provisioning-status` → `pdestate -a` / `get_dbs_state` →
`/var/log/messages` → salt `minion` log → `healthcheck.json` → BYNET UDP 1033/1034 →
`vprocmanager` / `tdinfo` / vconfig GDO → `du -ah /var/opt/teradata`.
Anchors: `HARDSTOP` · `Event 13912` · `Event 13895` · `tpareconfig` · `tosstate` ·
`expand vconfig` · `retcode 61` · `NEWPROC NoGT` · `queue: True`.

**D3 — MCS / BCM** *(largest open cluster, least documented)*
1. MCS system state dump — `System ID / State / Pending State`. `Disconnected` + `Pending: Active`
   means the CE never re-registered (registry #22a).
2. MCS streams: `MCSObjectLookup` → `processMCSObjectLookupRequest` → `replayItemWithDeps`,
   and the counters `created=N alreadyExists=N errors=N`.
3. `sequencer_region1_seq_debug.log` — op-numbers, lock hold times, `No targets found for address`.
4. The DBS side of the same request **plus DBQL for the window** (frequently missing → file
   an observability gap ticket).
5. Catalog JSON integrity; `@@___DDL___@@` (catalog item 0) state; `establishTargetSystems` bitmask.

**D4 — DBS** → streams log → dump/backtrace (`gdb`) → DBQL → `ctl`/`xctl`/`dbscontrol` diff
**pooled vs dedicated** (they legitimately differ — COG-15055) → TDWM/TASM rulesets → ResUsage.
Always capture the failing SQL with its query-band tag, session id, AMP/vproc, and whether a
dump was collected.

**D5 — OTF / NOS** → the full `Caused by:` chain, catalog type (Glue/Unity/Polaris), object-store
status code, NOS-connector logs with FAF logging enabled, and **retry/backoff behaviour**.

**D6 — Auth** → CIDS token `exp` vs request time, token-exchange call to OMS, TDGSS logs,
`logmech=JWT` + `logdata`, grants present in `dbc` vs expected, and per-CE password rotation
after restart.

**D7 — State/UX** → capture **three** states at the same instant: UI screenshot, API response,
engine probe. Without the triple this always closes "cannot reproduce".

---

## 6. S5 — TIMELINE: find the divergence point

One table. Every row: **UTC time · source · verbatim line · what it proves.**

```
08:13:32Z  pooling-svc  cluster 61 → RUNNING                    happy path reached
08:13:32Z  GC API       PUT /stop → 202                         stop arrived in the SAME second
08:16:09Z  ARM          HTTP 409 Conflict on follower VMSS      collided with in-flight ops → Failed stopping
```

1. **Normalise to UTC immediately** — mixed offsets have inverted causality here before.
2. **Last successful transition + first that didn't fire** = the owner.
3. **Diff against a known-good run** on the same site — the best control available.
4. **Write the noise sentence explicitly:** *"X failed (root cause); Y and Z logged errors but
   were not on the critical path."* See the registry's "Secondary noise" section.

---

## 7. S6 — CAUSE: name the class

Every root cause in 535 issues fits one of seven classes. The class predicts the fix *and*
the regression test.

| Class | Tell | Corpus exemplars | What the fix must be |
|---|---|---|---|
| **R1 Timing / race / ordering** — *the largest technical class, and it lives at service boundaries* | works on retry; two ops in the same second; a timeout just short of reality; "sporadic" | Salt nested `state.sls` with `queue: True` deadlock (REGULUS-3080/3549); ARM 409 from stop-in-same-second-as-RUNNING (REGULUS-3737); DBS 5-min lazy-load ceiling vs slow DDL (NOS-14560); OMS-registration timeout (COG-15345); Iceberg API called before Scorch finished (TCAWORK-7927); sequencer establishes sessions synchronously with **no timeout** (IDR-685); single-sequencer window (IDR-623); spool-map dropped between planning and execution (REGULUS-3580/3507/3676) | idempotency + serialisation + bounded waits. If you raise a timeout, **publish the real latency distribution** you measured |
| **R2 Stale / unpersisted state** | a setting applies once then vanishes; UI ≠ API ≠ engine; stale marker file | `qg_status` never written to GCS (GPSC-3482); `oms_status` stuck DEREGISTERED (GPSC-3394); pooling RUNNING vs metadata NOT_PROVISIONED (GPSC-3751); stale `establishTargetSystems` bitmask (IDR-288); stale `healthcheck.json`; NOSCACHE not remounted (REGULUS-3693) | write-through + a reconcile loop; make the engine the source of truth |
| **R3 Code defect** | deterministic with a specific input | null deref on a new path (IDR-121); `LIKE` instead of exact match (IDR-328); `JSON_ENSURE_ASCII` on Unicode DDL (IDR-704); 4096-byte parser buffer (IDR-311); `readObjects.count==0` gate bypass (IDR-323); missing bare privilege keywords (IDR-300); un-backticked column names (OTF-9555); 64-char `roleSessionName` (OTF-7914) | a unit test at the exact input class, **then** the fix |
| **R4 Capacity / limits** | only at scale or after long uptime | connection-pool session leak → 302 sessions → Error 8024 (TCAWORK-8295); core files 103 GB of 141 GB (PDEF-4501); `Too many open files` (OTF-6322) | quota + backpressure + an alert **before** the limit |
| **R5 Config / environment drift** | works on site A not site B; pooled ≠ dedicated; upgraded ≠ new | `ctl` settings differ pooled vs dedicated (COG-15055); manifest not uploaded to Scorch (REGULUS-3077); upgrade path doesn't migrate proxy certs (IDR-91); WAF rules (VP-64552); SLES 15 SP7 non-static disk order (REGULUS-2974) | make the config declarative and diffable across CE types and upgrade paths |
| **R6 Infra / network** | 504s, DNS, PrivateLink, 403/409 from a cloud API | `ce-secrets` 504 (GPSC-3857); site-gateway DNS unresolvable (COG-15036); PrivateLink DNS verification timeout (CCP-11948, COPS-25103); S3 inaccessible during resync (OTF-7967) | route to the owning account/service **and** add the missing connectivity assertion to provisioning |
| **R7 Not a defect** | intended behaviour, doc gap, or test-setup error | lazy-load timeout by design (IDR-722); undocumented extra grant for IDP users (REGULUS-2959); SSH-tunnel instability (COG-13187); reused test DB name (IDR-449) | **still ship an artifact** — a doc change or a test fix. 94 issues closed as non-defects; the ones with no artifact came back |

> **Discipline:** if you cannot name the class, you do not have a root cause — you have a
> hypothesis. Write *"suspected R1, unconfirmed, next check = X"*. Speculation appears in
> 57% of slow threads and 38% of fast ones; the difference is whether it was labelled and
> then tested.

---

## 8. S7 — ROUTE: ownership by evidence, not by org chart

The corpus's most expensive pathology, verbatim from COG-15345:
> *"how can this be an MCS issue. We're trying to connect and we can't. Moving it back to COG."*
> → *"if the MCS can't connect, there might be something else going on from a network
> perspective… Moving this back to COG."* → *"Which team would be the most appropriate team
> to move this Jira to?"* (NOS-14560)

Both teams were **correct** that it wasn't their component. Nobody owned the interface.

**Rules**
1. **Route on the divergence point**, not the symptom. The team owning the transition that
   failed to fire owns the ticket.
2. **A hand-off must carry a claim:** the timeline, the divergence point, and one sentence —
   *"Component X did A at T1; component Y did not do B by T2; therefore Y, or the X→Y contract,
   is next."* A hand-off without a claim is a bounce.
3. **If two teams both say "not mine", the defect is in the interface** — escalate to the owner
   of the *contract* (timeout, retry policy, payload schema, identifier format), not to either
   endpoint. Exactly what COG-15345 (timeout), REGULUS-2757 (CE-id casing) and NOS-14560
   (5-minute ceiling) turned out to be.
4. **Two-hop budget.** A third hop triggers a 30-minute joint call with the timeline on screen,
   and the outcome is written back to the ticket. 42% of issues reference a meeting; almost
   none record what the meeting concluded.

### Symptom → component (from the fix comments that actually shipped)

| Symptom class | Component / repo |
|---|---|
| Node bring-up, expansion, vconfig, Salt orchestration | `avcd-vce-engine-configure` (33 fixes), `avcd-vce-engine-provision`, manifest `ce` (65) |
| Autoscale expansion/contraction, warm pool | `cog-compute-engine-autoscaler` (29), `cog-compute-engine-pooling-service` |
| CE config lifecycle, scheduler, status | `cog-global-compute` (GCS) |
| Secrets / IAM at provisioning | manifest `ce-secrets` (31) |
| Object metastore, registration, lazy load | manifest `oms` (34) / MCS → **IDR** |
| Health & status reporting | `avcd-healthcheck-service` |
| Query engine `Failure 3xxx/45xx/6xxx`, autoscale-map bugs | REGULUS / SQLE / OPT |
| `Error 78xx/63xx/6881` on Iceberg/Delta/Unity | OTF |
| `read_nos` / write NOS / S3 / Blob | NOS |
| JWT / CIDS / token exchange | TDGSS + `accp-identity-service` |
| UI / console state | CCP / VAC / `con-console` |
| Telemetry collector | TCAWORK |

### Error code → first owner
`7825`,`6321`,`6881` → OTF · `4529`/`9710`,`6692`,`3807` → IDR (MCS) · `3523`/`3524` → REGULUS/IDR ·
`9134` → OPT/NOS · `8024` → sessions (TCAWORK/DBS) · `9565` → REGULUS (Unity) · `5602` → UDF ·
`9562`,`11513`,`7487`,`2640` → REGULUS (autoscale/global-map).

---

## 9. S8 — VERIFY: state the contract before the fix ships

Three issues in the corpus (NOS-14561, CCP-13921, IDR-244) were declared fixed and then broke
again. Typical closure dialogue: *"Did you see this again?" — "Not today." — "How many times
did you attempt?"*

```
Fixed in      : BOM AWS-VCE-CE-2.0.0.0.05  (repo + version)
Verified by   : <SIT owner>
Test          : <the exact repro from S1>
Sample size   : N attempts on M sites   ← N must beat the original failure ratio
                (3-in-10 needs ≥20 clean runs, not 1)
Clouds        : AWS ☐  Azure ☐          ← Azure-tagged issues are 67; AWS-only verification is a known gap
CE types      : dedicated ☐  pooled ☐  autoscale ☐
Adversarial   : for R1 fixes — stop-immediately-after-start, slow DDL, concurrent requests
Regression    : <link to the automated test that now covers this>
```

---

## 10. S9 — CLOSE: resolution hygiene

| Resolution | Use when | Required artifact |
|---|---|---|
| `Code Change` | code shipped | BOM/version + verification record |
| `Non-Code Change` | config/infra change | what changed, where, how it's now enforced |
| `Duplicate` | same root cause as an existing ticket | link `duplicates` **and** move your evidence to the master |
| `Not a Problem` / `Functions as Expected` | genuinely intended | a **doc or test** change |
| `Cannot Reproduce` | after N attempts on the *original* build | attempt count + build; if the build changed, say so |

Never close with an unexplained state change. Today 78 open issues carry no resolution and
35 no assignee.

---

## 11. S10 — REGISTER: leave the two artifacts

Diagnosis that lives only in a meeting is paid for twice. This step is what turns Finding 1 around.

### 11.1 RCA comment on the ticket (use `rca/RCA-WRITEUP.md`)

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

The **observability gap** section is the highest-leverage habit available. It already appears
organically in the best threads — *"is there any way MCS can add more logs about the type of
network failure, such as TCP reset or packet drops?"* (COG-15345); *"no DBQL data available
for the time period"* (NOS-14560) — and is never tracked. **Every gap becomes a ticket.**

### 11.2 Registry row
Add a row to `CE-SIGNATURE-REGISTRY.md`: signature · domain · class · master Jira · confirm
step · fix/BOM. If it is a **recurrence** of an earlier fix, record both keys — recurrences are
the strongest possible argument for a regression test (IDR-301 → IDR-278).

---

## 12. Severity → time-box

| Priority | Classify (S3) | Divergence point (S5) | Owner assigned (S7) | Update cadence |
|---|---|---|---|---|
| Blocker | 1 h | 4 h | 4 h | every 4 h until routed |
| Critical | 4 h | 1 day | 1 day | daily |
| Major | 1 day | 3 days | 3 days | twice weekly |
| Minor | 3 days | — | 1 week | weekly |

Baseline today: 3 open Blockers (one unassigned), 14 open Criticals, **35 of 85 open issues
unassigned**, first response same-day (good), p90 resolution 48 days (the routing tail).

---

## 13. Quick-reference card

```
CAPTURE   site · CE config · cluster UUID · BOM · UTC window · exact error · repro · ATTEMPT RATIO
DEDUP     grep CE-SIGNATURE-REGISTRY.md + 3 JQL searches       → stop if matched
CLASSIFY  Q1 never RUNNING?→D1  Q2 RUNNING but DB dead?→D2  Q3 objects missing?→D3
          Q4 Failure NNNN?→D4   Q5 external data?→D5      Q6 auth?→D6   Q7 state wrong?→D7
          ALWAYS check whether the CE autoscaled inside the window
EVIDENCE  day 0, raw, UTC-stamped, outside-in; whole `Caused by:` chain; no screenshots-only
TIMELINE  last good transition · first missing transition · diff vs known-good · name the noise
CAUSE     R1 timing · R2 stale state · R3 code · R4 capacity · R5 config · R6 infra · R7 not-a-defect
          can't name it? say "hypothesis" and state the next check
ROUTE     on the divergence point, with a claim · 2-hop budget · both say "not mine" → interface owner
VERIFY    N ≥ original failure ratio · both clouds · all CE types · adversarial case · regression test
CLOSE     resolution + artifact; every "Not a Problem" ships a doc or a test
REGISTER  RCA comment · registry row · observability-gap ticket
```

---

## 14. Programme-level fixes this SOP depends on

The SOP reduces waste; these remove its causes.

1. **Give CE a real component/Epic.** Tracked today by a free-text label across 31 projects;
   25% of issue links cross project boundaries. Also strip `CE` from the 29 KNOWLEDGE `KP-*`
   stories — they inflate the open count by a third.
2. **One state-reconciliation design.** GPSC-3907, CCP-14508, COG-15659, REGULUS-3661,
   GPSC-3519, GPSC-3751, GPSC-3394 are one architectural defect — the control plane trusts its
   own record instead of reconciling with the engine — filed seven times.
3. **A stop/start-restart regression suite.** Six open bugs share that single trigger: object
   restore, NOSCACHE remount, JWT re-login after password rotation, lazy loading, SP/macro restore.
4. **Publish interface contracts.** R1 is the largest class and nearly all of it lives at
   boundaries: OMS↔MCS, DBS↔MCS (the 5-minute ceiling), Scorch↔Iceberg, control-plane↔ARM,
   GCS↔config-container (identifier casing). Each boundary needs a published timeout, retry
   policy, idempotency guarantee and identifier format.
5. **Always-on DBQL + network-level MCS logging in SIT/preprod.** Two of the slowest
   investigations stalled purely on telemetry that was not being collected.
6. **Autoscale-aware triage data.** Because autoscale silently contaminates D3/D4/D5, expose a
   per-CE resize timeline that testers can attach to any ticket in one click.
