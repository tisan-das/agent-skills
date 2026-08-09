---
name: ce-log-triage
description: Root-cause Teradata Compute Engine (CE) failures from logs. Use when a CE (e.g. CEAMGPSCTEAM0009R) or its site (e.g. TDICAM53386PP80) is stuck, failed, or won't start — states like provisioning_failed, down/hardstop, down/tdmaint, stopped, NOT_PROVISIONED, "configuring / expand vconfig", NETWORK_PROVISIONING, or MANIFEST_CREATING — and the user provides or points at CloudWatch Logs Insights JSON exports (logs-insights-results__NN_.json), a Linux `messages` syslog, or a Salt `minion` log. Also trigger for ANY request like "why is this CE failing / stuck / not starting", "analyze these CE logs", "what's the root cause", or "this engine won't provision" — even just a pasted CE ID plus a state, and even if no file, state, or this skill is named explicitly. The failure trail crosses several AWS accounts and two layers (control-plane vs on-host), so follow this skill rather than guessing from general AWS knowledge or the symptom alone.
license: internal
---

# CE Log Triage

Compute Engine failures are hard for one structural reason: **the lifecycle is
split across services that each live in a different AWS account, and the failure
trail crosses those account boundaries.** A symptom observed by one service
(e.g. "status stuck in `MANIFEST_CREATING`", or a node reporting
`expand vconfig / configuring` forever) is almost always *caused* by a different
service — or a different layer — upstream. Done by hand this takes 30–90 minutes
per incident, demands deep tribal knowledge, and is error-prone because humans
are bad at mentally merging ten interleaved log streams and remembering which
account owns which step.

This skill encodes that tribal knowledge: the architecture, how the services
hand off to each other, both state machines, the exact shape of the two log
formats, and a playbook of failure signatures recovered from real past
investigations. The goal is to go **straight to root cause** instead of
rediscovering the terrain every time.

When the investigation is finished and the user wants a shareable document
(postmortem / RCA / incident writeup), hand the findings to the **`rca-writeup`**
skill — this skill produces the *conclusion*; `rca-writeup` produces the
*artifact*. They are designed to compose.

---

## Table of contents

1. [The two layers — decide which one first](#the-two-layers)
2. [The triage workflow (8 steps)](#the-triage-workflow)
3. [Reference A — System architecture and how services communicate](#reference-a)
4. [Reference B — The two state machines](#reference-b)
5. [Reference C — Log-format anatomy](#reference-c)
6. [Reference D — Failure-signature playbook](#reference-d)
7. [Bundled scripts](#bundled-scripts)
8. [Caveats and honesty rules](#caveats)

---

<a name="the-two-layers"></a>
## 1. The two layers — decide which one first

Before reading a single line in detail, classify the evidence. There are **two
distinct layers**, they fail for different reasons, and they are documented by
different log formats. Getting this classification right up front saves the
whole investigation.

| | **Control plane** | **On-host / node layer** |
|---|---|---|
| What it does | *Provisions and tracks* the CE: authorize → resolve config → create record → provision network/PrivateLink → create manifest → schedule ECS task → run | Brings up the actual **Teradata database** on the CE nodes: PDE, TPA, vconfig, BYNET, Salt orchestration |
| Who runs it | GCS, Metadata, Network, Scheduler, Auto-Suspend, Metering (Lambdas / ECS / SQS across several AWS accounts) | Salt master + minions on the EC2 nodes; PDE / DBS on the box |
| Evidence format | **CloudWatch Logs Insights JSON** — `logs-insights-results__NN_.json` | **Linux syslog** — a `messages` file or a Salt `minion` log |
| Typical stuck states | `NETWORK_PROVISIONING`, `MANIFEST_CREATING`, `provisioning_failed`, `NOT_PROVISIONED` | `down/hardstop`, `down/tdmaint`, `stopped`, `configuring / expand vconfig` |
| Read strategy | Parse JSON with Python (jq is usually absent, files run ~40 MB) | `grep` for anchors + `sed -n` a line window (40k+ lines, middle truncated by any viewer) |

A single incident can span **both** layers, and the most instructive cases do.
The canonical example: the control-plane status shows the CE stuck, but the
*cause* is a PDE crash on the node during an `expand vconfig` reconfiguration
(see signature **S2**/**S5**). When you have logs from both layers, reconcile
them on a shared UTC timeline — the on-host crash timestamp should line up with
where the control-plane status stopped advancing.

---

<a name="the-triage-workflow"></a>
## 2. The triage workflow (8 steps)

Follow these in order. Each step explains *why* it matters, because skipping the
reasoning is how a triage lands on a plausible-but-wrong cause.

### Step 1 — Pin the basics

Establish, and write down, four facts before touching the logs:

- **CE ID** (e.g. `CEAMGPSCTEAM0009R`) and/or **site ID** (e.g. `TDICAM53386PP80`).
  These are your grep key for everything downstream.
- **The reported state** exactly as given (`provisioning_failed`,
  `down/hardstop`, `configuring / expand vconfig`, …). The state tells you which
  layer and which state machine to walk.
- **The environment** (preprod / prod / sit). This matters because **AWS account
  IDs differ per environment** — see Reference A and verify rather than assume.
- **The time window** of interest, in **UTC**. Logs from different services use
  different local offsets; normalize everything to UTC immediately or the
  timeline will silently mislead you.

If the node is reachable, the fastest ground-truth for the on-host layer is the
provisioning-status endpoint:

```
curl -s http://localhost:22222/provisioning-status
# → {"engine":{"state":"configuring","stage":"expand vconfig","version":"20.00.31.58CE4-1"}}
```

### Step 2 — Inventory the files before loading them

Do **not** `cat` a 40 MB JSON or a 40k-line syslog into context. First, size
them and read only the boundaries:

```bash
wc -l <file>
head -5 <file>
echo "---"
tail -5 <file>
```

This tells you the format (JSON array vs syslog), the time span covered, and
whether the file is truncated. For the syslog, it also reveals the timestamp
format you'll need for `sed` windows. For the JSON, confirm it's an array of
`{"@timestamp", "@message"}` entries (Reference C) and note that bulk entries
like a `ListComputeEngineConfigs response` (`count: 214`, huge `items` array)
are noise to skip.

### Step 3 — Build a UTC timeline

Extract the events relevant to this CE into a single chronological, UTC timeline.
This is the spine of the whole investigation — every later claim hangs off "what
happened, in what order."

- **Control-plane (JSON):** filter `@message` entries to those mentioning the CE
  ID, and pull `level`, `msg`, `operation`, `status`, `connectivity`,
  `error_code`, plus the timestamp. Use `scripts/parse_cloudwatch.py --timeline`
  (it handles dict-vs-string `@message` and skips bulk entries).
- **On-host (syslog):** grep the anchors (Reference C), find the **transition
  line** where the state changed, then `sed -n '<start>,<end>p'` a window around
  it to see the surrounding sequence. Use `scripts/triage_syslog.sh`.

Each timeline entry should record not just *what* the log said but *what it
proves*. "06:43:23 — PDE Event 13912 (reconcile failed)" proves the reconfigure
aborted; "06:44:23 — DBS DOWN/HARDSTOP" proves the DB then went down.

### Step 4 — Map events to the state machine

Lay the timeline against the correct state machine (Reference B) and find the
**transition that did not fire**. The expected happy path is a known sequence;
the failure is wherever reality diverged. On the control plane, ask "which
service owns the transition that's missing?" — because that names the account
and service you'd escalate to. On the node, ask "which orchestration step
errored, and did the success/cleanup step ever run?"

### Step 5 — Match against known failure signatures

Compare the divergence point and its surrounding evidence against the
**failure-signature playbook (Reference D)**. Most real CE failures match one of
S1–S6. A signature gives you three things at once: the likely root cause, *where
to confirm it*, and the fix. Do not stop at "matches S2" — run the confirmation
step the signature names, because two signatures can present similarly (a stuck
`expand vconfig` is the *symptom* S5, but its *cause* is usually the PDE crash
S2).

### Step 6 — Separate root cause from secondary noise

A CE failure log is full of errors that **merely logged** but did not **cause**
anything. The discipline that makes a triage trustworthy is cleanly splitting
the load-bearing fault from the noise. Known benign-or-unrelated lines are
catalogued in Reference D ("Secondary noise"); the classic is treating a
`cloud-init: Unable to locate credentials` line or a ServiceNow `Org not found`
error as the cause when the real fault was a PrivateLink DNS timeout. State
explicitly: "X failed (root cause); Y and Z logged errors but were not on the
critical path."

### Step 7 — Assign ownership

Name *which account and service* owns the fault. This is the single most useful
output for the person who has to act, and it falls out of Steps 4–5 because each
state transition lives in a specific service/account. Order any next steps by
**authority of evidence**: CloudTrail / the authoritative log first, then
resolve the relevant role/ARN, then "ask the owning team" only when the logs
can't settle it.

### Step 8 — Hand off to `rca-writeup`

If the user wants a durable document, pass these findings to the `rca-writeup`
skill: one-line verdict, the UTC timeline, the root cause with its deciding
evidence, secondary issues kept separate, next steps ordered by authority, and a
caveats line for what the logs cannot prove. This skill's job ends at the
conclusion; `rca-writeup` formats it.

---

<a name="reference-a"></a>
## 3. Reference A — System architecture and how services communicate

### 3.1 The control-plane service map

```
                         ┌──────────────────┐
                         │   API Consumer    │
                         │  (Portal / CLI)   │
                         └────────┬──────────┘
                                  │ REST API call
                                  ▼
        API Gateway + Authorizer Lambda (validates token)
                                  │
                                  ▼
        ┌─────────────────────────────────────────────────┐
        │   Global Compute Service (GCS) — acct 700562448506│
        │   • lifecycle API Lambda                          │
        │   • writes cluster record → DynamoDB              │
        │       (cluster-provisioning-*)                    │
        │   • DynamoDB Stream → monitors:                   │
        │       Status Monitor   (detects state changes)    │
        │       Manifest Monitor (creates CE manifests)     │
        │       PrivateLink Monitor (pooled networking)     │
        └───────────────┬───────────────────────────────────┘
                        │ resolves config / deploys
       ┌────────────────┼─────────────────────────┐
       ▼                ▼                          ▼
 ┌────────────┐  ┌────────────────┐       ┌──────────────────┐
 │  Metadata  │  │  Network Svc   │       │   Scheduler       │
 │  Service   │  │ acct           │       │  acct 700562448506│
 │ acct       │  │ 656901843610   │       │                   │
 │ 649897665836  │ • Sites/Status │       │ • ECS task that   │
 │ • CE configs  │   DDB tables   │       │   runs the CE     │
 │ • Orgs     │  │ • PrivateLink  │       │                   │
 │ • Sites    │  │ • VPC flow logs│       │                   │
 └─────┬──────┘  └────────────────┘       └────────┬──────────┘
       │ config events                              │ deploys
       ▼                                            ▼
 ┌──────────────┐                          ┌──────────────────┐
 │ Auto-Suspend │  ◄──── idle events ───── │  Compute Engine   │
 │ SQS Processor│                          │ (customer OR      │
 │ acct          │                          │  pooled account) │
 │ 700562448506 │                          └──────────────────┘
 └──────────────┘
```

The **key insight**: each state transition happens in a *different* service,
often in a *different* AWS account. That is exactly why debugging is hard and
why "which transition is missing?" (Step 4) immediately points at an owner.

### 3.2 AWS account topology — VERIFY per environment

These are the account IDs observed in past investigations. **Account IDs shift
between preprod / prod / sit**, so treat this as a starting map and confirm
against the actual ARNs in the logs (the queue/topic ARNs carry the account ID).

| Service / role | Account (observed) | Notes |
|---|---|---|
| Global Compute Service (GCS), Scheduler, Auto-Suspend SQS processor | `700562448506` | Also the **publisher** account for the metering SNS topic |
| Metadata Service (CE configs, orgs, sites) | `649897665836` | Also the **consumer** account for the metering SQS queue |
| Global Network Service (VPC, PrivateLink, sites/status) | `656901843610` | The account to escalate network/PrivateLink faults to |
| Customer-allowed / pooled accounts | e.g. `210987654321`, `700562448506` | From `allowed_accounts` in `pooled_network_settings` |

### 3.3 Communication chain 1 — provisioning

```
API Consumer → API Gateway + Authorizer → GCS lifecycle Lambda
  → DynamoDB cluster-provisioning-* (write)
  → DynamoDB Stream → GCS monitors (Status / Manifest / PrivateLink)
  → Metadata Service (resolve CE config, org, site)
  → Network Service (provision VPC + PrivateLink; pooled → endpoint service)
  → Scheduler (create/update ECS task)
  → Compute Engine runs (customer or pooled account)
```

When provisioning stalls, walk this chain forward from the last successful step.

### 3.4 Communication chain 2 — metering / health (the "flap" path)

This chain is the source of the metering **health-flap** signature (S4), and its
log shape is the trickiest to read (the triple-nested envelope in Reference C).

```
CE / agent emits health
  → SNS topic  global-compute-engine-metering-service-preprod   (acct 700562448506)
  → SQS queue  metering-service-events-preprod                  (acct 649897665836)
  → consumer logs "Received SQS event"
        body (JSON string) → TopicArn + Message (double-escaped JSON string)
        Message → { ce_siteid, status }            # e.g. status: healthy | critical
        attributes.SenderId            = AROA…:AWS-CLOUDCAST   # publishing role
        attributes.ApproximateReceiveCount = N                 # redelivery counter
```

The crucial diagnostic fact: a flapping `healthy/critical` here is **publisher-
side** and does **not** mean the CE itself is down. Cross-check EC2 uptime over
the same window before acting.

### 3.5 Communication chain 3 — on-host DB bring-up (Teradata node layer)

```
Salt master → Salt minion(s) on the CE nodes
  orchestration:
    expand vconfig            → writes the Vconfig GDO ("NNNN bytes ... written to Vconfig GDO")
    run_tpareconfig           → sets tosstate RECONFIG, runs /usr/pde/bin/tpareconfig
    wait_normal_system_state  → polls tosstate / is_normal until NORMAL
  PDE  manages the DB process (TPA);  DBS state: Active ↔ DOWN/HARDSTOP | DOWN/TDMAINT
  BYNET is the inter-node interconnect (UDP ports 1033 / 1034)
  healthcheck.json (/var/opt/teradata/salt/healthcheck/healthcheck.json) records stage+state
  /provisioning-status (localhost:22222) surfaces engine state/stage to the control plane
```

On a healthy expansion, the orchestration's final step resets `healthcheck.json`
to `stage: completed, state: configured`. If PDE crashes mid-reconfigure, the
error path never reaches that final step, leaving a **stale** marker (S5).

---

<a name="reference-b"></a>
## 4. Reference B — The two state machines

### 4.1 Control-plane lifecycle (CloudWatch JSON layer)

```
[API_REQUEST_RECEIVED]
   → [AUTHORIZED]              (Authorizer Lambda validates token)
   → [CONFIG_RESOLVED]         (Metadata Service provides config/org/site)
   → [CLUSTER_RECORD_CREATED]  (GCS writes cluster-provisioning DynamoDB)
   → [NETWORK_PROVISIONING]    (Network Service sets up VPC/networking)
        └─ [PRIVATELINK_SETUP] (PrivateLink Monitor — pooled only)
   → [NETWORK_READY]
   → [MANIFEST_CREATING]       (Manifest Monitor creates CE manifests)
   → [MANIFEST_READY]
   → [SCHEDULING]              (Scheduler creates/updates ECS task)
   → [DEPLOYING]               (ECS task launching)
   → [RUNNING]                 (Status Monitor detects running)
        └─ [AUTO_SUSPEND_CONFIGURED] (SQS processor gets config from Metadata)
   → [IDLE_DETECTED]           (auto-suspend task on CE master node)
   → [TERMINATING] → [TERMINATED]
```

Find the last state reached and the first that didn't. A CE "stuck in
`NETWORK_PROVISIONING`" means the Network Service step never completed — look at
PrivateLink (S1). "Stuck in `MANIFEST_CREATING`" points at the Manifest Monitor.

### 4.2 On-host provisioning / DB lifecycle (syslog layer)

```
engine.state / engine.stage  (from /provisioning-status and healthcheck.json):
    configuring / "expand vconfig"  ──success──▶  configured / "completed"
                                    ──failure──▶  STALE marker (stays "configuring")

DBS state:
    Active  ──reconfigure failure──▶  DOWN/HARDSTOP   (does NOT self-heal)
            ──maintenance──────────▶  DOWN/TDMAINT
```

`DOWN/HARDSTOP` is terminal without intervention: the DB will not recover on its
own, so any "stuck stage" sitting on top of a HARDSTOP is a downstream symptom,
not the cause.

---

<a name="reference-c"></a>
## 5. Reference C — Log-format anatomy

### 5.1 CloudWatch Logs Insights JSON (`logs-insights-results__NN_.json`)

- A **JSON array** of entries: `{"@timestamp": "...Z", "@message": ...}`.
- `@message` is **sometimes a dict** (structured: `level`, `msg`, `ce_siteid`,
  `operation`, `status`, `connectivity`, `error_code`, …) **and sometimes a
  plain string** (e.g. a line mentioning the CE and `NOT_PROVISIONED`). Any
  parser must handle both shapes or it will crash or silently drop events.
- Files routinely run **~40 MB**. **`jq` is usually absent** on the box and the
  **network is off**, so fall back to Python's stdlib `json` every time — this
  is precisely what `scripts/parse_cloudwatch.py` is for.
- **Skip bulk entries.** A `ListComputeEngineConfigs response` with `count: 214`
  and a large `items` array is inventory noise, not a lifecycle event.

### 5.2 The triple-nested SQS → SNS → Message envelope

Metering entries are nested three deep, with two of the layers stored as escaped
JSON *strings* (not objects). Decoding requires two `json.loads` hops:

```
@message
└─ sqsEvent.Records[ ]
   ├─ eventSourceARN  = arn:aws:sqs:us-west-2:649897665836:metering-service-events-preprod
   ├─ attributes.SenderId            = AROA3TYMO3BGFNSFCVJZ6:AWS-CLOUDCAST
   ├─ attributes.ApproximateReceiveCount = "1"      # ← redelivery counter
   └─ body  (JSON *string*)  ── json.loads ─▶
        ├─ TopicArn = arn:aws:sns:us-west-2:700562448506:global-compute-engine-metering-service-preprod
        └─ Message  (double-escaped JSON *string*)  ── json.loads ─▶
             └─ { "ce_siteid": "...", "status": "healthy" | "critical" }
```

Two diagnostics fall straight out of this structure:
- **Flap detection** — collect `status` per `ce_siteid` over time; alternating
  `healthy`/`critical` while the EC2 stays up is signature **S4**.
- **Redelivery** — a high `ApproximateReceiveCount` (e.g. `4`) means SQS
  redelivered the message (consumer failures / visibility-timeout expiry), which
  can amplify an apparent flap. Note it; it's a clue, not usually the root cause.

### 5.3 Decoding `SenderId` / `AROA…` role IDs

`SenderId` looks like `AROA<role-unique-id>:<session-name>` (here
`AROA3TYMO3BGFNSFCVJZ6:AWS-CLOUDCAST`). The `AROA` prefix marks an **IAM role**
unique-id, and the session name (`AWS-CLOUDCAST`) identifies the publisher. Use
it to attribute *which role/service* put the message on the queue; cross-
reference the queue/topic ARNs for the owning **account IDs**.

### 5.4 The `messages` / Salt `minion` syslog

- **40k+ lines**; any viewer (and most editors) **truncate the middle**, so
  never trust a scroll-through. Work by `grep` + `sed -n '<a>,<b>p'` windows.
- Find the **transition line** first (the moment the state changed), then read a
  window around it. The earlier successful run of the same orchestration is the
  best control: diff its sequence against the failed one.

### 5.5 grep / sed anchors that pay off

```
HARDSTOP            DOWN/HARDSTOP        DOWN/TDMAINT
"PDE is not operational"                "Cannot open PDE device"
tosstate            tpareconfig          run_tpareconfig      is_normal
vconfig             "Vconfig GDO"        expand
"Event 13912"       "Event 13895"        "failed reconcile"
BYNET               "lost contact"       eth0-udp-1033        eth0-udp-1034
salt-master         salt-minion          healthcheck
tdinfo              vprocmanager         "node_num"           "retcode 61"
ce-autoscaler       scale-up
# secondary / noise:
"Unable to locate credentials"          "unrecognised disk label"      "Org not found"
```

---

<a name="reference-d"></a>
## 6. Reference D — Failure-signature playbook

Each signature lists **symptom → root cause → how to confirm → fix**. Match the
divergence point from Step 4 here, then *run the confirmation* before declaring
the cause.

### S1 — PrivateLink DNS verification timeout (control plane)

- **Symptom.** CE stuck in `NETWORK_PROVISIONING` / `provisioning_failed`. JSON
  shows `operation: PRIVATE_LINK_DEPLOY`, `status: WAITING_DNS`, then an `ERROR`
  `private dns verification timed out`, `error_code: AWS-AssignSlot-Error`,
  `connectivity: FAILED`.
- **Root cause.** The Network Service created the domain-verification **TXT
  record** in the CE's Route 53 hosted zone and polled (~10 min), but AWS never
  marked the endpoint-service private-DNS name **verified**, so slot assignment
  failed.
- **Confirm.** In the Route 53 hosted zone for the CE domain: is the TXT record
  present? Is the VPC **endpoint service** "Private DNS name" domain-verification
  status `verified`? Check the verification window/timeout in the network
  service code path (`ce_manager.go`).
- **Fix.** Re-trigger verification once TXT has propagated; confirm the endpoint
  service domain verification flips to verified; escalate to the Network Service
  owner (acct `656901843610`) if AWS-side verification keeps timing out.

### S2 — PDE crash mid-reconfig → DOWN/HARDSTOP (on-host)

- **Symptom / sequence** (timestamps from a real case):
  - Autoscaler scale-up under sustained CPU (~85–91%): `ce-autoscaler` triggers
    expansion.
  - `06:43:16` `state.sls expand vconfig` runs; `2408 bytes successfully written
    to Vconfig GDO`; healthcheck → `stage: "expand vconfig", state: "configuring"`.
  - `06:43:19` `tdops.run_tpareconfig` sets `tosstate RECONFIG`, runs
    `/usr/pde/bin/tpareconfig` (reports success in 0.1s).
  - `06:43:22` `wait_normal_system_state` poll #1 → `is_normal=False` (expected).
  - `06:43:23` **PDE Event 13912: "Node needed for online reconfiguration failed
    reconcile"** → **Event 13895: forced TPA restart**.
  - `06:43:27` `/usr/pde/bin/tosstate: PDE is not operational - Cannot open PDE
    device` — repeats **12× at 5s**.
  - `06:44:22` `run_tpareconfig` gives up: `tosstate persistently failing after
    12 attempts while waiting for NORMAL`.
  - `06:44:23` **DBS → DOWN/HARDSTOP**, never recovers. Retry cycles
    (`update_bynet_hosts` → `update_mpplist` → `tpa_snapshot`) all fail with
    `tdinfo: Error, can't find node_num 34, byn001-02, in vconfig GDO` and
    `vprocmanager: PDE is down or not completely up : Operation not permitted
    (retcode 61)`.
- **Root cause.** The new follower node could not reconcile with the leader —
  most likely the **BYNET was still processing the detachment of a previously
  decommissioned node at the same slot**, and the **vconfig GDO had not yet been
  updated** to include the new node. The reconcile failure (Event 13912) forced
  the TPA restart, which produced a hard stop the system never recovered from.
- **Confirm.** The leader's `messages` log around the reconfigure window; the
  vconfig GDO node list (the missing `node_num`); the prior decommission at that
  slot. A follower `minion` log typically shows the follower executed every step
  correctly — the fault is the leader's PDE, not the minion.
- **Fix.** Recover the **leader DB first** (DOWN/HARDSTOP will not self-heal),
  then re-run the expansion orchestration; verify the slot/`node_num` is clean
  before re-adding the node.

### S3 — Follower node unreachable / BYNET degraded (on-host)

- **Symptom.** Salt master completes the local node and **waits on the follower**
  (e.g. `001-02`); BYNET logs `DEGRADED: lost contact with all nodes on
  eth0-udp-1033` (and/or `-1034`). The cluster can't form.
- **Root cause.** The follower EC2 is unreachable on the BYNET UDP ports
  (1033/1034) — instance health, security-group, or network reachability.
- **Confirm.** Health of the follower EC2; SG rules permitting UDP 1033/1034
  between nodes; the follower's own `minion` log (often "I did my part, the
  leader never came back" — interplays with S2).
- **Fix.** Investigate/replace the follower EC2; open UDP 1033/1034 between
  nodes if an SG change closed them.

### S4 — Metering health flap (control plane; publisher-side)

- **Symptom.** The metering pipeline emits **alternating `healthy`/`critical`**
  for the same `ce_siteid` within minutes, while the EC2/CE stays up. Evidence is
  the SQS records from the metering SNS topic (Reference C, §5.2).
- **Root cause.** Publisher-side: the metering service (SNS topic in
  `700562448506`, consumed via SQS in `649897665836`) is producing flapping
  health. **This is not a CE fault.**
- **Confirm.** Decode the envelope, list `status` over time per `ce_siteid`,
  and check EC2 uptime across the same window. Note `ApproximateReceiveCount`
  (redelivery can amplify the flap).
- **Fix.** Route to the metering service owner. **Do not deprovision the CE** on
  the basis of the flap.

### S5 — Stale healthcheck marker / stuck `expand vconfig` (on-host)

- **Symptom.** Node reports `state: configuring, stage: "expand vconfig"`
  indefinitely (`curl localhost:22222/provisioning-status` confirms);
  `healthcheck.json` still set to `expand vconfig / configuring`.
- **Root cause.** An expansion failed catastrophically (usually **S2**). The
  orchestration's success path sets the healthcheck back to
  `completed / configured` (as it did on an earlier successful expansion at
  e.g. `05:56:03`), but because PDE crashed mid-reconfigure, the **error path
  never reached the completion step**. The healthcheck is a **stale marker**, not
  a live operation.
- **Confirm.** The `minion` log: did the expansion actually complete, or error
  before the completion line? Compare against a prior successful expansion's
  completion entry.
- **Fix.** Recover the DB (S2) and re-run orchestration; or, once the system is
  NORMAL, manually reset the healthcheck file. **Do not report the stuck stage as
  the root cause** — it is downstream of the crash.

### S6 — Config unmarshal failure / bad DynamoDB item (control plane)

- **Symptom.** GCS logs `Error unmarshaling site`, then `unmarshal failed,
  cannot unmarshal string set into Go value type map[string]string`, then
  `Error creating compute engine config: error retrieving site <SITE>: failed to
  unmarshal site`. The CE never begins provisioning.
- **Root cause.** The site's DynamoDB item stores
  `pooled_network_settings.allowed_accounts` / `allowed_cidrs` as **`SS`
  (String Set)** while the Go struct expects **`M` (Map → `map[string]string`)**.
  The read fails before any provisioning step runs.
- **Confirm.** `describe` the DynamoDB item for the site; check the attribute
  type of `allowed_accounts` / `allowed_cidrs` (`SS` vs `M`).
- **Fix.** Either correct the stored item (`SS` → `M`), or change the Go field's
  `dynamodbav` type to `[]string` to match an `SS`. This is a control-plane
  **data/schema** fault, not an on-host failure.

### Secondary noise — log errors that are usually NOT the root cause

Catalog these so they don't hijack a triage. Each *can* matter, but each is
frequently a red herring — verify before blaming:

- `cloud-init: Failed to fetch AWS instance tags via API: Unable to locate
  credentials` — usually benign IMDS/timing during early boot.
- `disk: Command 'parted' failed: unrecognised disk label` — often a benign
  first-boot disk-prep line.
- ServiceNow `Org not found` — an unrelated integration error. The textbook case
  of "logged an error but wasn't on the critical path" (the actual cause was the
  S1 PrivateLink timeout).

---

<a name="bundled-scripts"></a>
## 7. Bundled scripts

These exist because the box has **no `jq` and no network**, the JSON runs ~40 MB,
and the syslog is too long to read. They are pure-stdlib and offline. The agent
loads them only when a step needs them, so they cost nothing on a run that only
has a `messages` file.

- **`scripts/parse_cloudwatch.py`** — stdlib-only CloudWatch JSON parser.
  Handles dict-vs-string `@message`, skips bulk `List…` entries, and offers
  views: `--timeline` (per-CE chronological events), `--errors` (ERROR/`fail`
  lines), and `--metering` (decodes the triple-nested SQS→SNS→Message envelope,
  flags `healthy/critical` flaps and high `ApproximateReceiveCount`). Filter to
  one engine with `--ce <CE_ID>`.

  ```bash
  python3 scripts/parse_cloudwatch.py <file.json> --ce CEAMGPSCTEAM0009R --timeline
  python3 scripts/parse_cloudwatch.py <file.json> --metering
  ```

- **`scripts/triage_syslog.sh`** — one-command sweep of a `messages` / `minion`
  log: greps the Reference C anchors, surfaces the candidate transition lines,
  and prints the next command to `sed` a window around a chosen line number.
  Filter to a node with `--ip <node-ip>`.

  ```bash
  bash scripts/triage_syslog.sh <messages> --ip 10.0.2.201
  # then: bash scripts/triage_syslog.sh <messages> --window <lineno>
  ```

> These two scripts are described here so the workflow is complete; if they are
> not yet present in `scripts/`, generate them as a follow-up (both are short and
> stdlib/offline by design).

---

<a name="caveats"></a>
## 8. Caveats and honesty rules

- **Account IDs are environment-specific.** The topology in Reference A is a
  starting map; confirm against the ARNs in the actual logs.
- **Logs prove sequence, not always intent.** When the logs can't settle a
  question (e.g. *why* AWS never verified the DNS), say so and name what to check
  next (CloudTrail, the owning team) rather than guessing.
- **A stuck state is usually a symptom.** Especially `expand vconfig / configuring`
  on top of a `DOWN/HARDSTOP` — chase the crash, not the marker.
- **Flag load-bearing assumptions for empirical testing.** If a conclusion hinges
  on an edge case (a slot still detaching, a TXT still propagating), say it's
  load-bearing and verify it directly rather than trusting the spec.
- **Separate "what failed" from "what merely logged an error"** in every writeup.
  This is the habit that makes the analysis trustworthy to someone who wasn't in
  the logs.
