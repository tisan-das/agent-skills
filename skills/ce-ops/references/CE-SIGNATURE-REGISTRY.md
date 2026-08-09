# CE Failure-Signature Registry

**Purpose:** stop re-investigating the same defect. Every entry is a *grep-able signature*
recovered from a real Jira thread, with the mechanism that was actually proven, how to
confirm it, and where it was fixed.

**How to use:** in Step 2 (DEDUP) of `CE-DEBUG-SOP.md`, grep this file for the error code
or the distinctive log string **before** opening an investigation.

```bash
grep -i -n "9710\|SubCode" CE-SIGNATURE-REGISTRY.md
grep -i -n "AssignSlot\|WAITING_DNS" CE-SIGNATURE-REGISTRY.md
```

**Evidence base:** 535 Jira issues labelled `CE`, 3,462 comments (Nov 2024 – Aug 2026).
Every row cites the Jira it came from — verify before acting on it.

**Last update — CE 2.0 preprod UAT batch (2026-08-01 → 08-03), 6 tickets / 4 independent causes.**
Added #59, #60, #61, #62 and #16b; enriched #58; corrected the cross-reference on #6.
The four causes were **unrelated** — they shared a window and two sites, nothing more — but all
four were amplified into multi-day outages by the same three registered gaps (KB D1/D2/D7/D9).
See *Verdict hazards* for the one verdict in that batch that is **not** safe to trust.

**Legend — root-cause class** (see SOP §7): R1 timing/race · R2 stale-or-unpersisted state ·
R3 code defect · R4 capacity · R5 config drift · R6 infra/network · R7 not-a-defect.

---

## D1 — Control plane / provisioning

| # | Signature (grep) | Symptom | Mechanism proven | Class | Confirm | Jira / fix |
|---|---|---|---|---|---|---|
| 1 | `AWS-AssignSlot-Error`, `private dns verification timed out`, `WAITING_DNS`, `connectivity: FAILED` | CE stuck `NETWORK_PROVISIONING` / provisioning_failed | Route 53 TXT record created, but AWS never marked the endpoint-service private-DNS name *verified* within the poll window → slot assignment fails | R6 | Route 53 hosted zone: TXT present? VPC endpoint service "Private DNS name" verification = verified? | CCP-11948; open recurrence **COPS-25103** |
| 2 | `MCS API returned 400`, `container_name":"pool-manager`, `Triggering OMS registration for cluster` | Pooled CE → "Failed to start"; OMS registration fails | OMS could not complete registration to the pooled CE inside the timeout — reachability/latency between OMS and the CE, not an MCS logic bug. Mitigated by raising OMS-registration timeout (45s → 60s) | R1 + R6 | On OMS: `docker logs router-restapi-restapi-1 2>&1 \| grep -B2 -A10 '<CE config id>'`; resolve the CE DNS name; test port 1025 | **COG-15345** (open), GPSC-3817, IDR-692 |
| 3 | `System not found in MCS` on Get OMS status | Pooled CE status check errors | **GCS emits CE-ids in ALL-CAPS, the config container uses lower-case** — identifier-format mismatch between two services | R3 | Compare the CE-id casing in the GCS request vs the container config | REGULUS-2757 |
| 4 | CE stuck `Starting` after edit/re-provision; `oms_status` | Pooled CE never reaches RUNNING after being deleted and recreated | State-machine bug in the POOLED **re-provisioning** path: `oms_status` left `DEREGISTERED` in DynamoDB, so the gating logic never re-triggers the OMS registration cron | R2 | Read `oms_status` on the cluster item in DynamoDB | GPSC-3394 |
| 5 | `HTTP 409`, `Wrong state`, delete rejected | Cannot delete a pooled CE | Pooling service still has the CE `RUNNING` while UI/metadata show `NOT_PROVISIONED`; pooling only allows `terminate` from `STOPPED` | R2 | Query pooling service state and metadata state at the same instant — they disagree | GPSC-3751 |
| 6 | `DELETION_IN_PROGRESS`, UI "Stopping" but API "STOPPED" | UI stuck forever | Delete-PrivateLink actually succeeded in GNS; **metadata service** mis-reads the delete status | R3 | GNS shows delete complete; metadata still in progress | CCP-13921. **Not** the same mechanism as CCP-14508 / GPSC-3907 — see **#60** |
| 7 | `*.gateway.qa.cloud.<CORP_DOMAIN>` … DNS resolution fails | Whole site "down"; pooled CEs won't start | Site-gateway DNS hostname unresolvable → SCOrch and OMS health endpoints unreachable → OMS registration fails | R6 | `dig`/`nslookup` the site gateway FQDN from the control plane | COG-15036 |
| 8 | `409 Conflict` on a follower **VMSS**, "Failed stopping" | Azure CE goes to Failed stopping | Duplicate-delivery / concurrency race: a `PUT /stop` arrived **in the same second** the cluster reached RUNNING, colliding with in-flight ARM operations left by capacity churn | R1 | ARM activity log around the transition; look for stop enqueued at the RUNNING timestamp | REGULUS-3737 → fixed in **PACE-155** |
| 9 | `az stack group create`, Azure "internal host error" | 3x CE "Failed starting", succeeds on retry | Transient Azure host allocation failure | R6 | Retry; check ARM error body | REGULUS-3712 |
| 10 | `ResourceInitializationError: unable to pull secrets or registry auth` | Create cluster fails with no error surfaced | Fargate task could not fetch the MCPA token secret to pull images (network/VPC-endpoint setup incomplete; AMI lookup had failed earlier and aborted endpoint setup) | R6 | ECS task stopped-reason; VPC endpoints for ECR/Secrets Manager | REGULUS-2271 |
| 11 | `504 Gateway Time-out` fetching `ce-secrets` | Pooled CE provisioning fails intermittently | Component-fetch for the IAM role times out at the gateway | R6 | Retry rate + gateway logs for the component service | **GPSC-3857 (open, unassigned)** |
| 12 | `Cannot delete computeEngineConfig in state FAILED_PROVISIONING` | Cannot clean up failed CEs via API | No delete transition from `FAILED_PROVISIONING` in the state machine | R2 | Attempt delete via Swagger and read the state guard | **GPSC-3519 (open)** |
| 13 | `Insufficient Capacity` | CE fails to start | Cloud capacity shortage for the instance family | R6 | Cloud provider capacity/quota for the size + AZ | REGULUS-2531 |
| 14 | `auto_scale` field required / manifest 1.0.1 | Provisioning error after enabling autoscale | Autoscaling **manifest not uploaded to the Scorch instance**; newer manifest requires `auto_scale` in the payload | R5 | Compare manifest version registered in Scorch vs the BOM | REGULUS-3077 |
| 59 | `FAILED_DEREGISTRATION`, `OMS deregistration failed, requires investigation - no retry`, MCS `400` on the DELETE then `409` on the next register | CE stops/starts fine once; the **second** start fails → `FAILED_PROVISIONING`, UI "Failed starting" | OMS de-registration failed during the *previous* stop (MCS returned 400). GCS records `oms_status=FAILED_DEREGISTRATION` **but never writes a cluster status** — there is no `FAILED_STOPPING` — so nothing blocks the next start, which then hits `409 already registered`. The **registration** path escalates to `FAILED_PROVISIONING`; the **de-registration** path only logs. That asymmetry is the defect | R2 | Read `oms_status` **and** `status` on the DynamoDB item; pull the LMO run for the **stop** and see which step failed (`deregister_ce_from_oms` = OMS/MCS owns it, `wait_for_oms_ce_deregistration` = LMO owns it) — **24 h TTL, capture first**; `grep -rn "FAILED_DEREGISTRATION" cog-global-compute` and compare with the registration branch | **IDR-734** → **GPSC-3910** (open). Related but distinct: #4 |
| 60 | `"state":"DOWN"` + `NoGT` from `:22222/cluster-health`, `compute_engine_running_status": "STOPPED"`, `Cluster status unchanged, no update needed` (×1000s) | UI stuck **"Stopping"**, API/metadata `STOPPED`, engine actually `RUN/STARTED` with logons enabled — for days | The metering agent collapses **health into lifecycle**: `"STOPPED" if cluster_health in {degraded,critical,down,STOPPED}`. One `NoGT` vproc makes `/cluster-health` return DOWN; the event-processor writes `STOPPED` straight into metadata `state` via raw `UpdateItem`; and **nothing reverses it** — GCS can never emit `STOPPED`, its cron compares only its own DynamoDB, and `reconcileMetadataStateIfDiverged` is gated to Azure. `get_status()` also bypasses the `confirm_cluster_health()` debounce, so a single reading flips a customer-visible state | R3 + R2 | `psh pdestate -a` vs `curl :22222/cluster-health` — they must disagree, and the `reason` string names the vproc/node; event-processor log for the RUNNING→STOPPED flip; `grep -c "no update needed"` in status-monitor. **Then keep going and find why the vproc went NoGT** — usually a contraction that could not drain (#58) | **GPSC-3907**; KB §7.1; gaps D1/D2/D7. **CCP-14508 = same symptom, cause never verified on its own engine** (see *Verdict hazards*). Azure variant GPSC-3903 unverified |

---

## D2 — On-host / PDE / Salt

> **Two different mechanisms produce `Event 13912` / `failed reconcile`.** #16a and #16b are
> near-identical in `messages`, have different triggers and different owners. Never stop at the
> event number — run the confirm step for both.

| # | Signature (grep) | Symptom | Mechanism proven | Class | Confirm | Jira / fix |
|---|---|---|---|---|---|---|
| 15 | `queue: True`, `module.run` → `state.sls`, `orch/engine.sls`, highstate never returns | Dedicated CEs intermittently "Failed starting"; expansion hangs | **Nested Salt state execution with `queue: True` deadlocks the leader's salt-minion** — the outer state holds the queue the inner state waits on. Follower completes fine; leader hangs before Python module init | R1 | Leader minion log: `state.highstate` starts, never completes; follower identical run succeeds | REGULUS-3080, REGULUS-3549, REGULUS-3640 → AWS-VCE-CE-1.0.0.0.04/.05; interim workaround documented in Confluence |
| 16a | `Event 13912`, `Event 13895`, `PDE is not operational - Cannot open PDE device`, `DOWN/HARDSTOP`, `retcode 61` | Expansion fails; DB down and never recovers | New follower can't reconcile with the leader → forced TPA restart → hard stop. In this confirmed case caused by **PMA-ID collision from a missing BYNET teardown on decommissioned nodes** | R1/R3 | Leader `messages` around the reconfigure; `tdinfo: can't find node_num N in vconfig GDO`; **prior decommission at that slot** — this is what distinguishes 16a from 16b | REGULUS-3207 / REGULUS-3208 / REGULUS-3211 → `avcd-vce-engine-configure` **PR #344**; also ce-log-triage **S2** |
| 16b | `Node needed for online reconfiguration failed reconcile` (**exactly 1×**), `pdemain: Cannot open GDO source directory /etc/opt/teradata/tdconfig/Backup (errno 2)`, `expected 1 node replies` | Autoscale **expand** kills the DB; `DOWN/HARDSTOP`, never recovers; the API keeps reporting RUNNING | The compiled `vconfig.gdo` was **already missing the new `node_num`** while `vconfig_addremove.txt` listed it — `tdinfo` returned `ok: False` **6 s before** `tpareconfig` ran and the orchestration proceeded anyway. PDE then forced a recovery TPA restart (correct behaviour), but the restart died on a **missing GDO backup directory**, converting a survivable reconfig failure into a permanent hard stop. The gate `wait_for_vconfig_gdo_sync` fingerprints the **addremove TXT only** and explicitly declines to inspect the compiled GDO, so it cannot detect this class of mismatch | R3 + R5 | `grep -c "failed reconcile"` and `grep -c "Cannot open GDO source directory"` → **1 each** (event, not echo); minion log at `tpareconfig − 6 s` for `tdinfo … 'ok': False`; node-reply counts (`expected 2` on healthy runs vs `expected 1` on the fatal one); `ls /etc/opt/teradata/tdconfig/Backup`; read `_runners/vconfig_sync.py` **implementation**, not its name | **COG-15659** (open). Blind-spot amplifier ⇒ **#61** |
| 17 | `Timeout! PDE state is not running within 312 seconds`, `/etc/init.d/tpa start` | CE failed to start after scheduler start | **SLES 15 SP7 disk ordering is not static** — devices come up in a different order, TPA can't start | R5 | Salt log around `tpa start`; compare `/dev/` ordering with a good boot | REGULUS-2974 (known issue) |
| 18 | `ALTER SPOOL MAP AMPCOUNT=`, `FAILED after 1200.0s`, `scale-down` | Autoscale scale-down does nothing | Phase 1 of the Salt decommission hangs on `ALTER SPOOL MAP` and is killed at 1200 s, so nothing is decommissioned | R1 | Decommission log: `alter_attempt=n/6`, `cw_online`, `nogt_total` | OTF-9516 |
| 19 | `NEWPROC NoGT NoGT` | Warm-pool start: vprocs on other nodes take ~5 min to come online | Vprocs remain in NEWPROC until the cluster fully forms; the delay is an implementation-change side effect | R1 | `vprocmanager` state over the startup window | REGULUS-3713 (open) |
| 20 | `/var/opt/teradata/core`, `du -ah`, filesystem full | Instance filesystem fills; CE unusable, reprovision doesn't help | Accumulated **core files consumed 103 GB of 141 GB** | R4 | `du -ah --max-depth=5 /var/opt/teradata \| sort -rh \| head` | PDEF-4501 (see also OTF-6574 — large dumps fill the instance) |
| 21 | `NOSCACHE`, root filesystem fills after reboot | NOS spills to the root disk | **NOSCACHE not remounted after reboot**, so NOS writes land on `/` | R2 | `mount \| grep -i noscache` after a reboot | REGULUS-3693 |
| 61 | `scaling action completed; entering cooldown` logged **after** `DOWN/HARDSTOP`; `metrics snapshot is stale; skipping decision and resetting consecutive counters` | A failed scale operation is reported as success; a dead CE then sits unnoticed for 24 h+ | The autoscaler derives success from the **actuator / ASG result**, never from DB liveness, and treats "metrics stale because the DB is unreachable" identically to "no demand" — it resets its counters and continues silently. No alarm, no re-bringup, no rollback | R3 | Compare the autoscaler success timestamp against DBS state at that instant; `ce-autoscaler history` will show a **`success`** row whose DB died mid-operation | Blind spot in **COG-15659** *and* **GPSC-3907** — the only defect shared by two **independent** root causes, and therefore the highest-leverage fix in the batch |

---

## D3 — Metadata / MCS / BCM (Global DB, replication, lazy loading)

> The largest open cluster and the hardest domain. **Three different mechanisms produce
> `SubCode: 9710`** — never stop at the error code.

| # | Signature (grep) | Symptom | Mechanism proven | Class | Confirm | Jira / fix |
|---|---|---|---|---|---|---|
| 22a | `Failure 4529 ... (SubCode: 9710)`, MCS `State : Disconnected`, `Pending State : Active` | *All* queries fail on one CE | CE has been **disconnected from the MCS collection**; a code path introduced by IDR-60 then dereferenced a null `catalog_item_t` | R3 | MCS system dump: `System ID / State / Pending State`; `gdb` → `p (*item)` = 0x0 | IDR-121 (dup: IDR-149) |
| 22b | `Error 4501 Invalid request parcel received (SubCode: 9710)` | INDB/OTF query fails | Emitted by the **MCS/BCM router**, not DBS: it aborts when `RepMetadata` references an object DBS doesn't track in CDM (MOTF Iceberg tables, `READ_NOS` inputs) | R3 | Query references MOTF/READ_NOS; router log shows the RepMetadata abort | IDR-186 |
| 22c | `4529 / 9710` + `replication transport outage` during autoscale | Duration test fails intermittently | **Database instability**: the CE was repeatedly auto-scaling and the TPA restarting; replication transport dropped with it | R1 | Correlate 9710 timestamps with TPA restarts / autoscale events | IDR-730, IDR-703, IDR-621 |
| 23a | `Failure 6692 Transaction is aborted by the Replication transport` | All DDL blocked for hours | **SIGSEGV in the MCS sequencer corrupted its internal catalog state**, blocking DDL across every CE for 10+ hours | R3 | `sequencer_*_seq_debug.log` for the core; DBQL for the triggering SQL | IDR-164 |
| 23b | `OpDeactivateSystems`, `removeSystemsProcessor`, `Systems[systemId].lock` held ~148 s | 6+ hour outage on an *unrelated* peer CE | Admin-initiated deactivation against an **unreachable target** stalls the sequencer collection-wide (lock held far beyond the bounded dispatcher-stop work) | R1 | Sequencer debug log: lock hold duration around the admin op | IDR-221 |
| 23c | `ixn` worker threads parked; gateway not completing logons | Replication transport aborts | The sequencer establishes global/managed sessions **synchronously across all target CEs with no timeout**; one CE whose gateway stopped completing logons exhausted the worker pool | R1 | Which CE's logons hang; sequencer worker-pool saturation | IDR-685 |
| 24 | Objects (datalake/authorization/SP/macro) missing after **stop → start** | Restore incomplete | Two independent bugs: (a) corrupt catalog JSON on disk, (b) replay attempted **before required dependencies existed** on the target | R3 | Compare `help database` before/after; catalog JSON integrity; replay order in MCS log | IDR-162; family: IDR-705/706, NOS-14561, IDR-449, NOS-14584 |
| 25 | `MCSObjectLookup`, `processMCSObjectLookupRequest`, `replayItemWithDeps`, `created=N alreadyExists=N errors=0` yet DBS says object not found | Lazy load "fails" although both sides behaved correctly | **DBS waits 5 minutes for a lazy-load response**; MCS finished after that, so DBS had already timed out. A pure timeout mismatch, not a logic bug | R1 | MCS lines above + DBS timeout + DBQL for the window (often missing — file an observability gap) | NOS-14560 (open); by-design variant: IDR-722 |
| 26a | Missing access rights `CF DF PD DV CM DM DT PC CT CV` after repop | Grants lost | Router's `privilegeKeywordToAccessRight()` doesn't handle **bare/shorthand DBS privilege keywords** (DBS grammar `CREATE_or_DROP` has an empty alternative) | R3 | Diff `dbc` access rights before/after repop | IDR-300 |
| 26b | duplicate `CREATE ROLE` in `sequencer_region1_seq_debug.log` | Global-DB grants vanish on new CE | Router handled a **duplicate CREATE ROLE non-idempotently**, destroying the role's 13 existing permissions | R3 | Sequencer op timeline: GRANT succeeds at op-N, CREATE ROLE at op-M wipes it | IDR-269 |
| 26c | `No targets found for address 0x0001ffff (all sequencers)`, `catalog_load.c ... No available targets` | Grants silently not applied | **Single-sequencer window** — no secondary sequencer peer during the grant window, so every catalog modification failed | R1 | Sequencer peer count during the window | IDR-623 |
| 27 | `establishTargetSystems`, `SESSION_TYPE_DIRECT_MANAGED` | New CE doesn't receive `~GLOBAL` DDL | CDC sessions retain a **stale target-system bitmask** that excludes the newly added CE | R2 | Bitmask vs current collection membership | IDR-288 |
| 28 | `@@___DDL___@@`, `UNRECOVERABLE`, forced partial activation | Datalakes stop replicating | Fix commit `71d5eee62f` (for IDR-278) **introduced** corruption of catalog item 0 after every forced partial system activation | R3 | Catalog item 0 state after a forced activation | IDR-301 — *explicit recurrence of IDR-278* |
| 29 | Object metadata lists objects from other databases | Wrong metadata for a global DB | MCS metadata retrieval uses **pattern/`LIKE` matching instead of exact match** on database names | R3 | Query a DB whose name is a prefix of another | **IDR-328 (open)** |
| 30 | `JSON_ENSURE_ASCII`, `\uXXXX` in dictionary JSON, `Error 3807 Object does not exist` | Objects with non-ASCII names fail after restart | MCS escapes Unicode DDL to literal `\uXXXX` when writing the dictionary JSON; the escape is not reversed on load | R3 | Inspect dictionary JSON for `\u` sequences | IDR-704 |
| 31 | `catalog_load_external_files_from_json_file`, `MAX_JSON_LINE_LENGTH` 4096 | C/C++ UDFs & XSPs fail to repop with compile errors | Line-based `XFgets` parser with a **4096-byte buffer** truncates base64 source of non-trivial C files | R3 | Size of the base64 blob vs 4096 | IDR-311 |
| 32 | `src/sequencer/locks.c` ~1727, `readObjects.count != 0`, Error 4511 | Macro containing a view on FT/OTF doesn't replicate | Routing validation designed for normal DML; the `readObjects.count == 0` gate bypasses the check entirely for write-only requests | R3 | Sequencer routing decision for the macro | IDR-323 |
| 33 | `TD_GLOBAL`, `3541 perm space too small` | Replication delayed / MODIFY fails | `TD_GLOBAL` was tracked as a **per-CE** database instead of a global-collection database, so DDL never fanned out | R3 | MCS dictionary classification of TD_GLOBAL | IDR-264 (dup: IDR-265) |
| 34 | OMS status endpoint errors after an upgrade; proxy certs | Only **upgraded** sites affected | The OMS/MCS upgrade path replaces the instance **without migrating proxy certificates, TDGSS config, dictionary files** — new instance generates fresh proxy credentials | R5 | New vs old instance cert/dictionary artifacts | IDR-91 (dup: IDR-100), IDR-99 |
| 62 | `MCSObjLookupReplayComplete`, `Failed to submit resync SQL: 'GRANT … TO "<user>@<domain>"'`, `User or role '<user>' does not exist` | After a CE restart: global DBs visible but their objects not copied, local DBs missing, queries fail object-not-found, "lazy loading is enabled but nothing materialises" | **Ordering, not a replication defect.** The CE restarted while the IDP/JWT user-provisioning defect (#54 / COG-15584) was still in effect, so the creator users did not exist when MCS replayed the catalog and every `GRANT` resync failed. The manual workaround was applied **after** provisioning — by which time the MCS retries were already exhausted — and a full catalog replay had completed (`…ReplayComplete` ⇒ DBS disables lazy loading), so nothing re-materialised on demand | R1 | Compare CE `last_launched_at` against the COG-15584 hotfix deployment time, and the workaround timestamp against the MCS retry window; look for `…ReplayComplete` preceding the failing queries | **COG-15661** → parent **COG-15584**. Remedy = stop/start the cluster, **not** a code fix. Family: #24, #25 |

---

## D4 — DBS / query engine / autoscale interaction

| # | Signature (grep) | Symptom | Mechanism proven | Class | Confirm | Jira / fix |
|---|---|---|---|---|---|---|
| 35 | `9562`, `SysMapDie`, `Do_DerivedTab → ResPopTblOpMap → SysGetGrpDbcAmpList` | Internal error + dump during **first autoscale contraction** | **Stale map reference during the spool/global-map transition** | R1 | Dump backtrace path above; correlate with contraction start | **REGULUS-3676 (open)** |
| 36 | Backtrace `suterrcy/awtcat/nodegetgforctgmap/nodegetg` | Crash during CE autoscale | The spool map was marked in-use **only at dispatch time**, and the dispatcher's versioned-global-map check compared step map numbers against the *live* GDO GlobalVersion → online reconfiguration could drop the map between planning and execution | R1 | Reconfiguration timestamp between parse and dispatch | REGULUS-3580 (dup: REGULUS-3772) |
| 37 | `11513`, DB restart while retrieving contract-function response | Restart on Java OTF table operator | Resolver uses the **latest** spool-map version (`SysGetSpoolMapNo`) while building the parse tree — twice during parsing — so the two lookups can disagree | R1 | Parse-time vs execution-time map version | REGULUS-3507 |
| 38 | `7487`, `2640`, `S2S-Redist`, `GRPDBCAMPS` | Events/dumps during contraction with Iceberg reads | **AMP-index mismatch during contraction**: the `GRPDBCAMPS` bitmask can become inconsistent with `ampmap_p[].AMPOFNODE` | R1 | Bitmask vs ampmap at contraction | REGULUS-3211 |
| 39 | `SutChkSp`, left-over spool table, old transaction number | Left-over spool found after duration test | Spool created by a **session still active** from an earlier run | R2 | `date and time created` + `Old transaction number` in the SutChkSp output | OPT-12925 |
| 40 | `CheckBFBitMapMatch()`, `WTVExit`, `PLANDIE` | Crash dump on Deltalake/Unity query | Unmatched **Bloom-filter bitmap producer/consumer** in WTV plan validation | R3 | Dump backtrace; disable BF to confirm | OPT-13399 |
| 41 | DBS won't start on multinode | Bring-up failure | The SQLE-4706 change **didn't account for version global maps** (first global map has 1 clique + Perm AMPs; second has 2) | R3 | Global-map contents on the system | SQLE-4757 |

---

## D5 — Data access (OTF / NOS / object store)

| # | Signature (grep) | Symptom | Mechanism proven | Class | Confirm | Jira / fix |
|---|---|---|---|---|---|---|
| 42 | `Error 7825`, `SdkClientException: Unable to execute HTTP request: glue.us-west-2.amazonaws.com` | Deltalake/Iceberg queries fail intermittently during duration testing | **Transient AWS SDK/Glue failures with no retry/backoff** | R1/R6 | The `Caused by:` chain names the SDK client; failures cluster in time | **OTF-4606** — fix = exponential backoff. *Master of a 22-issue cluster* (OTF-4842/4854/4856/4857/4869/4870/4883/4907/4908/4927/5018/5019 …) |
| 43 | `Connection pool shut down`, `Failed to close manifest writer` | Later iterations of a DML loop fail | Same family as #42 — pool torn down after transient failures | R1 | Same | OTF-5018 / OTF-5019 (dups of #42) |
| 44 | `java.io.IOException: Too many open files` | Stress test: `Failed to create Parquet file` | File-descriptor exhaustion in the OTF writer under concurrency | R4 | `ulimit -n`, open FDs during the run | OTF-6322 |
| 45 | `roleSessionName' failed to satisfy constraint: Member must have length less than or equal to 64` | Error 6321 on assume-role queries | STS session name built from a long IDP username exceeds 64 chars | R3 | The generated `tdotf-sts-session-<user>.<...>` string length | OTF-7914 |
| 46 | `Failure 3610 Internal error: Please do not resubmit` with an email-style username | OTF queries fail for some users only | Username with special characters is injected into the rewritten table-op SELECT list **without quoting** | R3 | Does the failing user's name contain `@`/`.`? | OTF-7411 |
| 47 | `Table ... does not exist in AWS Glue`, `ObjectNotFoundException`, `rolled back from the Glue Catalog` | Drop+create of a Glue-backed Delta table fails | Delta files and Glue catalog **cannot be updated in one transaction**; the writer deliberately does not drop data files when the Glue table is missing | R3 | Glue table presence vs `_delta_log` | OTF-6326, OTF-5051, OTF-7059 |
| 48 | `AccessDeniedException: s3://.../_delta_log`, `Status Code: 403` | Read/collect-stats on Unity/Delta fails | Object-store authorization for the `_delta_log` prefix | R6 | Bucket policy / assumed role for that prefix | OTF-4332 (dup: OTF-4489); open variant OTF-9833 |
| 49 | Databricks `CREATE TABLE` with column names containing spaces | Error 7825 on `ICEBERG_EXPORT` | Column names not wrapped in **backticks** when generating Spark/Databricks SQL | R3 | Generated DDL string | OTF-9555 |
| 50 | `Failure 6881`, `MOTF_DDL_DUMMY` | Managed OTF table creation fails | Missing check for the `MOTF_DDL_DUMMY` generated for EOTF queries | R3 | Query path EOTF vs MOTF | OTF-9509 |
| 51 | Azure connector utils error on blob location | NOS query fails on Azure only | **Azure BLOB naming convention** not satisfied by the supplied location | R7/R5 | Retry with a conventional container/blob name | NOS-14513 |
| 52 | Mass object failures after a CE restart resync | 107 objects fail to restore | **S3 was inaccessible during the resync** — 95 of 107 failures were direct or cascading consequences. Not a product defect | R6 | Object-store reachability during the resync window | OTF-7967 |

---

## D6 — Auth (TDGSS / JWT / CIDS / grants)

| # | Signature (grep) | Symptom | Mechanism proven | Class | Confirm | Jira / fix |
|---|---|---|---|---|---|---|
| 53 | `Failed resolving customer r…` | CE auth breaks suddenly for everyone | **Key rotation** invalidated existing sessions | R1 | Correlate with the rotation event; all sessions must re-login with cleared cookies | TDGSS-12525 → `accp-identity-service` PR #169 |
| 54 | JWT logon fails **only after CE stop → start**, pooled | `logmech=JWT` login rejected on a restarted CE | IDP registration not re-established after restart; suspected **per-CE password rotation** using a different account | R2 | Does a fresh CE work while the restarted one fails? Check pool-manager pass-through vs image-side registration | COG-15584 |
| 55 | CIDS sends an **expired** token in token-exchange to OMS API | OMS API calls rejected | Token lifetime/refresh bug in CIDS | R1 | Decode `exp` vs request time | **TDGSS-12578 (open)** |
| 56 | `The user does not have …` for an IDP user running a valid query | Grants appear missing | **Documentation gap** — IDP users require an additional grant that was never documented | R7 | Compare with the documented grant list | REGULUS-2959 (fix = docs) |
| 57 | IDP user created with **0 perm space** | Cannot create foreign table | By design per the current provisioning model | R7 | — | REGULUS-2854 |
| 58 | `Error 8024 All virtual circuits are currently in use`, `Event 8092 … host group has reached its maximum session limit`, ≈302 sessions on `TDAAS_OBSERVABILITY1` (~600 total, ~493 of them platform accounts) | Nobody can log in to the CE — **including `dbc`** — and autoscale then wedges the cluster | **STC telemetry collector connection-pool session leak** (`tel-sqle-telemetry-collector`, module `sqle_collector`). Three defects compounding on a **15-second per-CE** schedule: (a) `is_connected()` runs a single `SELECT 1` with **no retry**, so any transient blip marks a healthy connection dead; (b) `reconnect()` swallows the `close()` exception → **LOGOFF never sent** → server-side session orphaned; (c) pool eviction does `del _connection_pool[ce_id]` **without** calling `disconnect()`. Sessions accumulate → 8092 → 8024 | R3 → R4 | `cnsterm 1` → `di ne`, then `di gtw <id>`; group sessions by **user and by client IP** (platform vs workload — the client IP names the leaking host); sample twice 10 min apart to separate a leak from an undersized ceiling; check whether the `computeengine` TDWM throttle was ever active (`throttle_active = -1` ⇒ no backpressure existed) | **TCAWORK-8295** (dup: TCAWORK-8298). Fix deferred to **VCE 3.4.1 / CE 3.0**; CE 2.0 mitigation = LMO stops STC registration (**CCP-14506**). Downstream cascade ⇒ **#60** |

---

## Secondary noise — logs that are usually NOT the cause

Verify before blaming. Each of these has hijacked at least one investigation.

- `cloud-init: Unable to locate credentials` — benign IMDS timing during early boot.
- `parted ... unrecognised disk label` — benign first-boot disk prep.
- ServiceNow `Org not found` — unrelated integration error (the real cause was a PrivateLink DNS timeout).
- YaST writing unsupported `USERADD_CMD` / `USERDEL_*` into `/etc/login.defs` — cosmetic, present in **all** SLES 15 SP7 images, not CE-specific (OSEDEV-21030).
- Metering `healthy`/`critical` flapping — publisher-side; **does not** mean the CE is down (ce-log-triage S4). Cross-check EC2/VM uptime before acting.
- `zombie dispatcher` entries in MCS — noted in IDR-259 as *probably not* the cause; logs had already wrapped.
- `ERROR - Database was unreachable while updating password for TDaaS_*` (cron, every few minutes) — a *consequence* of the DB already being down, never the cause. Hijacked the first pass on COG-15659.
- `tdinfo: Error, can't find node_num N … in vconfig GDO` repeating for hours **after** a hardstop — echo. The load-bearing occurrence is the **single** one *before* `tpareconfig` ran (#16b). Rule: causes change state, symptoms repeat — always `grep -c` before concluding.

---

## Environment gotchas that produce false defects

| Gotcha | Evidence |
|---|---|
| SSH tunnel through a jump host to reach a CE is unstable → `Error 8018 session id is illegal` | COG-13187 (closed Not a Problem after moving the workload onto the jump server) |
| Site not whitelisted with the GCS API → `504` on QueryGrid/CE config creation | GPSC-2962 |
| Endpoint whitelisting missing → connection failures | TCMC-6221, PCSS-13864 |
| WAF rules blocking CE registration (Azure) | VP-64552 |
| Manifest/BOM not uploaded to the target Scorch instance | REGULUS-3077 |
| `ctl` / `dbscontrol` debug settings **differ between pooled and dedicated** CEs | COG-15055 (open) |
| TDWM throttle applied to MCS management users (`TDaaS_tdbcmgmt1/2`) | REGULUS-3567 |
| Test DB name reused between runs → "not restored" false positives | IDR-449 (raised explicitly in-thread) |

---

## Verdict hazards — how a wrong root cause gets into this file

Every row below is a real event from the Aug-2026 UAT batch. These are the failure modes of the
*investigation*, not of the product, and each one can put a confidently-wrong row in this file.

| Hazard | What happened | Guard |
|---|---|---|
| **Duplicate closed on another engine's evidence** | CCP-14508 (Azure, **dedicated**) was closed as a duplicate of GPSC-3907 (AWS, **pooled**). Every on-box paste in that thread — `ip-10-0-41-143`, and a comment opening *"I looked at the machine … CEAM<CE_ID>"* — comes from the **AWS** engine. The shared **state-machine** defect (#60) is real; the shared **cause** was never verified on the Azure CE | Evidence must carry the ticket's own CE ID. Missing → record **`Cause not verified`**, not `Duplicate`. Note also that #60's Azure-only guard would *not* have covered CCP-14508 anyway, because it is a dedicated engine |
| **Symptom-clustering a me-too** | A second "failed starting" was posted on IDR-734 and correctly challenged: *"a CE can fail to start for multiple reasons — have you confirmed it is the same issue? What is the pooled CE id?"* | Every me-too gets a CE ID and a mechanism check before it is allowed to join a ticket |
| **Mitigation destroys the evidence** | The CE was removed from OMS config (`state: UNRECOVERABLE`) to unblock UAT; a DBS engineer asked for a live system a day later and there was none | Mitigate freely — but take the snapshot bundle **first**, and tag the instance `HOLD-FOR-EVIDENCE` |
| **P0 evidence expires on a timer** | LMO run history is Redis-only with a **24 h TTL**; the failing `stop` behind IDR-734 was already at the edge of the window when the investigation started | Capture LMO runs in the first hour, before any analysis |
| **Same weekend ≠ same cause** | Six tickets, two sites, one BOM, 72 hours — and **four unrelated root causes**. A shared-cause narrative would have shipped one fix and reopened three tickets | Merge only when A's fault makes B's symptom *unavoidable*. Opposite failure directions (reality-better-than-reported vs reality-worse-than-reported) are always different bugs |
| **Secrets pasted into the thread** | A live SSH private key and jumpbox IPs were posted in a COG-15661 comment | Never paste credentials into a ticket; if it happens, rotate + scrub and raise it separately from the defect |

---

## Maintenance

- Add a row **whenever** an investigation reaches a confirmed root cause (SOP §11).
- **Row numbers are stable identifiers — never renumber.** Append the next number even if that
  leaves a section non-contiguous (#59/#60 sit in D1, #61 in D2, #62 in D3), and use letter
  suffixes when one error string turns out to have several mechanisms (#16a/#16b, #22a–c).
- A signature that recurs after being marked fixed must be recorded as a *recurrence* with
  both Jira keys (see IDR-301 → IDR-278) — recurrences are the strongest argument for
  a regression test.
- Review quarterly: delete rows whose fix has shipped in every supported BOM.
