# Triage findings — CEAMEXAMPLE10001X (2026-05-28)

A **concluded** investigation, in the handoff shape the RCA module consumes. The
investigation itself is done; what remains is the writeup.

## Identity

- CE config ID: `CEAMEXAMPLE10001X`
- Site ID: `TDICAM00000EX01`
- Environment: preprod · Region/AZ: us-west-2 / usw2-az2
- Reported state: `NOT_PROVISIONED`, connectivity `FAILED`; last transition
  `PRIVATE_LINK_DEPLOY: FAILED`

## Verdict (one line)

AWS never completed private DNS domain-ownership verification for the endpoint service
within the ~10-minute window, so slot assignment failed.

**Class:** R6 infra/network (timeout boundary; R1 timing not excluded)

## UTC timeline

| Time (UTC) | Source | Verbatim | Proves |
|---|---|---|---|
| 07:22:39 | CloudWatch | `PRIVATE_LINK_DEPLOY status=started` | Request accepted, config well-formed |
| 07:22:39 | CloudWatch | `servicenow status=failed error_code=400 "Org not found" org=EXAMPLE1` | CMDB-sync failure; did NOT block the private-link flow |
| 07:25:54 | CloudWatch | `TXT record created to prove domain ownership` | Ownership-proof record was written |
| 07:26:00 | CloudWatch | `waiting for AWS private DNS verification` | AWS-side check started |
| 07:36:03 | CloudWatch | `slot_assignment status=failed error_code=AWS-AssignSlot-Error location=ce_manager.go:122 "private DNS verification timed out after ~10 minutes"` | **The failure point** |
| 07:36:04 | CloudWatch | `metadata status=patched "PRIVATE_LINK_DEPLOY FAILED"` | Failure recorded downstream |
| 07:48:20 | CloudWatch | `poll status=NOT_PROVISIONED connectivity=FAILED` | Settled into the terminal failed state; nothing retried |

## Deciding evidence

The `slot_assignment` error at 07:36:03 — `AWS-AssignSlot-Error`, raised from
`ce_manager.go:122` — immediately after the 07:26:00 `waiting` entry, with no
intervening network-side state change.

## Secondary noise (logged, off the critical path)

- ServiceNow `Org not found` (400) for org `EXAMPLE1` at 07:22:39. Real CMDB gap, but
  it did not block provisioning.

## Attempt ratio

1 of 1 on this CE (a recreate/test engine). No customer impact.

## Caveats / open questions

- Why AWS never verified the TXT record — propagation delay, wrong hosted zone, or an
  over-aggressive timeout — is `[inferred]`, not distinguishable from these logs.
- Owner of the endpoint-service account is unconfirmed here; verify before routing.

## Observability gap

No log line records the DNS verification *poll results* during the 10-minute wait —
only the start and the timeout. That single addition would make this diagnosis
immediate.
