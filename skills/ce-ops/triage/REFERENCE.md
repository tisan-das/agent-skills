# REFERENCE — Architecture, state machines, log anatomy

Load this file when mapping a timeline to the state machines (workflow Step 4),
identifying the owning account/service (Step 7), or decoding an unfamiliar log
shape. Contents:

- [A. System architecture and service communication](#a-architecture)
- [B. The two state machines](#b-state-machines)
- [C. Log-format anatomy](#c-log-anatomy)

---

<a name="a-architecture"></a>
## A. System architecture and how services communicate

### A.1 Control-plane service map

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
        ┌───────────────────────────────────────────────────┐
        │   Global Compute Service (GCS) — acct GCS_NONPROD │
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
 ┌──────────────┐  ┌──────────────────┐   ┌──────────────────┐
 │  Metadata    │  │  Network Svc     │   │   Scheduler      │
 │  Service     │  │  acct GNS_DEPLOY │   │ acct GCS_NONPROD │
 │ acct MD_ACCT │  │                  │   │                  │
 │ • CE configs │  │ • Sites/Status   │   │ • ECS task that  │
 │ • Orgs       │  │   DDB tables     │   │   runs the CE    │
 │ • Sites      │  │ • PrivateLink    │   │                  │
 │              │  │ • VPC flow logs  │   │                  │
 └─────┬────────┘  └──────────────────┘   └────────┬─────────┘
       │ config events                              │ deploys
       ▼                                            ▼
 ┌──────────────┐                          ┌──────────────────┐
 │ Auto-Suspend │  ◄──── idle events ───── │ Compute Engine   │
 │ SQS Processor│                          │ (customer OR     │
 │ acct         │                          │  pooled account) │
 │ GCS_NONPROD  │                          └──────────────────┘
 └──────────────┘
```

The boxes above use short account tokens so the fixed-width layout stays
readable: `GCS_NONPROD` = `<GCS_NONPROD_ACCT>`, `MD_ACCT` =
`<METADATA_NONPROD_ACCT>`, `GNS_DEPLOY` = `<GNS_DEPLOY_ACCT>`. The full
placeholders and their meaning are in the A.2 table below and KB §8.1.

**Key insight:** each state transition happens in a *different* service, often
in a *different* AWS account. That is why debugging is hard, and why "which
transition is missing?" immediately points at an owner.

The **pooling service** manages the pooled-account instance inventory; its
`/infrastructure` endpoint is queried during instance-registration flows and
is the upstream dependency in signature **S7**.

### A.2 AWS account topology — VERIFY per environment

Account IDs shift between preprod / prod / sit. Treat this as a starting map
and confirm against the ARNs in the actual logs (queue/topic ARNs carry the
account ID).

`<UPPERCASE>` placeholders stand for real values that were scrubbed from this
corpus; each maps 1:1 to one real account, so the relationships hold. Substitute
from your environment. **The method below does not change between prod and
non-prod** — only the account and domain you substitute do. See KB §8.1 for the
full convention.

| Service / role | Account (observed) | Notes |
|---|---|---|
| GCS, Scheduler, Auto-Suspend SQS processor | `<GCS_NONPROD_ACCT>` | Also the **publisher** account for the metering SNS topic |
| Metadata Service (CE configs, orgs, sites) | `<METADATA_NONPROD_ACCT>` | Also the **consumer** account for the metering SQS queue |
| Global Network Service (GNS) — VPC, PrivateLink, sites/status | `<GNS_DEPLOY_ACCT>` | **Escalation target** for network/PrivateLink faults |
| `privatelink-monitor` intermediate AssumeRole hop | `<PRIVATELINK_MONITOR_ACCT>` | Shows up in PrivateLink traces; **not** the GNS owner — do not escalate here |
| Customer-allowed / pooled accounts | e.g. `210987654321`, `<GCS_NONPROD_ACCT>` | From `allowed_accounts` in `pooled_network_settings` |

> **Corrected attribution.** Earlier revisions of this card listed
> `<PRIVATELINK_MONITOR_ACCT>` as the Network Service owner, and the box diagram
> above carried the same error. It is the privatelink-monitor's intermediate
> AssumeRole hop; GNS itself is `<GNS_DEPLOY_ACCT>` (KB §8.1; Provisioning §16 #14;
> see also the precedence note in `TRIAGE.md`). Escalating to the wrong account is
> the practical cost of the old wording, so trust this row over any cached memory
> of it.

### A.3 Communication chain 1 — provisioning

```
API Consumer → API Gateway + Authorizer → GCS lifecycle Lambda
  → DynamoDB cluster-provisioning-* (write)
  → DynamoDB Stream → GCS monitors (Status / Manifest / PrivateLink)
  → Metadata Service (resolve CE config, org, site)
  → Network Service (provision VPC + PrivateLink; pooled → endpoint service)
  → Scheduler (create/update ECS task)
  → Compute Engine runs (customer or pooled account)
```

When provisioning stalls, walk this chain forward from the last successful
step.

### A.4 Communication chain 2 — metering / health (the "flap" path)

Source of signature **S4**; its log shape is the trickiest to read (the
triple-nested envelope in §C.2).

```
CE / agent emits health
  → SNS topic  global-compute-engine-metering-service-preprod   (acct <GCS_NONPROD_ACCT>)
  → SQS queue  metering-service-events-preprod                  (acct <METADATA_NONPROD_ACCT>)
  → consumer logs "Received SQS event"
        body (JSON string) → TopicArn + Message (double-escaped JSON string)
        Message → { ce_siteid, status }            # e.g. status: healthy | critical
        attributes.SenderId            = AROA…:AWS-CLOUDCAST   # publishing role
        attributes.ApproximateReceiveCount = N                 # redelivery counter
```

Crucial diagnostic fact: a flapping `healthy/critical` here is **publisher-
side** and does **not** mean the CE itself is down. Cross-check EC2 uptime
over the same window before acting.

### A.5 Communication chain 3 — on-host DB bring-up (Teradata node layer)

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

On a healthy expansion, the orchestration's final step resets
`healthcheck.json` to `stage: completed, state: configured`. If PDE crashes
mid-reconfigure, the error path never reaches that final step, leaving a
**stale** marker (signature S5).

### A.6 Communication chain 4 — pooled instance registration (the S7 path)

```
Registration flow (control plane)
  → GET  pooling-service /infrastructure          # discover pooled instances
  → build instances payload from the response
  → PUT  /compute-engines/<CE_ID>/instances       # register with downstream
       downstream validates: instances list must not be empty
       empty list → HTTP 400 INVALID_ARGUMENT
```

The failure mode: the GET times out, the caller treats the timeout as an
empty-but-successful result, and the subsequent PUT carries `instances: []` —
which the downstream correctly rejects. The 400 is therefore a *downstream
symptom*; the *cause* is the upstream timeout. See signature **S7**.

---

<a name="b-state-machines"></a>
## B. The two state machines

### B.1 Control-plane lifecycle (CloudWatch JSON layer)

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

Find the last state reached and the first that didn't. Stuck in
`NETWORK_PROVISIONING` → the Network Service step never completed; look at
PrivateLink (S1). Stuck in `MANIFEST_CREATING` → the Manifest Monitor.

### B.2 On-host provisioning / DB lifecycle (syslog layer)

```
engine.state / engine.stage  (from /provisioning-status and healthcheck.json):
    configuring / "expand vconfig"  ──success──▶  configured / "completed"
                                    ──failure──▶  STALE marker (stays "configuring")

DBS state:
    Active  ──reconfigure failure──▶  DOWN/HARDSTOP   (does NOT self-heal)
            ──maintenance──────────▶  DOWN/TDMAINT
```

`DOWN/HARDSTOP` is terminal without intervention: any "stuck stage" sitting on
top of a HARDSTOP is a downstream symptom, not the cause.

---

<a name="c-log-anatomy"></a>
## C. Log-format anatomy

### C.1 CloudWatch Logs Insights JSON (`logs-insights-results__NN_.json`)

- A **JSON array** of entries: `{"@timestamp": "...Z", "@message": ...}`.
  Some exports arrive as `{"results": [...]}` wrappers or as arrays of
  `{"field": ..., "value": ...}` pairs — `parse_cloudwatch.py` normalizes
  all three shapes.
- `@message` is **sometimes a dict** (structured: `level`, `msg`, `ce_siteid`,
  `operation`, `status`, `connectivity`, `error_code`, …) **and sometimes a
  plain string**. Any parser must handle both or it will crash or silently
  drop events — this is exactly what the parser's `text_of()` is for.
- Files routinely run **~40 MB**; `jq` is usually absent and the network is
  off — always use the bundled stdlib parser.
- Exports are **reverse-chronological**; the parser re-sorts and tells you it
  did (stderr).
- **Bulk entries must be skipped**: a `ListComputeEngineConfigs response`
  (`count: 214`, huge `items` array) mentions every CE in the fleet and will
  false-positive any ID filter that runs before the skip.

### C.2 The triple-nested SQS → SNS → Message envelope

Metering entries are nested three deep, with two layers stored as escaped JSON
*strings*. Decoding requires two `json.loads` hops (automated by
`parse_cloudwatch.py --metering`):

```
@message
└─ sqsEvent.Records[ ]
   ├─ eventSourceARN  = arn:aws:sqs:us-west-2:<METADATA_NONPROD_ACCT>:metering-service-events-preprod
   ├─ attributes.SenderId            = AROAEXAMPLEPRINCIPAL1:AWS-CLOUDCAST
   ├─ attributes.ApproximateReceiveCount = "1"      # ← redelivery counter
   └─ body  (JSON *string*)  ── json.loads ─▶
        ├─ TopicArn = arn:aws:sns:us-west-2:<GCS_NONPROD_ACCT>:global-compute-engine-metering-service-preprod
        └─ Message  (double-escaped JSON *string*)  ── json.loads ─▶
             └─ { "ce_siteid": "...", "status": "healthy" | "critical" }
```

Two diagnostics fall out of the structure: **flap detection** (alternating
`healthy`/`critical` per `ce_siteid` while the EC2 stays up → S4) and
**redelivery** (`ApproximateReceiveCount` > 1 → consumer failures /
visibility-timeout expiry, which can amplify an apparent flap — a clue, not
usually the root cause).

### C.3 Decoding `SenderId` / `AROA…` role IDs

`SenderId` looks like `AROA<role-unique-id>:<session-name>` (e.g.
`AROAEXAMPLEPRINCIPAL1:AWS-CLOUDCAST`). The `AROA` prefix marks an **IAM
role** unique-id; the session name identifies the publisher. Use it to
attribute which role/service put the message on the queue, and cross-reference
the queue/topic ARNs for the owning account IDs.

### C.4 The `messages` / Salt `minion` syslog

- **40k+ lines**; every viewer truncates the middle, so never trust a
  scroll-through. Work by `grep` + `sed -n '<a>,<b>p'` windows — automated by
  `triage_syslog.sh` (sweep mode → `--window` mode).
- Find the **transition line** first (the moment the state changed), then read
  a window around it. The earlier successful run of the same orchestration is
  the best control: diff its sequence against the failed one.

### C.5 grep / sed anchors that pay off

These are the patterns `triage_syslog.sh` sweeps, grouped:

```
DB_STATE : HARDSTOP  DOWN/HARDSTOP  DOWN/TDMAINT
           "PDE is not operational"  "Cannot open PDE device"
RECONFIG : tosstate  tpareconfig  run_tpareconfig  is_normal
           "Event 13912"  "Event 13895"  "failed reconcile"
VCONFIG  : vconfig  "Vconfig GDO"  expand  "node_num"  "retcode 61"
           tdinfo  vprocmanager
BYNET    : BYNET  "lost contact"  eth0-udp-1033  eth0-udp-1034
SALT     : salt-master  salt-minion  healthcheck
AUTOSCALE: ce-autoscaler  scale-up
NOISE    : "Unable to locate credentials"  "unrecognised disk label"
           "Org not found"          # usually NOT the root cause — see
                                    # TROUBLESHOOTING.md "Secondary noise"
```

### C.6 Control-plane anchors for `--grep` (parser)

```
PRIVATE_LINK_DEPLOY|WAITING_DNS|AssignSlot          # S1
unmarshal|String Set|dynamodbav                     # S6
/infrastructure|INVALID_ARGUMENT|must not be empty  # S7
ApproximateReceiveCount|TopicArn                    # S4 (or use --metering)
```
