# Worked example — PrivateLink DNS verification timeout (control plane)

The gold-standard writeup: a control-plane failure where the root cause is clean,
the secondary issue (a ServiceNow "Org not found") is real but off the critical
path, and the conclusion carries honest caveats. Study how identifiers are named
exactly and how the secondary issue is kept visibly separate. Identifiers are
real-shaped but scrubbed.


```markdown
# RCA: PrivateLink deploy failed — private DNS verification timeout — CEAMEXAMPLE10001X

| Field | Value |
|---|---|
| CE ID | CEAMEXAMPLE10001X (example-ce-recreate-001) |
| Site ID | TDICAM00000EX01 |
| Environment | preprod |
| Region / AZ | us-west-2 / usw2-az2 |
| Account | 111122223333 |
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
One CE (`CEAMEXAMPLE10001X`) left in `NOT_PROVISIONED` with connectivity
`FAILED`. No customer-facing impact; this was a recreate/test CE.

## Timeline (UTC) — 2026-05-28
| Time | Event | What it proves |
|---|---|---|
| 07:22:39 | POST to create the private link for site `TDICAM00000EX01` / CE `CEAMEXAMPLE10001X` (initiated by `svc-tdops-01` via a TD-Ops service-account role; allowed accounts `123456789012`, `444455556666`; DEDICATED, 1X) | Provisioning request accepted; config is well-formed |
| 07:22:39 | Metadata service registered the config, granted IDP access, called the network service — and ServiceNow returned **400 "Org not found"** for org `EXAMPLE1` | A secondary CMDB-sync failure occurred here; it did **not** block the private-link flow |
| 07:22:55 | Network service found no existing slot, created a new NLB `TDICAM00000EX01-0a1b2c3d4e5f-nlb` in `vpc-0a1b2c3d4e5f67890`, `subnet-0a1b2c3d4e5f67890` | Network setup progressed normally |
| 07:25:53 | Allowed accounts updated on endpoint service `vpce-svc-0a1b2c3d4e5f6789`; private DNS enabled for `ceamexample10001x.ce.preprod.example.com` | Endpoint service configured; DNS step begins |
| 07:25:54 | TXT record created (`_a1b2c3d4e5f6g7h8i9j0` → `vpce:0abcdefghijklmnopqrs`) to prove domain ownership | The ownership-proof record was written |
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
- **ServiceNow `Org not found` (400) for org `EXAMPLE1`** at 07:22:39. This means
  the CE was not tracked in ServiceNow's CMDB — an operational blind spot and a
  metadata↔ServiceNow data-sync problem. It is **not** related to the DNS
  timeout and did not block provisioning.

## Recommended next steps
1. Verify the TXT record `_a1b2c3d4e5f6g7h8i9j0.ceamexample10001x.ce.preprod.example.com`
   actually resolves in the Route 53 hosted zone that serves
   `ce.preprod.example.com` (confirm it's the correct zone, not a
   sibling). — *Network Service owner, acct 555566667777*
2. If the record is correct and the zone is right, increase the DNS verification
   timeout in `ce_manager.go` and retry the deployment.
3. Fix the ServiceNow org mapping for `EXAMPLE1` to stop future CMDB-sync gaps.

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

**One correction to carry forward.** Step 1 above routes to that account as the
"Network Service owner" — the attribution made at the time, which the corpus has
since corrected: GNS is a separate deployment account, and the account named here
is the privatelink-monitor's intermediate. Check the ownership note in
`triage/TRIAGE.md` and the account map in KB §8.1 before routing a real ticket on
it. The example is left as written because the reasoning is what it teaches; the
routing target is what the corpus fixes.

