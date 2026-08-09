
# CE Log Triage

CE failures are hard for one structural reason: **the lifecycle is split across
services in different AWS accounts, and the failure trail crosses those
boundaries.** A symptom seen by one service is almost always *caused* by a
different service or layer upstream. This skill encodes the architecture, both
state machines, both log formats, a signature playbook (S1–S7) recovered from
real investigations, and **tested bundled scripts** — so a triage goes straight
to root cause instead of rediscovering the terrain (or re-writing the same
CloudWatch parser inline) every time.

**Triage is read-only.** Nothing in this module modifies logs, AWS resources, or
CE state; all "rollback" in the workflows means recovering the *investigation*
from a wrong turn, never mutating the system under study.

## Precedence — read before using this module

Three rules keep this playbook aligned with the corpus in `references/`:

1. **Dedup against the canonical registry, not just S1–S7.** The playbook below
   carries 7 deep-dive signatures; `references/CE-SIGNATURE-REGISTRY.md` carries
   60+ and is where new rows land. At W5, grep the registry for the error code /
   distinctive phrase **in the same pass** as the S1–S7 index. The two registry
   contacts are different passes: symptom-level dedup (pipeline step S2) happens
   *before* any evidence work; W5's pass is the confirmation-level match once a
   divergence point exists. If step S2 was skipped, do it now. Cross-map:

   | Triage | Registry | Note |
   |---|---|---|
   | S1 | **#1** | Same strings (`AWS-AssignSlot-Error`, `WAITING_DNS`); CCP-11948 / COPS-25103 |
   | S2 | **#16a** | Same family as registry #16a; run #16b's confirm too — near-identical logs, different mechanism |
   | S3 | — (mechanism family of #16a) | No dedicated row |
   | S4 | *Secondary noise* entry + the **#60** / GPSC-3907 family | Publisher-side flap ≠ CE down |
   | S5, S6, S7 | **no registry row yet** | Confirmed causes missing from the canonical registry — file them as new rows (#63+) per the registry's own maintenance rule |

2. **New signatures go to the registry first** (`rca/RCA-WRITEUP.md`, Artifact 3,
   append-only numbering); add a deep-dive S-entry here only when a signature
   earns a full symptom→confirm→fix procedure.

3. **Corpus wins on facts; this module wins on procedure.** Where this module's
   architecture, account, or state-machine claims conflict with `references/`,
   the corpus is newer and verified — a proven example: this module used to name
   `<PRIVATELINK_MONITOR_ACCT>` as the Network Service owner, but the corpus shows GNS is
   `<GNS_DEPLOY_ACCT>` and `<PRIVATELINK_MONITOR_ACCT>` is the privatelink-monitor's intermediate
   AssumeRole hop (KB §8.1; Provisioning §16 #14). That correction has now been
   applied to `REFERENCE.md` A.2 and its box diagram; the example stands as a
   reminder of the precedence rule, not as an outstanding defect. The module's
   *workflows, checkpointing protocol, and bundled scripts* remain canonical for
   log handling regardless.

## When to use this module

Use it for any of these (triggering into the skill is handled by SKILL.md):

- "why is `<CE_ID>` failing / stuck / not starting / not provisioning"
- "analyze these CE logs" / "what's the root cause" / "triage this engine"
- A pasted CE ID (`CEAM…`) or site ID (`TDICAM…`) plus any state word:
  `provisioning_failed`, `down/hardstop`, `down/tdmaint`, `stopped`,
  `NOT_PROVISIONED`, `NETWORK_PROVISIONING`, `MANIFEST_CREATING`,
  `configuring` / `expand vconfig`
- A `logs-insights-results__NN_.json`, `messages`, or Salt `minion` file
  appears in the task, uploads, or working directory
- Pooling-service symptoms: `/infrastructure` timeout, `INVALID_ARGUMENT`
  400 on an instances PUT, "instances list must not be empty" (signature S7)
- Metering health flapping `healthy`/`critical` for a `ce_siteid` (S4)

## When NOT to use this module

These merely share vocabulary; handle them from the knowledge corpus or decline:

- Writing or reviewing **application code** for GCS/Network/Scheduler services
  (Go/Python development tasks) — unless the task is diagnosing a live failure
- Generic CloudWatch/AWS questions with no CE, site, or CE log file involved
- `resume-gateway` proxy work, NLB/PrivateLink **design** discussions, or
  cost/quota questions — architecture work, not failure triage
- Kubernetes/EKS or RAG-pipeline debugging (different systems entirely)
- Producing the final RCA **document** — that is the RCA module (`rca/RCA-WRITEUP.md`);
  invoke it *after* this module has produced a conclusion

## The two layers — classify first

| | **Control plane** | **On-host / node layer** |
|---|---|---|
| Does | Provisions/tracks the CE: authorize → config → record → network/PrivateLink → manifest → ECS | Brings up the Teradata DB: PDE, TPA, vconfig, BYNET, Salt |
| Runs in | GCS, Metadata, Network, Scheduler, Auto-Suspend, Metering (Lambdas/ECS/SQS, several accounts) | Salt master + minions on the EC2 nodes |
| Evidence | CloudWatch Logs Insights JSON (`logs-insights-results__NN_.json`) | Linux `messages` / Salt `minion` syslog |
| Stuck states | `NETWORK_PROVISIONING`, `MANIFEST_CREATING`, `provisioning_failed`, `NOT_PROVISIONED` | `down/hardstop`, `down/tdmaint`, `stopped`, `configuring / expand vconfig` |
| Read via | `scripts/parse_cloudwatch.py` (no jq, ~40 MB files) | `scripts/triage_syslog.sh` (40k+ lines, viewers truncate) |

One incident can span both layers; reconcile them on a shared UTC timeline
(the on-host crash timestamp should line up with where the control-plane
status stopped advancing).

## The triage workflow (8 steps)

Detailed procedures with preconditions, expected outputs, recovery, and
validation live in `triage/WORKFLOWS.md` (W1–W6). The spine:

1. **Pin the basics** — CE ID + site ID, exact reported state, environment
   (account IDs differ per env), UTC time window. Record them in the
   checkpoint file (see below). If the node is reachable:
   `curl -s http://localhost:22222/provisioning-status`.
2. **Inventory files before loading** — `wc -l`, `head -5`, `tail -5` only.
   Never `cat` a 40 MB JSON or 40k-line syslog into context. → W1
3. **Build a UTC timeline** — control plane via
   `parse_cloudwatch.py --ce <CE> --site <SITE> --timeline`; on-host via
   `triage_syslog.sh` sweep → window. Each entry records what it *proves*. → W2/W3
4. **Map to the state machine** (`triage/REFERENCE.md` §B) — find the
   transition that did not fire; the missing transition names the owning
   service and account.
5. **Match failure signatures** (`triage/TROUBLESHOOTING.md`, S1–S7) —
   then *run the signature's confirmation step*; two signatures can present
   identically (a stuck `expand vconfig` (S5) is usually caused by S2). → W5
6. **Separate root cause from secondary noise** — known red herrings are
   catalogued in TROUBLESHOOTING.md; state explicitly "X failed (root cause);
   Y and Z logged errors but were not on the critical path."
7. **Assign ownership** — name the account and service that owns the missing
   transition; order next steps by authority of evidence (CloudTrail /
   authoritative log → role/ARN resolution → ask the owning team).
8. **Hand off to the RCA module (`rca/RCA-WRITEUP.md`)** — verdict, UTC timeline, root cause with
   deciding evidence, secondary issues, next steps, caveats. → W6

## Bundled scripts — quick reference

Both are stdlib/offline by design (the box has no `jq`, no pip, no network)
and are **read-only** with respect to the input files.

| Command | Purpose |
|---|---|
| `python3 scripts/parse_cloudwatch.py F.json` | Default `--stats`: counts, UTC span, level histogram, top `ce_siteid`s |
| `… --ce CEAM… --site TDICAM… --timeline` | Chronological per-engine event lines (dual-filter matches EITHER id) |
| `… --ce CEAM… --errors` | ERROR/FATAL levels + error-pattern text matches |
| `… --grep 'PATTERN'` | Case-insensitive regex over raw record text (e.g. `'INVALID_ARGUMENT\|/infrastructure'`) |
| `… --metering` | Decode SQS→SNS→Message envelope; flag flaps + redelivery (S4) |
| `… --show N` | Full JSON of record `[N]` from the current filtered listing |
| `… --no-skip` | Debug only: disable bulk `List*` skipping |
| `bash scripts/triage_syslog.sh messages` | Anchor sweep of a syslog, grouped with counts + line numbers |
| `… messages --ip 10.0.2.201` | Sweep filtered to one node |
| `… messages --window 18342 [--ctx 80]` | `sed` window around a transition line |

Parser guarantees (do not re-implement inline): handles `@message` as dict OR
string via a single `text_of()` normalization point; skips bulk `List*`
inventory entries *before* ID filtering (they mention every CE — false-positive
factory); re-sorts to chronological order (exports arrive
reverse-chronological); dual-filters `--ce`/`--site` against the raw serialized
record so IDs buried in ARNs or free text still match. If the parser seems to
miss records, see "Script troubleshooting" in `triage/TROUBLESHOOTING.md`
before patching it.

## Checkpointing for long sessions (summary)

Full protocol in `triage/WORKFLOWS.md` §"State and checkpointing". Rules:

- Maintain `./.triage/<CE_ID>/STATE.md` from Step 1 onward; update it at every
  step boundary (it is the resume point after context compaction).
- Every load-bearing claim cites evidence: `file:line` for syslog, `[index] +
  UTC timestamp` for parser output.
- Append verification entries to `./.triage/<CE_ID>/VERIFY.log` in the fixed
  `UTC | step | command | expected | observed | PASS/FAIL` format.
- On resume: read STATE.md first, re-verify file identities (line counts /
  hashes) before trusting cached conclusions, and continue from the recorded
  step — do not restart from scratch.

## Progressive loading map

| Read | When |
|---|---|
| `triage/WORKFLOWS.md` | Executing any step in detail; starting/resuming a session; writing checkpoints |
| `triage/REFERENCE.md` | Mapping a timeline to state machines; identifying owning account/service; decoding log/envelope formats |
| `triage/TROUBLESHOOTING.md` | Step 5 signature matching; suspected red herrings; parser/sweep misbehavior |
| `scripts/*` | Execute directly; read source only if debugging them |

## Caveats and honesty rules

- **Account IDs are environment-specific** — the topology table is a starting
  map; confirm against ARNs in the actual logs.
- **Logs prove sequence, not always intent** — when logs can't settle a
  question, say so and name what to check next rather than guessing.
- **A stuck state is usually a symptom** — chase the crash, not the marker.
- **Flag load-bearing assumptions** and verify them directly.
- **Separate "what failed" from "what merely logged an error"** in every
  writeup — this is what makes the analysis trustworthy.
