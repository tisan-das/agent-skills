# Worked example — PDE crash to DOWN/HARDSTOP (on-host)

The on-host layer, where the timeline is the star: the failure is a precise chain
of events on the node and the root cause is a reconcile failure during an
expansion. Note how the stuck `expand vconfig / configuring` marker is framed as
a *downstream symptom*, not the cause - the trap that domain-D2 incidents set.


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

