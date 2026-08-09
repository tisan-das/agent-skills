# Compute Engine (CE) — Lifecycle, Notification & Autoscale Analysis

> **Scope.** Every repo under `~/workspace/compute-engine`. This document catalogues **every
> notification, event, callback and status write** that participates in the CE lifecycle, the
> **six independent state models** they carry, the end-to-end **START / RUNNING / STOP / STOPPED**
> flows, the **autoscaling** design, and a ranked **defect register** ending in the root cause of
> the "CE status goes degraded/STOPPED during autoscaling" issue.
>
> Every claim carries a `file:line` citation. Where behaviour lives outside this workspace
> (`avcd-healthcheck-service`, `ce-autoscaler`, SCOrch, OMS, CIDS, Boost, Ping, STC, Consumption)
> it is explicitly marked **[external]**.

---

## Table of contents

- [Part 0 — Actors](#part-0--actors)
- [Part 1 — The complete notification catalogue](#part-1--the-complete-notification-catalogue)
  - [1.1 Synchronous HTTP — control plane](#11-synchronous-http--control-plane)
  - [1.2 Asynchronous — SNS / SQS / pgmq / DynamoDB Streams](#12-asynchronous--sns--sqs--pgmq--dynamodb-streams)
  - [1.3 On-engine local HTTP](#13-on-engine-local-http)
  - [1.4 Salt event bus (inside the engine)](#14-salt-event-bus-inside-the-engine)
  - [1.5 Notification payloads, verbatim](#15-notification-payloads-verbatim)
- [Part 2 — The six state models](#part-2--the-six-state-models)
- [Part 3 — Lifecycle walkthroughs](#part-3--lifecycle-walkthroughs)
- [Part 4 — Autoscaling, end to end](#part-4--autoscaling-end-to-end)
- [Part 5 — Root cause: the autoscale "degraded" bug](#part-5--root-cause-the-autoscale-degraded-bug)
- [Part 6 — Defect register](#part-6--defect-register)
- [Part 7 — Recommendations](#part-7--recommendations)
- [Appendix A — Who writes which field](#appendix-a--who-writes-which-field)
- [Appendix B — Vocabulary mismatch matrix](#appendix-b--vocabulary-mismatch-matrix)

---

## Part 0 — Actors

| # | Repo | Runtime / deployment | Lifecycle role | Writes CE status? |
|---|---|---|---|---|
| 1 | `cog-global-compute` (**GCS**) | Go; Lambdas `api`, `authorizer`, `status-monitor`, `privatelink-monitor`, `whitelist-ip` + ECS Fargate `scheduler` | Control plane. Routes create/delete, reconciles status, drives OMS/QG/VP/PrivateLink, runs schedules & auto-suspend | **Yes** — own DynamoDB `status` + metadata `state` |
| 2 | `accp-metadata-service` | Go; API-GW Lambda + `event-processor`, `config-change-processor`, `deletion-poller`, `org-sync-processor` Lambdas | Canonical config store. **Also hosts the metering event processor** | **Yes** — `state`, `compute.auto_scale.*`, infra snapshot |
| 3 | `cog-compute-engine-pooling-service` | Python Litestar + ALB on ECS Fargate; PostgreSQL + `pgmq` | Pooled-cluster manager; leader claim, follower ASG, OMS trigger, DNS/PrivateLink | **Yes** — own PG `cluster.state`; notifies GCS; PATCHes metadata |
| 4 | `cog-compute-metering` | Python systemd agent **on the engine leader** | Emits CE state + autoscale events to SNS; `/scale` sink for `ce-autoscaler` | **Yes, indirectly** — the telemetry writer of `RUNNING`/`STOPPED` |
| 5 | `svc-vce-lmo` (**LMO**) | Python Litestar + Celery on ECS Fargate; SQS-FIFO broker | Async flow engine: OMS, QueryGrid, secrets, Viewpoint, STC, manifest sync, health checks | Indirect (returns run outputs; signed webhooks) |
| 6 | `accp-network-service` | Go; ECS Fargate `:8080` | Per-site VPC/NAT/SG/NLB/endpoint-service; per-CE PrivateLink + target registration | PATCHes metadata `compute.connectivity` |
| 7 | `mcld-mariners_vce-dns-service` | Python; Chalice proxy → FastAPI worker Lambda + Route53 | CE endpoint DNS records | No |
| 8 | `avcd-vce-engine-provision` | Bash container + CFN/Bicep/Terraform, launched by SCOrch | Actually provisions/destroys one **dedicated** CE | No (drives SCOrch component status) |
| 9 | `avcd-vce-engine-configure` | SaltStack → RPMs | Engine bootstrap + **the autoscale expand/contract orchestrations** and `cluster:state` grain | Engine-local only |
| 10 | `avcd-vce-engine-packer` / `avcd-vce-image-bom` | Packer HCL / YAML + Odin | Bakes the image; ships `avcd-healthcheck-service` and `ce-autoscaler` RPMs | No |
| 11 | `workspaces-event-scheduler` (**WES**) | Go; on the engine, gRPC `:50051` + REST `:50061` | Idle detection → auto-suspend POST; telemetry/crashdump | No (triggers stop) |
| 12 | `ce-agent` | Go; LLM agent | **Not read-only** — exposes `start_cluster` / `stop_cluster` tools that call GCS | Indirect (initiates start/stop) |
| 13 | `ce-assistant` | Go + Bedrock; ECS Fargate | Cost/sizing advisory; maps CE state → billability | No |
| 14 | `cog-ce-diagnostic-agent` | Python; Bedrock | Diagnostic runbook agent | No |

**[external]** SCOrch, OMS (`/oms/ce-admin/v1`), QueryGrid, CIDS (RBAC), Ping Identity, Boost OAuth2,
SCIM, Valtix, ServiceNow, CSM, Viewpoint, STC, Consumption/billing service,
`avcd-healthcheck-service` (`:22222`), `ce-autoscaler` (leader Go service).

---

## Part 1 — The complete notification catalogue

### 1.1 Synchronous HTTP — control plane

Legend: **Trig** = what causes it. **Ord** = ordering guarantee. **Idem** = idempotency handling.

| # | Producer → Consumer | Call | Trigger | Auth | Retry / Ord / Idem | Citation |
|---|---|---|---|---|---|---|
| H1 | User / `ce-agent` / GCS-scheduler / SQS-processor → **GCS** | `POST /clusters` | start CE | Ping JWT + CIDS `compute_engine:start_stop` | none / n/a / CAS on `FAILED_PROVISIONING` | `internal/api/router.go:35`, `clusterService.go:581-600` |
| H2 | same → **GCS** | `DELETE /clusters/{id}` | stop CE | as above | none | `router.go:40`, `clusterService.go:1536` |
| H3 | **Pooling → GCS** | `POST /clusters/status` | every pooled state change + OMS outcome | pooling SA token (API-GW) | `stamina` exponential retry on 5xx; **no ordering**; sends `Idempotency-Key` header that **GCS never reads** | `pool_manager/clients/gcs.py:60-95`; `poolingService.go:156`; no `Idempotency` in GCS |
| H4 | **Pooling → GCS** | `POST /privatelink` | DNS name created / PL endpoint service created | pooling SA token | best-effort, exception swallowed | `cluster_events.py:271-301`, `gcs.py:41-57` |
| H5 | **GCS → Metadata** | `PATCH /v1/compute-engine-configs/{id}` body `{"state": …}` (+ `{"cluster":{"cluster_id","key_name"}}` when `PROVISIONING`) | every GCS status change **except** `RUNNING` | `GetScorchMetadataToken` (Ping client-credentials) | no retry / no ordering / no version guard | `metadataService.go:786-880` |
| H6 | **GCS → Metadata** | `PUT /v1/compute-engine-configs/{id}/infrastructure` body `{"status":"RUNNING","event_time":…,"infrastructure":{…}}` | **the only** way `RUNNING` reaches metadata | same | none; a failure flips CE to `FAILED_PROVISIONING` | `clusterService.go:2706-2758`, `metadataService.go:1771-1820` |
| H7 | **GCS → Metadata** | `PATCH …/{id}` body `{"applications":{"query_grid":{"status","error"}}}` | QG state change | same | none | `metadataService.go:920-1000` |
| H8 | **GCS → Metadata** | `PATCH …/{id}` body `{"applications":{"viewpoint":{"status","error"}}}` | VP state change; **gated on `compute.add_system_to_viewpoint == true`** | same | none | `metadataService.go:1029-1135` |
| H9 | **GCS → Metadata** | `PATCH …/{id}` PrivateLink connectivity | PL monitor result | same | none | `metadataService.go:1170-1250` |
| H10 | **GCS → SCOrch** | `POST/GET/DELETE https://{site}.gateway[.{env}].cloud.<CORP_DOMAIN>/scorch/v2/components[/{id}]` | dedicated CE create/poll/delete | SCOrch SA token | none | `clusterService.go:133-160, 861-875` |
| H11 | **GCS → Pooling** | `POST /v1/clusters`, `PUT /v1/clusters/{uuid}/start`, `PUT …/stop`, `DELETE …`, `GET …`, `GET …/infrastructure` | pooled CE lifecycle | pooling SA token | none | `clients/pooling_service_client.go:32,329-540` |
| H12 | **GCS → OMS** | `POST /oms/ce-admin/v1/compute-engines`; `GET …/compute-engine-jobs/{id}`; `DELETE …/compute-engines/{id}` | dedicated CE OMS register/poll/deregister | JWT | none | `omsService.go:17-230` |
| H13 | **GCS → LMO** | `POST /lmo/v1/flows/{copy-secret-aws, delete-secret-aws, oms-ce-registration, oms-ce-deregistration, ce-postprovisioning, ce-provisioning-health-check}`; `GET /lmo/v1/runs/{id}` | post-provisioning, secrets, OMS | Ping token | polled by `status-monitor` cron | `internal/util/constants.go:49-62`, `orchestrationService.go` |
| H14 | **Pooling → LMO** | `POST /lmo/v1/flows/oms-ce-registration` / `oms-ce-deregistration`; `GET /lmo/v1/runs/{id}` | pooled OMS lifecycle | Ping | `stamina` on 5xx; poll loop with heartbeat | `pool_manager/clients/lmo.py:15-130` |
| H15 | **Pooling → Metadata** | `PATCH /compute-engine-configs/{id}` body `{"compute":{"dns_name","connectivity":{…}},"cluster":{"cluster_id"}}` | DNS created / PL set up / connectivity ready / connectivity failure | Ping SA | `httpx_retries` total=3 incl. PATCH | `pool_manager/clients/metadata.py:141-155`, `cluster_events.py` |
| H16 | **Pooling → DNS service** | `GET/POST/PATCH/DELETE /dns/records` | cluster DNS create/update/delete | Ping | retry transport | `pool_manager/clients/dns.py:90-180` |
| H17 | **Network svc → Metadata** | `PATCH /compute-engine-configs/{ceSiteId}` `{compute.connectivity.private_link…}` | PrivateLink assign result | Ping | `sendWithRetry` | `src/internal/clients/metadata_client.go:58-102` |
| H18 | **Network svc → Metadata** | `POST /sites/networks` (deploy & teardown callbacks); `DELETE /organizations/{id}?notify=true` | site network deploy/teardown complete | Ping | `sendWithRetry` | `metadata_client.go:105-206` |
| H19 | **Network svc → Metadata** | `GET /compute-engine-configs/{ceSiteId}` | forces a CE status refresh | Ping | — | `metadata_client.go:212+` |
| H20 | **Metadata → Network svc** | `POST/PATCH/DELETE /networks/{site}/compute-engines/{ce}/private-link`, `POST /networks`, `DELETE /networks`, `GET /networks/status/{req}`, `POST /quotas` | org/site onboarding, CE PL | Ping | — | `clients/network_service_client.go:157-700` |
| H21 | **Metadata → LMO** | `POST` flow trigger + `GET /lmo/v1/runs/{id}`; supplies `X-LMO-Callback-URL` = `{metadata}/v1/site-settings/{siteID}/setup-callback` | site setup / OMS deprovision | Ping | LMO webhook retries | `clients/global_orchestrator_client.go:91-250` |
| H22 | **LMO → Metadata** | `POST /v1/site-settings/{id}/setup-callback` with `X-LMO-Signature: sha256=…` HMAC | LMO run reaches terminal state | HMAC-SHA256 over raw body, secret from Secrets Manager (5-min cache) | `max_attempts` (default 3) + retryable status codes; `X-LMO-Idempotency-Key` sent | `callbacks/delivery.py:36-84`, `restapi/sites_api.go:437-470`, `restapi/lmo_signature.go` |
| H23 | **LMO → OMS** | `POST /oms/ce-admin/v1/compute-engines`, `GET …/compute-engine-jobs/{id}`, `DELETE …/compute-engines/{id}` via Site Gateway | pooled OMS registration | Site-gateway token | `WorkflowRetryFixed` | `services/oms_ce_admin/client.go:36-170` |
| H24 | **Metadata → SCIM** | `POST` / `DELETE` / `PUT` `{site}/tdscim/v1/targets` | on config `state` → `RUNNING` (register) or `NOT_PROVISIONED` (deregister), **only via the manager path** | SCIM token per site | — | `clients/scim_client.go:361-560`, `managers/computeengineconfig/update.go:1110-1130` |
| H25 | **Engine provision container → Network svc** | `PATCH/DELETE {gns}/{SITE_ID}/compute-engines/{name}/targets`; `GET {gns}/status/{req}` | dedicated CE OMS-NLB target register/deregister | JWT | polls status | `src/network/config.sh:49-115` |
| H26 | **Engine provision container → OMS** | `POST /oms/ce-admin/v1/compute-engines` … polls until `SUCCEEDED` | dedicated CE registration inside the container | generated JWT | poll loop | `src/oms/config.sh:38-130` |
| H27 | **Engine provision container → WES (on engine)** | `POST http://{leader}:50061/scheduler-vce-telemetry-start` `{engine_id, site_id}`; `POST …/scheduler-telemetry-stop` | CREATE / DELETE | none (in-VPC) | `run_and_check_with_save_err` | `src/telemetry/config.sh:10-45` |
| H28 | **WES (on engine) → GCS** | `POST {global_compute_endpoint}` body = suspend event | idle detected | OAuth2 bearer from Secrets Manager secret `auto-suspend-configs-{env}` | one auth-refresh retry; **`Content-Type: application/x-www-form-urlencoded` with a JSON body** | `service/task/inactive-ce/inactive_ce.go:542-660` |
| H29 | **`ce-agent` → GCS** | `POST /clusters`, `DELETE /clusters/{id}`, `POST /configs` … | LLM tool invocation | user token | wait-for-state polling | `internal/tools/gcs_cluster.go:70-160` |

### 1.2 Asynchronous — SNS / SQS / pgmq / DynamoDB Streams

| # | Producer → Consumer | Transport | Trigger | Ordering / dedup | Failure handling | Citation |
|---|---|---|---|---|---|---|
| A1 | **Metering agent → SNS** `global-compute-engine-metering-service-{env}` | SNS | every **300 s** (`periodic_loop`), **plus** one per `/scale` POST, **plus** one on SIGTERM | none | log-and-continue | `agent.py:1425-1429, 1347-1391, 1183-1196`, `config.yaml` |
| A2 | **SNS → Metadata `event-processor`** | **standard SQS** `{MeteringEventQueueName}-{env}`, `BatchSize:10`, `MaxBatchingWindow:5s`, `MaxConcurrency:5`, `ReportBatchItemFailures:true` | A1 | **none** — not FIFO; no SNS filter policy; **processor does not sort by `event_timestamp`** despite its docstring | redrive to DLQ after `MaxReceiveCount:5` | `cdk/lib/infra.go:585-641`; `cmd/event-processor/main.go:105-150`; `managers/eventprocessor/processor.go:74-150` |
| A3 | **Metadata `event-processor` → Consumption/billing** **[external]** | **SQS FIFO**, `MessageGroupId = ce_siteid`, `MessageDeduplicationId = sha256(ce_siteid|event_timestamp|type|event_source)` | per valid metering event | FIFO **per CE**, 5-min dedup window | send failure ⇒ whole event marked failed ⇒ SQS retry | `managers/eventprocessor/queue_client.go:49-105` |
| A4 | **Metadata config table DynamoDB Stream → `config-change-processor`** | DynamoDB Streams (`NEW_AND_OLD_IMAGES`) | any config item write | per-shard; `BatchItemFailures` by `SequenceNumber` | retry via batch item failure | `cmd/config-change-processor/main.go:72-117` |
| A5 | **`config-change-processor` → GCS auto-suspend queue** | **SQS FIFO**, `MessageGroupId = config_id` | `state` becomes `RUNNING`, or `auto_suspend.enabled` changes, or timeout changes while enabled | FIFO per config + dedup id over `(configID,eventType,enabled,timeout,csp,region,account)` | — | `managers/configchangeprocessor/{auto_suspend_detector.go:107-127, queue_client.go:55-146}` |
| A6 | **GCS `scheduler` (Fargate, `WORKER_MODE=SQS_PROCESSOR`)** consumes A5 + H28 | SQS long-poll | `event_type ∈ {auto_suspend, suspend, cluster_status_changed}` | none | message retained on error (visibility timeout) | `internal/scheduler/processor/sqs_processor.go:224-330` |
| A7 | **GCS scheduler → customer account SSM / Azure Key Vault** | `PutParameter` `/compute-engine-configs-{clusterID}` (SecureString) after `AssumeRole GlobalComputeRole`; Azure → secret `auto-suspend-configs-{env}` | `auto_suspend` event | — | error ⇒ SQS retry | `sqs_processor.go:422-470, 1813-1890` |
| A8 | **Engine `state.finalize` → per-CE SQS** | `aws sqs send-message` with body = `curl localhost:22222/provisioning-status` | end of bootstrap **and end of every autoscale integration batch** | none | — | `src/salt/state/finalize.sls:8-14`; re-fired from `orch/integrate_node_cluster.sls:398-404` |
| A9 | **Engine provision container ← per-CE SQS** | `receive-message` fallback when `:22222` unreachable | `wait_for_db` loop | none; message deleted after read | timeout ⇒ `save_err` | `src/aws/engine/modules/utils.sh:209-273` |
| A10 | **Pooling internal task queue** | **pgmq** on PostgreSQL, `read_with_poll(vt=40, max_poll=5s)` | `TaskClusterStart`, `TaskClusterStartDeployment`, `TaskClusterMonitorDeployment`, `TaskClusterCommand`, `TaskClusterOMSRegistration` | single consumer loop; visibility timeout + `heartbeat()` | task restarted by `run_service` supervisor | `pool_manager/queue/{tasks.py, consumer.py, client.py}` |
| A11 | **LMO Celery** | **SQS FIFO** broker + Redis/Valkey result backend | flow start via `POST /lmo/v1/flows/{name}` | per-flow | task retry policies per step | `src/lmo/celery.py`, `run.py` |
| A12 | **CloudWatch cron → GCS `status-monitor`** | EventBridge schedule | ~3–5 min | n/a | per-cluster goroutines, `MONITOR_CONCURRENCY` default 100, max 500 | `cmd/status-monitor/main.go:176-250` |
| A13 | **EventBridge (5 min) → Step Functions Distributed Map → LMO `ce-provisioning-health-check`** | Step Functions | scheduled site health sweep | `MaxConcurrency` 50 | per-site catch; results upserted to `…-vce-sites-health-status` | `HEALTH_MONITOR_REQUIREMENTS.md` R-1…R-11 |

### 1.3 On-engine local HTTP

| Endpoint | Server | Client | Purpose |
|---|---|---|---|
| `GET :22222/provisioning-status` → `{"engine":{"stage","state","version"}}` | `avcd-healthcheck-service` **[external]**, fed by Salt state `healthcheck.{configuring,configured,failed}` writing `/var/opt/teradata/salt/healthcheck/healthcheck.json` | provision container (`utils.sh:229`), pooling `InstanceHealth` (`instance_health.py:85`), `state.finalize` | bootstrap progress |
| `GET :22222/dbs-state` → `{"dbs state","pde state"}` | same | metering agent (`agent.py:875`), pooling (`instance_health.py:91`), provision container | DBS/PDE liveness |
| `GET :22222/cluster-health` → `{"state": …}` | same | **metering agent only** (`agent.py:890`) | **the signal behind the autoscale bug** |
| `GET :22222/system-info` | same | pooling `infra_info_single_instance` (`instance_health.py:114`) | arch/BOM/OS/instance list for the CMDB |
| `POST 127.0.0.1:23000/scale` | **metering agent** (Flask, leader only) | `ce-autoscaler` **[external]** | pushes `scale_size/total_amps/total_pes/instances/autoscale_status` |
| `POST :50061/scheduler-vce-telemetry-start` / `…-stop` | WES | provision container | telemetry lifecycle |
| gRPC `:50051` `EngineSuspend` → `localhost:3282` | WES | (AI-Unlimited workspaces path, **not** the CE cloud path) | `service/task/inactive/inactive.go` |

### 1.4 Salt event bus (inside the engine)

Configured in `src/files/common/engine_reactor.conf`:

```
salt/minion/*/start          → reactor/assign_index.sls      → runner cluster.assign_index()
cluster/node/index_assigned  → reactor/integrate_unconfigured.sls → local state.autoscale.prep_node_local (parallel)
cluster/node/prep_complete   → reactor/prep_complete.sls     → runner vconfig_queue (ordered by cluster index)
cluster/node/vconfig_complete→ reactor/vconfig_complete.sls  → runner integration_queue.maybe_trigger_batch()
cluster/node/bootstrap_ready → reactor/bootstrap_ready.sls   → mid-bootstrap node integration
cluster/node/decommission    → reactor/decommission_node.sls → runner decommission_queue.handle_request(targeted_amp_count)
```

`cluster/node/decommission` carries `{"data": {"targeted_amp_count": N}}`
(`src/salt/reactor/decommission_node.sls:8-19`). This is how `ce-autoscaler` **[external]** asks
for a scale-down. Scale-up is implicit: the autoscaler raises the follower ASG desired capacity and
new minions announce themselves with `salt/minion/*/start`.

**Nothing on this bus is forwarded to the cloud.** The whole autoscale state machine
(`cluster:state` grain) is invisible to GCS, pooling and metadata.

### 1.5 Notification payloads, verbatim

**A1 — metering SNS message** (`agent.py:822-836`):

```json
{
  "ce_siteid": "CEAM<CE_ID>",
  "event_type": "COMPUTE_ENGINE_STATE",          // or COMPUTE_ENGINE_AUTOSCALE_STATE
  "event_timestamp": "2026-08-01T10:15:00.123Z",
  "event_value": {
    "compute_engine_running_status": "RUNNING",  // or "STOPPED"
    "autoscale_status": "IDLE",                  // only on autoscale events; IDLE|UNHEALTHY
    "scale_size": 4,
    "total_amps": 32,
    "total_pes": 16,
    "instances": [{"type":"i4i.4xlarge","ip":"10.0.1.10","amps":16,"pes":8}]
  }
}
```
SNS `Subject` = `CE State: {site_id}/{instance_name} - {Scale Event|Termination|cluster_health}`,
truncated to 100 chars. Published **only from the leader** (`agent.py:788-792`).

**A3 — enriched event to consumption** (`models/meteringevent/enriched_event.go:48-67`):

```json
{ "ce_siteid":…, "type":"COMPUTE_ENGINE_STATE", "value":"RUNNING|STOPPED",
  "event_timestamp":…, "number_of_nodes":null,
  "organization_name":…, "vce_siteid":…, "event_source":"CE_STATUS_METERING|CE_AUTOSCALER",
  "license_package":"BASE", "cloud_service_provider":…, "region":…,
  "t_shirt_size":"4X", "compute_engine_type":"STANDARD",
  "compute_type":"DEDICATED|POOLED", "offering_type":"Standard Offer|Managed App" }
```
Note `type` is **always** `COMPUTE_ENGINE_STATE` — the autoscale event is flattened into the same
type and only distinguished by `event_source` (`enriched_event.go:108`).

**A5 — auto-suspend config change event** (`models/configchangeevent/config_change_event.go:17-29`):

```json
{ "event_type":"auto_suspend", "site_id":…, "ce_site_id":…,
  "compute_engine_config_id":…, "cluster_id":…,
  "enabled":true, "idle_timeout_minutes":30,
  "cloud_account_number":"", "csp":"AWS", "region":"us-west-2" }
```
`cloud_account_number` is empty for POOLED.

**H28 — suspend event from the engine** (`inactive_ce.go:561-571`):

```json
{ "event_type":"suspend", "account_id":…, "region":…, "cluster_id":…,
  "project_id":"headless-ce", "site_id":…, "ce_site_id":…,
  "suspend_interval":30, "timestamp":"…Z" }
```

**H3 — pooling → GCS status event** (`clients/gcs.py:26-33`, `internal/model/pooling.go:43-58`):

```json
{ "site_id":"TDICAM<SITE_ID>", "config_id":"CEAM<CE_ID>",
  "component_id":"<CLUSTER_UUID>",
  "status":"RUNNING", "oms_job_id":"…", "oms_status":"REGISTERED" }
```

**H6 — GCS infra PUT** (`clusterService.go:2733-2737`):

```json
{ "status":"RUNNING", "event_time":"2026-08-01T10:15:00Z",
  "infrastructure":{ "vpc_id","subnet_id","az_id","os_version","arch","bom_version",
                     "tdbms_version","engine_image_id","account_number",
                     "instances":[{"instance_type","ip","amps","pes"}] } }
```

**H12/H23/H26 — OMS registration, three producers, three different `collection_id`s** (see
[Appendix B](#appendix-b--vocabulary-mismatch-matrix)).

---

## Part 2 — The six state models

### 2.1 GCS DynamoDB `cluster-provisioning.status` (control-plane truth)
`PROVISIONING · CONFIGURING · RUNNING · TERMINATING · TERMINATED · FAILED_PROVISIONING · FAILED_TERMINATING`
(`internal/scheduler/monitor/scheduler.go:60-65`).
**There is no STARTING/STOPPING/STOPPED here.** "Stop" destroys the CE; the row is deleted and the
config returns to `NOT_PROVISIONED` (`clusterService.go:2260-2275`).
`FAILED_*` is **sticky** — the monitor refuses to overwrite it with a non-`FAILED_` upstream status
(`clusterService.go:2358-2371`).

### 2.2 Pooling PostgreSQL `cluster.state` (the only real START/STOP machine)
`pool_manager/cluster_state.py:28-66`, enum `models/responses.py:46-59`:

```
INITIALIZING ─finished→ STOPPED ─start→ PROVISIONING ─configure→ CONFIGURING ─finished→ RUNNING
     │fail                 │configure                                                      │stop
     ▼                     ▼                                                               ▼
FAILED_SETUP     UPDATING_INFRASTRUCTURE ─finished→ STOPPED                            STOPPING ─finished→ STOPPED
                           │fail                                                           │fail
                           ▼                                                               ▼
                  FAILED_INFRA_UPDATE ─configure→ UPDATING_INFRASTRUCTURE          FAILED_STOPPING ─stop→ STOPPING

STOPPED | FAILED_SETUP | FAILED_INFRA_UPDATE ─terminate→ TERMINATING ─finished→ TERMINATED (final)
```

### 2.3 Metadata config `state` (what the customer/UI sees)
12 values, membership-validated only, **no transition graph, no ordering guard, no ETag**
(`models/computeengineconfig/enum_state.go:22-33`):
`NOT_PROVISIONED · PROVISIONING · CONFIGURING · FAILED_PROVISIONING · RETRYING · RUNNING · STOPPED ·
TERMINATING · FAILED_TERMINATING · DELETION_IN_PROGRESS · FAILED_PRIVATELINK_DELETE · FAILED_CLUSTER_DELETE`

`STOPPED` is **never written by any control-plane component** — GCS maps `TERMINATED → NOT_PROVISIONED`
(`metadataService.go:806-808`). **The only writer of `STOPPED` is the metering telemetry path.**

### 2.4 On-engine `cluster:state` grain (the autoscale machine — cloud-invisible)
`src/salt/_runners/decommission_queue.py:36-79, 112-119`:

```
INITIALIZING → BOOTSTRAPPING → IDLE
                                 ├─ EXPANSION_IN_PROGRESS ──────────────→ IDLE
                                 └─ PREP_FOR_CONTRACTION ─commit→ CONTRACTION_IN_PROGRESS → IDLE
                                        │ ▲                                    │ phase-2 failure
                                        │ └── QUERY_DRAIN_TIMED_OUT            ▼
                                        └── EXPANSION_IN_PROGRESS      CONTRACTION_FAILED
                                            (expand aborts phase 1)     (durable; blocks ALL future
                                                                         scaling until operator
                                                                         runs force_reset())
```
Queue states: `ready_for_contraction, preparing, decommissioning, drain_timed_out, decommissioned,
aborted, failed, completed`.

### 2.5 Sub-resource states
- `oms_status` (GCS DynamoDB): documented `NOT_REGISTERED → PROVISIONING → REGISTERED |
  FAILED_REGISTRATION`; `DEREGISTERING → DEREGISTERED | FAILED_DEREGISTRATION`
  (`internal/model/cluster.go:129`).
- `qg_status`: `NOT_PROVISIONED → PROVISIONING → RUNNING`; `TERMINATING → TERMINATED`;
  `FAILED_PROVISIONING`; plus sentinel `NOT_APPLICABLE` (`cmd/status-monitor/main.go:780`).
- `vp_status` / `applications.viewpoint.status`, `applications.query_grid.status` — reuse `State`.
- PrivateLink (pooling→metadata): `IN_PROGRESS | SUCCESS | FAILED` (`clients/metadata.py:17-21`).
- Network service op status: `NOT_FOUND · LOCKED · IN_PROGRESS · SUCCESS · FAILED · COMPLETED`
  (`src/internal/models/status_models.go:26-32`).
- Network service health: `HEALTHY · RUNNING · DOWN · UNHEALTHY · MISCONFIGURED`
  (`src/internal/models/health_models.go:11-18`).
- LMO run: `PENDING · RUNNING · SUCCEEDED · FAILED · CANCELED`; task status adds `STARTED · RETRYING · UNKNOWN`
  (`callbacks/models.py:160-176`).
- OMS job **[external]**: `WAITING · RUNNING · COMPLETED · SUCCEEDED · FAILED · ERROR`
  (`services/oms_ce_admin/models.py:8-30`).

### 2.6 Translation at the pooling↔GCS boundary
`internal/util/cluster.go:15-33`:

| Pooling | → GCS |
|---|---|
| `PROVISIONING` / `CONFIGURING` / `RUNNING` | same |
| **`STOPPING`** | **`TERMINATING`** |
| **`STOPPED`** | **`NOT_PROVISIONED`** |
| `FAILED_RUNNING` | `FAILED_PROVISIONING` |
| `FAILED_STOPPING` | `FAILED_TERMINATING` |
| `INITIALIZING`, `UPDATING_INFRASTRUCTURE`, `TERMINATING`, `TERMINATED`, `FAILED_SETUP`, `FAILED_INFRA_UPDATE` | **unmapped → passed through uppercased** |

The unmapped set is a live bug — those strings are not valid metadata `State` values, so
`UpdateConfigState` 400s and `HandleClusterStatusEvent` returns 500 to pooling
(`poolingService.go:249-256`), which then retries the same status forever.

---

## Part 3 — Lifecycle walkthroughs

### 3.1 START — dedicated (SCORCH) CE

```
user/ce-agent/scheduler ──POST /clusters──▶ GCS api
  GCS: RBAC compute_engine:start_stop; GET metadata config; compute.type != POOLED
  GCS: if existing status == FAILED_PROVISIONING → conditional CAS FAILED_PROVISIONING→PROVISIONING   [clusterService.go:581-600]
  GCS ──POST /scorch/v2/components──▶ SCOrch
        body {name, manifest_name:"ce", manifest_version, platform, desired_status:"RUNNING", configuration}
        response data.status = "PROVISIONING", data.component_id                                       [clusterService.go:834-875, 1891-1915]
  GCS ──DynamoDB PutItem status=PROVISIONING, component_id, desired_status "RUNNING"
  GCS ──PATCH metadata {state:"PROVISIONING", cluster:{cluster_id, key_name}}                          [metadataService.go:817-845]

SCOrch ──▶ launches avcd-vce-engine-provision container COMPONENT_JOB_TYPE=CREATE
  setup_create → deploy_cft (CloudFormation, 5× AWS::EC2::Instance, NO ASG) → save_outputs
  create_targets (PrivateLink NLB targets) → wait_for_db → run_oms_registration                        [src/aws/engine/runner.sh:27-51]

engine (Salt): bootstrap → orch/engine.sls → state.finalize
  healthcheck.json {engine:{stage:"completed", state:"configured", version}}
  aws sqs send-message body=$(curl localhost:22222/provisioning-status)                                [state/finalize.sls:8-14]
  container polls :22222/provisioning-status, SQS as fallback                                          [utils.sh:209-273]

GCS status-monitor (cron): GET /scorch/v2/components/{id} → "RUNNING"
  → PUT metadata /infrastructure {status:"RUNNING", event_time, infrastructure{…}}                     [clusterService.go:2421-2450]
  → DynamoDB status=RUNNING
  metadata: UpdateComputeEngineConfigState sets state=RUNNING + last_launched_at + syncSCIM(register)  [update.go:569-647]
  DynamoDB stream → config-change-processor → auto_suspend SQS → GCS scheduler → SSM PutParameter      [A4/A5/A6/A7]
```

### 3.2 START — pooled CE

```
GCS ──POST /v1/clusters──▶ Pooling      (create record)
GCS ──PUT /v1/clusters/{uuid}/start──▶ Pooling
  Pooling: sm.start()  STOPPED→PROVISIONING; resolve cluster_size (capacity.vcpu or min_size × 16)     [cluster.py:645-745]
  Pooling: PoolRouter picks a LEADER instance type — only ONE t-shirt unit; followers come from an ASG
  Pooling: enqueue TaskClusterStart (pgmq)
  bg_start_cluster: claim leader from warm pool → create_cluster() builds FollowerLaunchTemplate +
                    FollowerAutoScalingGroup {prefix}-cluster-{uuid[-12:]}-followers                   [csp/aws/cluster.py:135-215]
                  → ensure_follower_capacity(desired_amps)   (no-op on AWS; ASG handles fallback)
                  → push_autoscale_config_to_leader(min/max/desired amps)                              [cluster.py:1173-1178]
                  → refresh_grains_on_leader(leader_configuration incl. cluster.asgs)
  Pooling: sm.configure()  PROVISIONING→CONFIGURING  → POST /clusters/status {status:"CONFIGURING"}
  Pooling: _update_instance_connectivity, _enqueue_oms_registration, enqueue TaskClusterMonitorDeployment

  monitor_deployment: poll :22222/provisioning-status on the INITIAL instance set until
                      stage=="completed" && state=="configured"; CONFIGURATION_TIMEOUT → FAILED_RUNNING [orchestration.py:236-300]
                      → sm.finished() CONFIGURING→RUNNING → POST /clusters/status {status:"RUNNING"}

  OMSRegistrationManager: LMO POST /lmo/v1/flows/oms-ce-registration → poll → POST /clusters/status
                          {status:<current pg state>, oms_status:"REGISTERED"|"FAILED"|"TIMEOUT"}       [oms_registration.py:95-248]

  cluster_events (blinker signals): on_internet_setup → DNS create → on_dns_name_created
      → PATCH metadata {compute:{dns_name, connectivity:{status:"IN_PROGRESS"}}, cluster:{cluster_id}}
      → POST GCS /privatelink                                                                          [cluster_events.py:147-301]
      on_connectivity_ready (Azure only) → PATCH metadata connectivity status "SUCCESS"

GCS HandleClusterStatusEvent (POST /clusters/status):
  reject if component_id mismatches stored one (409)                                                   [poolingService.go:200-207]
  MapPoolingStatus → DynamoDB
  metadata PATCH for every status EXCEPT RUNNING (skipMetadataStateUpdate)                             [poolingService.go:247]
  if oms REGISTERED && mapped RUNNING → updateInfrastructureOnRunning (PUT /infrastructure)            [poolingService.go:268-285]

GCS status-monitor also gates RUNNING on oms_status == "REGISTERED"                                    [clusterService.go:2375-2410]
```

### 3.3 RUNNING — steady-state signals

| Signal | Period | Effect |
|---|---|---|
| GCS `status-monitor` full scan of non-terminal **+ RUNNING** rows | cron ~3–5 min | reconcile vs SCOrch/pooling; QG/VP/STC LMO polling; OMS polling; failed-cluster cleanup (`clusterService.go:2011-2022`, `cmd/status-monitor/main.go:176-350`) |
| Metering agent SNS state event | **300 s** | → metadata `state` = `RUNNING`/`STOPPED`; → billing |
| WES idle poll | `EventIntervalMinutes` from SSM | `DBC.SESSIONINFO` count (excl. system users) → `FLUSH QUERY LOGGING WITH ALLDBQL` → `MERGE MAX(collecttimestamp)` into `inactivemon.qrylog_ts` → count rows newer than N min, with `inactivemon.init_time` "new system" guard (`inactive_ce.go:72-140, 454-540`) |
| Site health Step Function → LMO `ce-provisioning-health-check` | 5 min | per-site downstream health → `…-vce-sites-health-status` table |
| GCS `scheduler` per-cluster cron goroutines | `start_time`/`end_time` cron exprs | scheduled `POST /clusters` / `DELETE /clusters/{id}`; skips start if status ∈ {RUNNING, ACTIVE, PROVISIONING} (`monitor/scheduler.go:226-330`) |

### 3.4 STOP

```
trigger: DELETE /clusters/{id} | end-cron | auto-suspend (A6) | ce-agent stop_cluster
GCS: canTerminateCluster(status) — allowed only from ACTIVE, RUNNING, FAILED_TERMINATING            [sqs_processor.go:1549]
SCORCH: DELETE /scorch/v2/components/{id}  → container COMPONENT_JOB_TYPE=DELETE:
        OMS deregister → telemetry stop (POST :50061/scheduler-telemetry-stop) → Viewpoint delete
        → delete PL targets → purge+delete per-CE SQS → delete S3 folder → delete CFN stack
        → delete instance profile + IAM role                                                        [src/aws/engine/runner.sh:52-92]
POOLED: PUT /v1/clusters/{uuid}/stop → sm.stop() RUNNING→STOPPING → TaskClusterCommand bg_stop_cluster
        LMO OMS deregistration → POST /clusters/status {status:"STOPPING", oms_status:"DEREGISTERED"} [cluster.py:1266-1350]
        suspend_cluster_autoscale: clear per-instance scale-in protection, then ASG 0/0/0
        (ASG object is KEPT for reuse)                                                              [csp/aws/cluster.py:277-333]
GCS: DynamoDB status = TERMINATING; PATCH metadata state = TERMINATING
     QG deregister if enabled → qg_status TERMINATING
engine: metering agent SIGTERM → SNS {compute_engine_running_status:"STOPPED"} (leader only)        [agent.py:1183-1196]
```

### 3.5 STOPPED / terminal

```
pooling STOPPING → STOPPED  → GCS maps to NOT_PROVISIONED
SCOrch component gone (404) → GCS writes TERMINATED then NOT_PROVISIONED,
    unless status is already FAILED_* (preserved) or Azure-pooled inside the 3-strike 404 tolerance  [clusterService.go:2172-2275]
GCS: delete DynamoDB row; metadata state = NOT_PROVISIONED + last_terminated_at + syncSCIM(deregister)
WES: after a successful suspend POST it DELETES the SSM auto-suspend parameter                       [inactive_ce.go:615-618]
```

---

## Part 4 — Autoscaling, end to end

### 4.1 Where it applies

Autoscaling is **pooled-only** today:

```jinja
# src/salt/state/autoscale/init.sls:55-66
pause autoscaler:
  cmd.run:
    - name: ce-autoscaler pause
    - onlyif:
        - fun: match.compound
          tgt: 'G@roles:td_unlimited_leader and not G@features:pooling'
```
On dedicated (SCOrch) engines `ce-autoscaler` is started, then immediately **paused**. And the
dedicated CFN template contains five `AWS::EC2::Instance` resources and **no ASG at all**
(`src/aws/engine/templates/engine.yaml`).

Also set at bootstrap: a **fixed** TDWM concurrency throttle (`tdwm_fixed_limit: 20`) —
the previously dynamic per-CW throttle is disabled
(`src/salt/pillar/autoscale/init.sls:17-21`, `orch/integrate_node_cluster.sls:296-312`).

### 4.2 Scale-up (expand)

1. `ce-autoscaler` **[external]** raises the follower ASG desired capacity **and** `POST`s the new
   sizing to `127.0.0.1:23000/scale` on the metering agent (`agent.py:1347-1410`).
2. New followers boot → salt-minion → `salt/minion/*/start` → `cluster.assign_index()` →
   `cluster/node/index_assigned` → `state.autoscale.prep_node_local` (parallel per node).
3. `cluster/node/prep_complete` → `vconfig_queue` (ordered by cluster index) → `state.vconfig.expand`.
4. `cluster/node/vconfig_complete` → `integration_queue.maybe_trigger_batch()`
   (`STALL_TIMEOUT=30 s`, `MAX_TIMEOUT=300 s`, `ORCH_MAX_RETRIES=5`) →
   `state.orch orch.integrate_node_cluster`.
5. `orch/integrate_node_cluster.sls:120-300`:
   `cluster.sync_cluster_size` → `bynet.hosts` → `bynet.mpplist` → `tdops.tpa_snapshot` →
   `state.vconfig.expand` → `vconfig_sync.wait_for_sync` (600 s) →
   **`tdops.run_tpareconfig` (timeout 1200 s)** → `wait_pde_healthy` (300 s) →
   `poll_for_nogt` (300 s) → `wait_dbs_ready` (600 s) → `bteq_alter_spoolmap_ampcount` →
   `wait_no_newprocs` (300 s) → `wait_pde_healthy` (300 s) → snapshot.
6. `set_cluster_state_idle` → grain `cluster:state = IDLE`; then **`state.finalize` re-runs**, which
   re-posts `provisioning-status` to the provisioning SQS queue (`integrate_node_cluster.sls:398-404`).

**Worst-case disruption window ≈ 1200 + 300 + 300 + 600 + 300 + 300 ≈ 50 minutes**, i.e. up to
~10 metering ticks. Splitnet protection is explicitly disabled because `tpamaxnets` is boot-time
only (`integrate_node_cluster.sls:143-155`).

### 4.3 Scale-down (contract)

`ce-autoscaler` fires `cluster/node/decommission {targeted_amp_count}` →
`decommission_queue.handle_request()`:

- **Phase 1 (abortable)**: `IDLE → PREP_FOR_CONTRACTION`; `ALTER SPOOL MAP` → poll NoGT (600 s) →
  `DROP SPOOL MAP` with active-query drain (`ACTIVE_DRAIN_TIMEOUT = 1800 s`) → poll NEWPROC (600 s).
  Drain timeout ⇒ `QUERY_DRAIN_TIMED_OUT`, Go controller re-enters at the DROP step.
- **Commit point**: `PREP_FOR_CONTRACTION → CONTRACTION_IN_PROGRESS` (frozen).
- **Phase 2 (non-abortable)**: vconfig + `tpareconfig` (timeouts from
  `pillar/autoscale/init.sls`: `timeout_alter 1200`, `timeout_drop 7200`, `timeout_tpareconfig 1200`,
  `timeout_vproc_clear 300`).
- Phase-2 failure ⇒ **`CONTRACTION_FAILED`**, a durable state that **blocks all future scaling** until
  an operator runs `force_reset()` (`decommission_queue.py:56, 1137-1150, 2310`).

The autoscaler's event contract (documented in `decommission_queue.py:66-72`):

> Only emits contract events when state is `IDLE` or `QUERY_DRAIN_TIMED_OUT`; may emit expand events
> when `PREP_FOR_CONTRACTION` (aborts phase 1); **suppresses ALL events** when
> `CONTRACTION_IN_PROGRESS` or `EXPANSION_IN_PROGRESS`; **halts with degraded-mode alert** when
> `CONTRACTION_FAILED`.

### 4.4 What the cloud observes during a scale

**Nothing structural.**

| Component | During expand/contract |
|---|---|
| Pooling `cluster.state` | stays `RUNNING`. `monitor_deployment` only watched the *initial* instance set and exited at `sm.finished()` (`orchestration.py:236-322`). There is no follower or post-RUNNING health watcher anywhere in the pooling service. |
| GCS `status-monitor` | pooling says `RUNNING`, DynamoDB says `RUNNING` → "status unchanged" branch → **no metadata write**; self-heal is `platform == AZURE && POOLED` only (`clusterService.go:2496-2516`). |
| Metadata `state` | untouched by the control plane… |
| **Metering agent** | …but it samples `:22222/cluster-health` every 300 s **and** synchronously inside `/scale`, and publishes `compute_engine_running_status`. **This is the only cloud-visible signal, and it is the one that says `STOPPED`.** |
| `cluster:state` grain | `EXPANSION_IN_PROGRESS` / `CONTRACTION_IN_PROGRESS` / `CONTRACTION_FAILED` — read by nobody outside the engine. |

---

## Part 5 — Root cause: the autoscale "degraded" bug

### 5.1 The classification

```python
# cog-compute-metering/metering-agent/agent.py:555
cluster_health_problem_states = {"degraded", "critical", "down", "STOPPED"}

# agent.py:800-806
is_scale_event  = final_message == "Scale Event"
cluster_health  = status_data.get("cluster_health", "unknown")
running_status  = "STOPPED" if cluster_health in cluster_health_problem_states else "RUNNING"
event_type      = COMPUTE_ENGINE_AUTOSCALE_EVENT_TYPE if is_scale_event else COMPUTE_ENGINE_EVENT_TYPE
```

### 5.2 The debounce is dead code

```python
# agent.py:930-953  — implements 3 × 180 s re-check
def confirm_cluster_health(private_ip, wait_time=180, confirmation_retries=3): ...

# agent.py:956-962  — the ONLY caller path, and it bypasses them
def get_status(private_ip):
    """Uses the confirmation wrappers to capture final status."""   # ← docstring lies
    final_dbs_status     = check_dbs(private_ip)
    final_cluster_status = check_cluster_health(private_ip)
    return {"dbs_state": final_dbs_status, "cluster_health": final_cluster_status}
```
`confirm_cluster_health` and `confirm_dbs_status` have **zero call sites** in the repo. A single
transient `degraded` sample immediately produces `STOPPED`.

### 5.3 The `/scale` handler samples health mid-scale

```python
# agent.py:1379-1391
private_ip = get_private_ip()
status = get_status(private_ip)                 # ← samples health WHILE tpareconfig is running
upload_to_sns(instance_details, status, final_message="Scale Event")
```
So the autoscale event itself is the single most likely message to carry `STOPPED`.

### 5.4 The write path into the customer-visible `state`

```go
// accp-metadata-service/managers/eventprocessor/processor.go:230-231
p.updateComputeEngineStateBestEffort(ctx, event, metadata, runningStatus)

// processor.go:421-424 — gate
func shouldUpdateState(metadata) bool {
    return metadata.State != nil && (*metadata.State == StateRunning || *metadata.State == StateStopped)
}

// processor.go:477-486 — the ONLY protection, Azure-pooled only
func isAzureTelemetryStateRegression(metadata, reported) bool {
    if !isAzurePooledEngine(metadata) { return false }     // ← AWS returns false ⇒ NOT protected
    ...
}

// processor.go:461-469 — same scoping for the autoscale event
func shouldUpdateAutoscaleState(metadata) bool {
    if isAzurePooledEngine(metadata) { return false }
    return shouldUpdateState(metadata)
}
```

then a bare, unconditioned write:

```go
// managers/eventprocessor/metadata_client.go:189-215
update := expression.UpdateBuilder{}.Set(Name(StateAttr), jsonValue(status.String()))
condition := expression.AttributeExists(Name(ComputeEngineConfigIdAttr))   // ← only "row exists"
client.UpdateItem(...)                                                      // no version, no timestamp
```

### 5.5 The full chain

```
tpareconfig / vproc reshuffle / DBS logon rejection (EM_TIMEOUT 217)
  └─ :22222/cluster-health → "degraded"                                [avcd-healthcheck-service, external]
      └─ agent.get_status()  ── NO DEBOUNCE (5.2) ──▶ upload_to_sns()
          └─ SNS {"compute_engine_running_status":"STOPPED",
                  "event_type":"COMPUTE_ENGINE_AUTOSCALE_STATE"|"COMPUTE_ENGINE_STATE"}
              └─ STANDARD SQS (batch 10, MaxConcurrency 5, no FIFO, no sort)
                  └─ event-processor
                       • shouldUpdateState: current ∈ {RUNNING, STOPPED} ⇒ apply
                       • isAzureTelemetryStateRegression: blocks ONLY Azure pooled  ⇒ AWS unprotected
                  └─ DynamoDB UpdateItem  state = "STOPPED"   (no ordering / version guard)
                       ├─▶ Console / GET /compute-engine-configs shows STOPPED while the CE is expanding
                       ├─▶ enriched event value="STOPPED", event_source="CE_AUTOSCALER" → Consumption
                       │     (ce-assistant classifies STOPPED as BillableNo — billing.go:29-40)
                       └─▶ DynamoDB stream → config-change-processor
                             on the recovery STOPPED→RUNNING flap, re-emits an `auto_suspend` event
                             → GCS scheduler → AssumeRole GlobalComputeRole → SSM PutParameter
                               in the customer account (amplification)
GCS never repairs it on AWS: status-monitor takes the "unchanged" branch and the self-heal
(reconcileMetadataStateIfDiverged) is gated on platform == AZURE && POOLED.  [clusterService.go:2508-2515, 2619-2670]
```

### 5.6 Why the CE sometimes never recovers

Normally the next healthy 300 s tick re-asserts `RUNNING` (allowed because `shouldUpdateState`
permits `STOPPED → RUNNING`). It does **not** recover when:

- `cluster-health` stays in a problem state (e.g. `CONTRACTION_FAILED`, disks grain `degraded` at
  `src/salt/_grains/td_disks.py:665`);
- a *late* `STOPPED` from the standard queue is delivered after the recovery `RUNNING`
  (no ordering guard, `MaxConcurrency: 5`);
- the agent lost leadership detection and stopped publishing;
- the scale event's `scale_size` was not a canonical t-shirt size, so the autoscale event DLQ'd
  before the infra snapshot / autoscale fields were written (defect #9 below).

---

## Part 6 — Defect register

### 6.1 Directly causing the autoscale/degraded symptom

| # | Defect | Evidence | Impact |
|---|---|---|---|
| **1** | Debounce is dead code — one transient `degraded` sample flips the CE to `STOPPED` | `agent.py:904-953` defined, **0 call sites**; `agent.py:956-962` bypasses them | **Primary cause** |
| **2** | `/scale` samples health synchronously during the scale | `agent.py:1379-1391` | The autoscale event is the most likely carrier of the false `STOPPED` |
| **3** | Telemetry is allowed to write the lifecycle `state` at all | `processor.go:230-270`, `metadata_client.go:176-215` | Two uncoordinated writers on one attribute |
| **4** | Regression guard is **Azure-pooled only** | `processor.go:461, 477` | **AWS — the primary platform — is unprotected** |
| **5** | GCS self-heal is **Azure-pooled only** | `clusterService.go:2508-2515, 2619-2670` | On AWS a bad `STOPPED` persists until the next healthy tick |
| **6** | No ordering/staleness guard; standard (non-FIFO) queue; `MaxConcurrency: 5`; docstring promises a chronological sort that does not exist | `cdk/lib/infra.go:595-641`; `processor.go:74-150` ("Step 2: Sorts events chronologically") | A late `STOPPED` can land after a `RUNNING` and stick |
| **7** | Engine `cluster:state` never leaves the node | `decommission_queue.py:112-119`; no consumer anywhere | Nothing can suppress health reporting during a known-transitional window |
| **8** | Health vocabulary is a hardcoded guess, asymmetric failure modes | `agent.py:555` vs `check_cluster_health` lowercasing at `agent.py:891` | `"STOPPED"` can never match (lowercased); `"unhealthy"`/`"warning"` would be read as RUNNING; exception ⇒ `"unknown"` ⇒ **fails open to RUNNING**, while `degraded` **fails closed to STOPPED** |
| **9** | `scale_size` domain mismatch | agent accepts any int ≥ 0 (`agent.py:1258-1264`); metadata requires ≥ 1 **and** a canonical size — valid set {1,2,3,4,6,8,10,12,16,24,32} (`enum_compute_size.go:40-53,116`) | A 5X/7X/9X/20X intermediate makes `processAutoscaleEvent` **return an error** ⇒ retried 5× ⇒ **DLQ**; no state update, no `current_size`, no infra snapshot |
| **10** | Autoscale globals never reset | `agent.py:96-100`, set only in `/scale` | Every later 5-min *state* event carries stale `scale_size/total_amps/total_pes/instances` |
| **11** | Cluster size read once at boot | `load_salt_grains()` called once (`agent.py:1466`); `get_cluster_size()` reads cached `SALT_INFO` | `node_count` frozen at boot size, wrong after any scale |
| **12** | Leader detection **fails open** | `agent.py:751` `IS_LEADER_NODE = True` on grain-load exception | A follower that cannot read grains speaks for the whole CE — and on scale-in its SIGTERM handler publishes a terminal `STOPPED` (`agent.py:1183-1196`) |
| **13** | `agent_shutdown.py` is a silent no-op | it imports `agent` and calls `capture_status()`, but `SNS_TOPIC_ARN`/`IS_LEADER_NODE`/`CLOUD_PROVIDER` are initialised only inside `if __name__ == "__main__":` (`agent.py:1432-1504`) | `upload_to_sns` returns at `agent.py:783`. The documented shutdown event never fires from this path |
| **14** | `state.finalize` re-fires on every integration batch | `orch/integrate_node_cluster.sls:398-404` → `state/finalize.sls:8-14` | Duplicate "provisioning complete" SQS messages long after the CE is RUNNING |
| **15** | Flap amplification into the customer account | `auto_suspend_detector.go:57-62, 292-310` fires on any `oldState != RUNNING && newState == RUNNING` | Every false `STOPPED → RUNNING` triggers a cross-account `AssumeRole` + SSM `PutParameter` |

### 6.2 Other lifecycle defects found

| # | Defect | Evidence | Impact |
|---|---|---|---|
| 16 | **Pooled CE can wedge permanently below RUNNING.** Pooling sends `oms_status ∈ {REGISTERED, FAILED, TIMEOUT, DEREGISTERED, FAILED_DEREGISTRATION}`; GCS persists it verbatim, but its RUNNING gate only accepts `"REGISTERED"` and its "initialise" branch only fires for `nil`/`"DEREGISTERED"`; the cron OMS switch has no case for `FAILED`/`TIMEOUT` (`default: Debug("OMS in unknown state")`) | `oms_registration.py:104,118,180,211,232`; `poolingService.go:213-219`; `clusterService.go:2375-2410`; `cmd/status-monitor/main.go:1501-1503` | CE stuck in `CONFIGURING` forever; only operator intervention clears it |
| 17 | **Unmapped pooled states reach metadata** — `FAILED_SETUP`, `FAILED_INFRA_UPDATE`, `UPDATING_INFRASTRUCTURE`, `INITIALIZING`, `TERMINATED` | `util/cluster.go:15-33` vs `enum_state.go:22-33`; `poolingService.go:249-256` returns 500 | Pooling retries the same status in a loop |
| 18 | **Three OMS `collection_id` conventions** — see Appendix B | `omsService.go:37-42`; `ce_registration.py:113`; `src/oms/config.sh:47-53` | Same CE can be filed under different OMS collections depending on which path registered it; LMO's docstring claims parity with GCS but does not match |
| 19 | **`Idempotency-Key` sent but never read** | `pool_manager/clients/gcs.py:90`; no `Idempotency` handling in GCS | `stamina` retries can double-apply status writes |
| 20 | **WES auto-suspend deadlocks on the success path** — unbuffered `errChan` is only written on error; `err = <-errChan` blocks forever; `defer close(errChan)` unreachable | `service/task/inactive-ce/inactive_ce.go:123-135` | Every successful auto-suspend leaks a goroutine + DB session |
| 21 | **Auto-suspend silently self-disables** — after a successful suspend POST, WES deletes the SSM parameter; thereafter `enabled, _, _ := utils.FetchAutoSuspendConfig()` **discards the error** and returns `enabled=false`; the parameter is only recreated on a fresh `→ RUNNING` DynamoDB-stream transition | `inactive_ce.go:615-618, 544`; `scheduler.go:124-127`; `auto_suspend_detector.go:57-62` | If the suspend is refused (CE not in a terminatable state) auto-suspend stays off until the next RUNNING transition |
| 22 | **Telemetry-driven `RUNNING` bypasses bookkeeping** — `eventprocessor.UpdateState` writes only `state`; no `last_launched_at`/`last_terminated_at`, no `syncSCIM` | `metadata_client.go:176-215` vs `managers/computeengineconfig/update.go:593-647` | Launch/terminate timestamps drift; SCIM sync skipped |
| 23 | **Auto-suspend POST sends JSON with `Content-Type: application/x-www-form-urlencoded`** | `inactive_ce.go:641` | Works only because the receiver ignores content type; brittle |
| 24 | **`UpdateAutoscaleFields` writes nested paths with no parent-map bootstrap** (contrast `initCSPDetailsIfAbsent`) | `metadata_client.go:229-232` vs `update.go:657` | Safe only because `size` and `auto_scale` are mutually exclusive; fragile |
| 25 | **`MeteringEvent.NumberOfNodes` is never populated** | model at `metering_event.go:19-25`; agent payload at `agent.py:822-836` omits it | Dead field; node count is not billable-visible |
| 26 | **`ce-agent` is not read-only** — exposes `start_cluster`/`stop_cluster` LLM tools that call `POST /clusters` and `DELETE /clusters/{id}` | `internal/tools/gcs_cluster.go:70-160` | An LLM can destroy a CE; worth an explicit confirmation gate |
| 27 | **Pooling `_notify_gcs` sends the raw pg state as `status`** including states GCS cannot map | `oms_registration.py:243-249` | Feeds defect 17 |

---

## Part 7 — Recommendations

### Immediate — agent-side only, no control-plane change
1. **Call the debounce.** In `get_status()` use `confirm_cluster_health()` / `confirm_dbs_status()`,
   or better, replace the 9-minute blocking sleep with an N-of-M sliding window evaluated on the
   existing 300 s loop.
2. **Stop sampling health in `/scale`.** An autoscale event should report sizing only; leave
   `compute_engine_running_status` to the periodic loop or reuse the last known good value.
3. **Consult `cluster:state` before classifying.** Read the leader grain (or `ce-autoscaler status
   --json`); if it is `EXPANSION_IN_PROGRESS` / `PREP_FOR_CONTRACTION` /
   `CONTRACTION_IN_PROGRESS`, suppress the `STOPPED` classification.
4. **Refresh grains periodically** (leader role + cluster size) and make leader detection
   **fail closed** (`IS_LEADER_NODE = False` on error).
5. **Reset the autoscale globals** after each publish; fix `agent_shutdown.py` to initialise the
   module (or drop it and rely on the SIGTERM handler).
6. **Align the `scale_size` domain** with `computeengineconfig.ComputeSize` and reject at the agent
   instead of DLQ-ing at the processor.

### Short term — control plane, mirrors the existing Azure fix
7. **Generalise `isAzureTelemetryStateRegression` / `shouldUpdateAutoscaleState` to all CSPs and
   both compute types.** Lifecycle is owned by pooling/SCOrch/OMS everywhere, not just on Azure.
8. **Generalise `reconcileMetadataStateIfDiverged`** beyond `platform == AZURE && POOLED`.
9. **Add an ordering guard**: persist `state_event_time` and make `UpdateState` /
   `UpdateAutoscaleFields` conditional on
   `attribute_not_exists(state_event_time) OR state_event_time < :event_time`. Cheap, and it fixes
   the out-of-order problem structurally.
10. **Either sort by `event_timestamp` in `ProcessEvents`** (the docstring already promises it)
    **or make the ingest queue FIFO** with `MessageGroupId = ce_siteid`.
11. **Complete `MapPoolingStatus`** for `INITIALIZING`, `UPDATING_INFRASTRUCTURE`, `TERMINATING`,
    `TERMINATED`, `FAILED_SETUP`, `FAILED_INFRA_UPDATE`; and **normalise `oms_status`** at the
    `POST /clusters/status` boundary (`FAILED → FAILED_REGISTRATION`, `TIMEOUT →
    FAILED_REGISTRATION`) so pooled CEs cannot wedge (defect 16).
12. **Honour the `Idempotency-Key`** that pooling already sends.

### Medium term — design
13. **Separate health from lifecycle.** Introduce a distinct `health` field
    (`HEALTHY | DEGRADED | UNHEALTHY | SCALING`) owned by telemetry, and keep `state` exclusively
    control-plane-owned. "Degraded" is currently being laundered into "STOPPED", which is why both
    the console and billing misbehave.
14. **Propagate the engine autoscale state to the cloud** so the console can show "Resizing…" and so
    `CONTRACTION_FAILED` (which permanently blocks scaling) raises an alert.
15. **Add a post-RUNNING health watcher to the pooling service** — today nothing watches a pooled
    cluster after `sm.finished()`.
16. **Unify the OMS registration contract** across GCS / LMO / the provision container.
17. Fix the WES `errChan` deadlock and stop discarding the `FetchAutoSuspendConfig` error.

---

## Appendix A — Who writes which field

| Field | Store | Writers |
|---|---|---|
| `status` | GCS DynamoDB `cluster-provisioning` | GCS api (create/delete), GCS `status-monitor`, GCS `HandleClusterStatusEvent` |
| `state` | Metadata config table | GCS `UpdateConfigState` (PATCH), Metadata `PutInfrastructure` (PUT, `RUNNING`), **Metadata `event-processor` (metering)**, Metadata create/delete managers |
| `compute.auto_scale.current_size` / `.status` | Metadata config table | **Metadata `event-processor` only** (`UpdateAutoscaleFields`) |
| `compute.dns_name`, `compute.connectivity.*` | Metadata config table | Pooling `MetadataClient.update`, Network service `UpdatePrivateLinkStatus`, GCS `UpdatePrivateLinkState` |
| `applications.query_grid.status` | Metadata config table | GCS `UpdateQueryGridConfigState` |
| `applications.viewpoint.status` | Metadata config table | GCS `UpdateViewpointConfigState` (gated on `add_system_to_viewpoint`) |
| `last_launched_at` / `last_terminated_at` | Metadata config table | **only** `UpdateComputeEngineConfigState` (manager path) — not the metering path |
| infrastructure snapshot | Metadata infra table | GCS `PUT …/infrastructure`; **Metadata `event-processor`** on autoscale events (`UpdateInstances`) |
| `oms_status`, `oms_job_id` | GCS DynamoDB | GCS `status-monitor`, GCS `HandleClusterStatusEvent` (verbatim from pooling) |
| `qg_status`, `qg_component_id`, `postprovision_run_*`, `vp_*`, `stc_*` | GCS DynamoDB | GCS `status-monitor` |
| `pooling_not_found_count` | GCS DynamoDB | GCS `updateClusterStatus` (Azure pooled only) |
| `cluster.state` | Pooling PostgreSQL | `ClusterStateMachine.on_enter_state` only |
| `cluster:state` grain | Engine leader | `integration_queue`, `decommission_queue`, `orch/*.sls` |
| `healthcheck.json` | Engine node | Salt `_states/healthcheck.py` (`configuring`/`configured`/`failed`) |
| SSM `/compute-engine-configs-{clusterID}` | Customer account | GCS `scheduler` (write), WES (read + **delete**) |

## Appendix B — Vocabulary mismatch matrix

| Concept | Producer A | Producer B | Producer C | Consequence |
|---|---|---|---|---|
| OMS `collection_id` | GCS: `configID` (`omsService.go:37-42`) | LMO: `site_id.upper()` (`ce_registration.py:113`) | provision container: `UPPERCASE_COMPONENT_NAME` (`src/oms/config.sh:47-53`) | Same CE filed under different OMS collections depending on the registration path |
| OMS terminal job status | GCS status-monitor: `SUCCEEDED`/`FAILED` (LMO run) | LMO: `COMPLETED`∪`SUCCEEDED` success, `FAILED`∪`ERROR` failure (`oms_ce_admin/models.py:20-21`) | provision container: loops until exactly `"SUCCEEDED"` (`src/oms/config.sh:113`) | Container can hang on a `COMPLETED` job |
| `oms_status` | Pooling emits `REGISTERED, FAILED, TIMEOUT, DEREGISTERED, FAILED_DEREGISTRATION` | GCS expects `NOT_REGISTERED, PROVISIONING, REGISTERED, FAILED_REGISTRATION, DEREGISTERING, DEREGISTERED` | — | Defect 16 — permanent wedge |
| Cluster status | Pooling: 13 values | GCS: 7 values | Metadata: 12 values | Defect 17 — 6 pooled values unmapped |
| Compute size | Agent `/scale`: any int ≥ 0 | Metadata: {1,2,3,4,6,8,10,12,16,24,32} | — | Defect 9 — DLQ |
| Health | `:22222/cluster-health`: unknown vocabulary **[external]** | Agent problem set `{degraded, critical, down, STOPPED}` (lowercased comparison) | Network svc: `HEALTHY/RUNNING/DOWN/UNHEALTHY/MISCONFIGURED` | Defect 8 — `"STOPPED"` unmatchable; unknown values fail open |
| Billability | `ce-assistant`: `STOPPED → BillableNo` (`billing.go:29-40`) | Consumption **[external]** consumes `value:"STOPPED"` | — | False `STOPPED` during a scale = billing gap |
