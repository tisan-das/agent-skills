# TROUBLESHOOTING — Failure-signature playbook (S1–S7) and noise catalog

> **Precedence.** Always grep `references/CE-SIGNATURE-REGISTRY.md` (60+
> canonical rows) in the same pass as this index — the S↔# cross-map is in
> `triage/TRIAGE.md`. Where this file's facts conflict with the corpus, the
> corpus wins.

Load this file at workflow Step 5 (signature matching), when you suspect a red
herring, or when the bundled scripts misbehave. Each signature lists
**symptom → root cause → how to confirm → fix**. Match the divergence point
from Step 4 here, then **run the confirmation** before declaring the cause —
two signatures can present identically.

Quick index:

| # | Layer | One-line symptom |
|---|---|---|
| S1 | Control plane | Stuck `NETWORK_PROVISIONING`; `private dns verification timed out` |
| S2 | On-host | PDE crash mid-reconfig → `DOWN/HARDSTOP` |
| S3 | On-host | Follower unreachable; BYNET `lost contact` on UDP 1033/1034 |
| S4 | Control plane | Metering `healthy`/`critical` flap while EC2 stays up |
| S5 | On-host | Stuck `configuring / expand vconfig` — stale healthcheck marker |
| S6 | Control plane | GCS `unmarshal failed` — bad DynamoDB item (`SS` vs `M`) |
| S7 | Control plane | `/infrastructure` timeout → empty-instances PUT → `INVALID_ARGUMENT` 400 |

---

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
  owner (GNS deploy acct `<GNS_DEPLOY_ACCT>` per KB §8.1 — `<PRIVATELINK_MONITOR_ACCT>` is the
  privatelink-monitor's intermediate AssumeRole hop, Provisioning §16 #14, not
  the owner) if AWS-side verification keeps timing out.

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
  the SQS records from the metering SNS topic (REFERENCE.md §C.2). Detect with
  `parse_cloudwatch.py --metering` — it prints the per-site status sequence,
  marks transitions, and flags redelivery.
- **Root cause.** Publisher-side: the metering service (SNS topic in
  `<GCS_NONPROD_ACCT>`, consumed via SQS in `<METADATA_NONPROD_ACCT>`) is producing flapping
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

### S7 — Pooling-service `/infrastructure` timeout → empty-instances PUT → `INVALID_ARGUMENT` 400 (control plane)

> Recently observed; based on fewer occurrences than S1–S6, so calibrate
> confidence accordingly and confirm every element before concluding.

- **Symptom.** Instance registration fails with a downstream **`PUT …/instances
  → HTTP 400 INVALID_ARGUMENT`** (message like `instances list must not be
  empty`). Seconds earlier in the same flow, a **`GET` to the pooling service's
  `/infrastructure` endpoint timed out** (e.g. `DEADLINE_EXCEEDED`, `timed out
  after 10s`). The intermediate registration log line shows an empty payload
  (`count: 0` or `instances: []`).
- **Root cause.** The caller treated the `/infrastructure` **timeout as an
  empty-but-successful result** instead of an error, then proceeded to PUT an
  empty instances list, which the downstream correctly rejected. The 400 is the
  *messenger*; the fault is the upstream timeout plus the missing
  timeout-as-error handling. This does **not** match the S1–S6 divergence
  points — the provisioning chain itself may be healthy.
- **Confirm.** Run
  `parse_cloudwatch.py <file> --ce <CE> --site <SITE> --grep '/infrastructure|INVALID_ARGUMENT|must not be empty'`
  and verify the **three-beat sequence in strict order** on the chronological
  timeline: (1) infrastructure GET timeout → (2) registration attempt with
  empty/zero-count payload → (3) 400 INVALID_ARGUMENT. The grep surfaces beats
  (1) and (3); confirm beat (2) on the `--timeline` view, where the
  registration record renders with a `[count=0]` extra (or inspect it fully
  with `--show N`). Then check
  pooling-service health/latency in that window — was the timeout a blip or was
  the service degraded? If beat (1) is absent, this is NOT S7 (an empty PUT
  without a preceding timeout points at the payload-construction logic instead).
- **Fix.** *Operationally:* retry the registration once the pooling service is
  healthy; the flow typically succeeds unchanged. *Durably:* the calling code
  should treat an `/infrastructure` timeout as a hard error (retry/backoff, then
  fail the flow) and should refuse to PUT an empty instances list —
  validate-before-send. Route the code fix to the owner of the registration
  flow; route persistent `/infrastructure` latency to the pooling-service owner.
- **Ownership split.** Two candidate owners — the registration caller (missing
  error handling) and the pooling service (latency/timeout). Name both in the
  writeup, with the caller's error handling as the durable fix regardless of
  who caused this instance.

---

## Secondary noise — errors that are usually NOT the root cause

Catalog these so they don't hijack a triage. Each *can* matter, but each is
frequently a red herring — verify before blaming:

- `cloud-init: Failed to fetch AWS instance tags via API: Unable to locate
  credentials` — usually benign IMDS/timing during early boot.
- `disk: Command 'parted' failed: unrecognised disk label` — often a benign
  first-boot disk-prep line.
- ServiceNow `Org not found` — an unrelated integration error. The textbook case
  of "logged an error but wasn't on the critical path" (the actual cause was the
  S1 PrivateLink timeout).
- A stuck `expand vconfig / configuring` stage — usually the stale marker (S5)
  downstream of a crash (S2), not the fault itself.
- An `INVALID_ARGUMENT` 400 on an instances PUT — usually the downstream echo
  of an upstream timeout (S7), not a payload-construction bug; confirm which.

In every writeup, state explicitly: "X failed (root cause); Y and Z logged
errors but were not on the critical path."

---

## Script troubleshooting

Read this before patching the bundled scripts — most "parser bugs" are one of
these.

| Observation | Likely cause | Action |
|---|---|---|
| `--ce` keeps 0 records but the ID is right | Records carry only the **site** ID | Add `--site <SITE>`; the dual-filter matches EITHER |
| Suspiciously few records kept | Legit records skipped as bulk (rare) | Re-run with `--no-skip` and diff kept-counts (stderr) |
| Way too many records kept | Bulk skip disabled, or ID is a substring of another ID | Remove `--no-skip`; check for ID-prefix collisions |
| `unrecognized top-level object` | New export wrapper shape | `head -c 400 file.json` to inspect; extend `load_records()` only after confirming |
| Timestamps `??[…]` sorted to top | Unparseable/absent `@timestamp` | Inspect one with `--show 0`; add the format to `TS_FORMATS` if it's a real new format |
| `--metering` finds nothing | Export doesn't include the consumer log group | Confirm the export's source log group; metering lives in the SQS consumer's logs |
| Sweep shows 0 matches everywhere | Wrong file (not a syslog), or `--ip` too strict | `head -5` the file; drop `--ip` and re-sweep |
| Timeline line ends in `…` | Width cap (default 240) | `--width 0` for full lines, or `--show N` for the whole record |

Two invariants to preserve if you ever do modify the parser: bulk-skip must run
**before** ID filtering, and every text access must go through `text_of()`.
