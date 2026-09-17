# CAN-X — Safety Architecture

> **Document**: `docs/architecture/SAFETY_ARCHITECTURE.md`
> **Applies To**: every capability that can change a vehicle, and every caller that might ask for one
> **Status**: implemented, remediated after the first independent review returned NOT PASS, and awaiting independent re-acceptance (SAFETY-01-FIX-1; see §24)
> **Authority**: the invariants in §5 are frozen. Later tasks may add to them; none may be removed or weakened.

This document is the authority on what CAN-X's safety boundary *is*. The code in
`runtime/canx/safety/` is its implementation, and `AGENTS.md` §17/§21/§22 carry
the rule that any task touching dangerous-operation work must read this document
first.

Read §2 for the one rule, §5 for the invariants, and §20 for what is explicitly
**not** verified.

---

## 1. Why this stage exists

CAN-X is becoming capable of more than capture. The trajectory is:

```text
CAN / CAN FD capture · Trace · DBC · Plot · Analysis        (exists)
CAN TX · Periodic TX · Replay · Frame injection             (planned)
Diagnostics · UDS · Read DTC · Clear DTC · Session control
ECU Reset · Routine control · IO control                    (planned)
Automation · Python script · Agent-driven workflows          (planned)
ECU-mutating operations                                      (future)
```

The safety question for that trajectory is not "should the UI show a
confirmation dialog". It is:

> **Can a caller gain a dangerous vehicle capability by not going through the
> runtime's safety authority?**

"Add a confirm button" answers that question for exactly one caller — a human
clicking one screen. It answers nothing about an Agent that calls a tool, a
script that imports a module, an automation rule that fires at 3 a.m., or a
diagnostic workflow five layers down. SAFETY-01 builds the authority those
callers must all pass through, *before* any of the capabilities exist.

This is deliberate ordering. A safety boundary designed after the capabilities
it governs is a boundary shaped around whatever was already built.

---

## 2. The one rule

> **No caller may gain dangerous vehicle capability by bypassing the Runtime
> Safety Kernel.**

Everything in this document follows from that sentence.

```text
UI ───────────────┐
Agent ────────────┤
Automation ───────┤
Script ───────────┤
Protocol Workflow ┤
                  ↓
             Safety Kernel          ← the policy authority
                  ↓
          controlled execution      ← not built in this stage
                  ↓
              Adapter
```

---

## 3. Safety goals

```text
G1  Dangerous capability is opt-in, bounded and temporary — never ambient.
G2  Every dangerous capability has exactly one entry point, and it decides.
G3  A refusal is a normal, diagnosable outcome; a fault fails closed.
G4  Every decision is attributable after the fact.
G5  Authority cannot be widened by the party that benefits from widening it.
G6  No safety-critical error is silently swallowed.
G7  The boundary is testable without a vehicle, and without a device.
```

G7 is a design constraint, not an aspiration: a safety boundary that can only be
tested on hardware will be tested rarely, and a boundary tested rarely is a
boundary nobody trusts.

---

## 4. Threat / misuse model

The model is about **misuse by a trusted-inside-the-process caller**, not about
a hostile network. CAN-X runs as a local engineering tool; the interesting
adversary is a component of CAN-X itself.

```text
T1  An Agent decides a dangerous operation is the right next step, and is wrong.
T2  An Agent manufactures its own authorisation ("I approve this").
T3  An Agent is compromised or manipulated by crafted input (a task prompt, a
    DBC comment, a captured frame) into a dangerous request.
T4  A generated Python script reaches a device handle the sandbox should not
    have given it.
T5  An automation rule re-fires a previously-approved action indefinitely.
T6  A stale approval — issued for one target, one capability, one moment —
    is presented for a different one.
T7  A UI bug constructs a request that skips the runtime entirely.
T8  A future transmit path is added for convenience and never crosses the kernel.
T9  An internal fault (bad clock, corrupt state, unhandled exception) is read as
    "no objection" rather than "no answer".
T10 An emergency stop is engaged but one subsystem keeps working.
T11 An audit trail records the operation's parameters, and with them a
    security-access key or token.
```

Each threat maps onto an invariant in §5. T1 is not preventable — an Agent may
always *ask*. What is preventable is that asking is sufficient.

---

## 5. Safety invariants (frozen)

These are frozen. They may be added to. They may not be weakened, and no task
prompt, agent instruction or convenience argument may bypass one.

```text
S1   Dangerous operations default to DENY.
S2   The Runtime Safety Kernel is the policy authority.
S3   The UI cannot grant itself vehicle authority.
S4   An Agent cannot approve itself.
S5   A script cannot receive raw vehicle handles.
S6   Dangerous permission must be least-privilege and scoped.
S7   ARM is scoped and temporary, not a global persistent boolean.
S8   Restart returns to DISARMED.
S9   Unknown / malformed safety state fails closed.
S10  Safety decisions are auditable.
S11  Approval cannot increase permission.
S12  Future Adapter.send paths must not bypass the Safety Kernel.
S13  Emergency stop has authority over every dangerous operation family.
S14  Safety-critical errors cannot be silently swallowed.
S15  Any operation that transmits to a live vehicle requires explicit CAN_TX
     authority regardless of its semantic effect risk.
S16  Approval provenance is derived from trusted caller identity, and cannot be
     self-declared.
S17  Audit failure may remove authority, but must never create, retain or
     restore unaudited authority.
S18  A permission grant that opens any dangerous capability must carry a finite
     expiry.
S19  No arbitrary caller-controlled text may be persisted in a safety audit
     event. A reason is recorded as a digest and a bounded label, never as text.
```

S1–S14 were frozen by SAFETY-01. S15–S19 were added by SAFETY-01-FIX-1 after the
first independent review returned `NOT PASS`, and they are the five things that
review found the model could not yet hold (§24).

### How each invariant is held

| # | Held by | Where |
| --- | --- | --- |
| S1 | `RiskLevel.is_dangerous` and `OperationPolicy.requires_arm` gate the chain; the only `ALLOW` is the last statement | `safety/risk.py`, `safety/policy.py` |
| S2 | `SafetyKernel.evaluate` is the only decision surface in the runtime | `safety/kernel.py` |
| S3 | `PermissionSet` has no mutator; `arm`/`confirm_arm` require an authority-bearing caller | `safety/permission.py`, `safety/kernel.py` |
| S4 | `SafetyKernel.grant_approval` refuses a machine caller; `ApprovalIssuer` has no machine member | `safety/approval.py`, `safety/kernel.py` |
| S5 | Documented boundary + `CanAdapter` has no transmit primitive + the sandbox contract in `SPEC.md` §26 | §15 below, `tests/unit/safety/test_device_transmit_boundary.py` |
| S6 | Capability-based grants with targets; empty set by default | `safety/permission.py` |
| S7 | Three-state machine, `ArmScope` with expiry, idempotent disarm | `safety/arm.py`, `safety/scope.py` |
| S8 | No persistence of arm state or approvals; constructors start `DISARMED`/empty | `safety/arm.py`, `safety/approval.py` |
| S9 | `has_lapsed` is NaN-safe; the policy's outermost handler denies; constructors validate | `safety/scope.py`, `safety/policy.py` |
| S10 | Every verdict and every authority-moving action is written to a sink | `safety/kernel.py`, `safety/audit.py` |
| S11 | Single-capability approvals, exact-target matching, atomic consumption | `safety/approval.py`, `safety/policy.py` |
| S12 | A boundary test that fails if a transmit primitive appears in the device contracts | `tests/unit/safety/test_device_transmit_boundary.py` |
| S13 | `EmergencyStopController` disarms, refuses new dangerous work and requests cancellations | `safety/emergency.py` |
| S14 | A broken audit sink raises instead of returning a verdict; cancellation failures are reported | `safety/kernel.py`, `safety/emergency.py` |
| S15 | `required_capabilities` includes `CAN_TX` on every transmitting operation; the chain checks all of them | `safety/risk.py`, `safety/policy.py` |
| S16 | `ApprovalSpec` has no issuer field; `issuer_for` derives it from the caller; the store checks the match | `safety/approval.py`, `safety/kernel.py` |
| S17 | `_record_or_rollback` commits then rolls back a failed audit to a *reducing* action | `safety/kernel.py` |
| S18 | `PermissionGrant.__post_init__` refuses a dangerous grant without a finite expiry | `safety/permission.py` |
| S19 | `reason_digest` replaces raw reasons; `CallerIdentity` bounds its name | `safety/audit.py`, `safety/caller.py`, `safety/emergency.py` |

---

## 6. Risk taxonomy — effect risk and execution authority

Two independent axes, and keeping them independent is the correction
SAFETY-01-FIX-1 made (invariant S15).

**Effect risk** describes what an operation does to the vehicle, never which
protocol it speaks.

```text
READ                  observe existing data
COMPUTE               derive from existing data
WRITE_PROJECT         change the project's own storage
TX                    put frames on a live bus
DIAGNOSTIC_MUTATION   change stored diagnostic state
ACTUATION             drive a physical output
ECU_MUTATION          change ECU configuration or firmware state
CRITICAL              an operation whose failure mode is not bounded
```

**Required capabilities** describe what authority executing it costs. It is a
set, and every operation that frames a live bus has `CAN_TX` in it:

```text
operation class          effect risk           required capabilities
engineering.read         READ                  READ
engineering.compute      COMPUTE               COMPUTE
project.write            WRITE_PROJECT         WRITE_PROJECT
bus.transmit             TX                    CAN_TX
bus.replay               TX                    CAN_TX
bus.injection            TX                    CAN_TX
diagnostic.read          READ                  READ · CAN_TX      ← the split
diagnostic.mutation      DIAGNOSTIC_MUTATION   CAN_TX · DIAGNOSTIC_MUTATION
actuation                ACTUATION             CAN_TX · ACTUATION
ecu.mutation             ECU_MUTATION          CAN_TX · ECU_MUTATION
critical                 CRITICAL              CAN_TX · CRITICAL_OPERATION
```

**Why the two axes exist.** `diagnostic.read` observes — its effect risk is
`READ`, and that must stay true, because it is what decides whether an approval
is needed. But it still puts frames on the vehicle's bus. Under a one-axis model
(``operation → risk → exactly one capability``) it landed on ``Capability.READ``
alone, and a future would have been free to let it ride the low-risk automatic
path — past the ARM state, the `CAN_TX` grant and the audit trail that every
other real transmission has to cross.

Relabelling it `TX` would have been the other wrong answer: it would throw away a
read-only effect risk that is real. Both answers are kept, and both are checked.

**The two predicates, and what each decides:**

```text
effect_risk.is_dangerous     →  is an approval required?
requires_arm                 →  is the ARM state required?
                                (dangerous effect  OR  transmits to the vehicle)
required_capabilities        →  may the session execute it at all?
                                (every capability, checked individually)
```

The `.is_dangerous` boundary: the first three levels are `False`, the last five
are `True`.

**What an approval is about.** An approval names the operation's **effect
capability** — what will be done — not its full authority. An operator approves
"read this diagnostic" or "change this ECU state"; the standing `CAN_TX` grant is
what makes transmitting possible at all. Both are checked before anything runs.

**ARM covers the vehicle capabilities, not the whole set.** Requiring an arm scope
to also list `READ` would turn "may we touch the bus?" into "may we read data?",
which is the standing permission's question, answered separately. A diagnostic
read therefore needs `CAN_TX` inside the arm scope — that is the vehicle
capability it uses — and `READ` in the permission set, because that is who may
read.

**On protocol-keyed risk.** `DIAGNOSTIC_READ` and `DIAGNOSTIC_MUTATION` exist as
*separate operation classes that land on opposite sides of the boundary* —
reading a fault code is `READ`, clearing it is `DIAGNOSTIC_MUTATION`. A taxonomy
that asked "is this UDS?" would have to answer the same way for both, and would
be wrong for one of them. The taxonomy keys on consequence, so adding a protocol
later adds rows to a table rather than redefining the boundary.

**On operation classes vs. SIDs.** No service identifier appears anywhere in the
safety domain. `diagnostic.mutation` means "this changes stored diagnostic
state"; which service implements that is the protocol layer's business and is
deliberately not frozen here (SAFETY-01 §14).

Classification is a pure function over a closed table
(`classify_operation`). An unknown operation class raises
`SafetyUnknownOperationError`, which the policy turns into
`DENY / safety.unknown_operation`. It never falls through to a default.

### 6.1 Coverage of PRD §7.2

PRD §7.2 names ten operation families that require approval by default. None of
them is implemented in this stage. What follows is the mapping the taxonomy has
to support, written down so that the coverage is a design fact rather than an
assumption someone makes later.

| PRD §7.2 | operation class | risk level | approval demanded |
| --- | --- | --- | --- |
| CAN TX | `bus.transmit` | `TX` | required, exact target |
| Frame injection | `bus.injection` | `TX` | required, exact target |
| UDS write | `diagnostic.mutation` | `DIAGNOSTIC_MUTATION` | required, single-use |
| ECU reset | `ecu.mutation` | `ECU_MUTATION` | required, single-use, human issuer |
| Routine control (dangerous) | `actuation` / `ecu.mutation` | `ACTUATION` / `ECU_MUTATION` | required, single-use |
| Security access | `ecu.mutation` / `critical` | `ECU_MUTATION` / `CRITICAL` | required, single-use, human issuer |
| Flash | `ecu.mutation` | `ECU_MUTATION` | required, single-use, human issuer |
| Fuzzing | `bus.injection` | `TX` | required, exact target |
| Gateway TX | `bus.transmit` | `TX` | required, exact target |
| Modify ECU state | `ecu.mutation` | `ECU_MUTATION` | required, single-use, human issuer |

Every row lands on a level whose `ApprovalRequirement.required` is `True`, and
"PRD §7.2 lists it" is not what decides that — the effect the operation has on
the vehicle is. Where a family could plausibly sit on two classes, the mapping
above takes the more consequential one: an ECU reset is an `ECU_MUTATION`, not a
`DIAGNOSTIC_MUTATION`, because the stronger approval demand is the honest reading
of what it does.

The taxonomy therefore already carries the shape PRD §7.2 demands. A later task
adds the capability; it does not add the boundary.

---

## 7. Operation model

`OperationRequest` is a pure domain value:

```text
operation_id        stable identifier for correlation
operation_class     what it does (RiskLevel is derived, never supplied)
caller              CallerIdentity
target              OperationTarget — device / channel / target address
requested_at        the instant the caller asked
approval_id         optional reference to an approval to spend
parameters_digest   sha256 of the parameters, never the parameters
```

Alongside it sits the **operation policy** — the table row that answers both
questions for an operation class:

```text
OperationPolicy
  operation_class          diagnostic.read
  effect_risk              READ
  required_capabilities    {READ, CAN_TX}
  transmits_to_vehicle     True      (derived from the capability set)
  requires_arm             True      (derived: dangerous effect OR transmits)
```

The table is closed, total over `OperationClass`, and validates itself at import:
a policy whose effect capability is missing from its own required set, or a
dangerous effect that does not claim `CAN_TX`, raises rather than being loaded. A
table row that contradicts itself is a row nobody can reason about later.

Three deliberate exclusions:

* **no adapter, bus, protocol or UI object** — the decision must be testable
  without a device, and the domain must not acquire vendor coupling;
* **no `risk_level` field** — risk is *derived* from the operation class. A caller
  that could declare its own risk level could declare `READ` and transmit;
* **no raw parameters** — see §12 (audit) for why the digest is the whole point.

`requested_at` is supplied rather than read from a clock inside the kernel, so a
decision is reproducible from its inputs.

---

## 8. Caller model

```text
HUMAN_UI      a person at the desktop
AGENT         the Agent runtime
SCRIPT        agent-generated or user Python
AUTOMATION    rules and scheduled workflows
SYSTEM        the host runtime itself
```

Every caller is an **operation requester**. Only the first and last may *supply*
authority:

```text
                        may request  may control ARM  may issue approval  may release e-stop
HUMAN_UI                    yes           yes                yes                 yes
SYSTEM                      yes           yes                yes                 yes
AGENT                       yes           no                 no                  no
SCRIPT                      yes           no                 no                  no
AUTOMATION                  yes           no                 no                  no
```

An unknown caller kind cannot be constructed at all: `CallerIdentity.__post_init__`
refuses a kind outside the vocabulary.

This table is the answer to "can an Agent self-arm / self-approve?" — no, and not
because a rule remembers to check. There is no code path that would accept it.

---

## 9. ARM model

```text
             request()              confirm()
DISARMED  ───────────────▶ ARMING  ───────────▶ ARMED
    ▲                         │                    │
    └───────── disarm() ──────┴────────────────────┘
```

* The runtime starts `DISARMED`.
* `DISARMED → ARMED` **does not exist**. Arming is two deliberate steps.
* In `ARMING`, dangerous operations are refused exactly as in `DISARMED`.
* `ARMED → ARMING` does not exist: re-arming requires disarming first, so a live
  authority cannot be silently re-targeted.
* `disarm()` is idempotent from every state. Disarming is the safe direction, and
  an emergency stop calls it unconditionally.
* Every transition outside the diagram raises `SafetyStateError`
  (`safety.invalid_transition`) rather than being dropped. A dropped arm attempt
  would leave a caller believing the runtime was armed when it was not.

### Mandatory auto-disarm semantics

| Event | Required behaviour | Status |
| --- | --- | --- |
| Runtime startup | `DISARMED` | **implemented** (constructor) |
| Runtime restart | `DISARMED` | **implemented** (nothing is persisted) |
| Critical safety error | `DISARMED` | **implemented** (emergency stop disarms) |
| Emergency stop engaged | `DISARMED` | **implemented** |
| Scope expiration | effective disarm | **implemented** (`active_scope()` returns `None`) |
| Approval expiration | refusal | **implemented** (policy) |
| Permission invalidation | `DISARMED` | **contract only** — see below |
| Device reconnect | `DISARMED` | **contract only** (no device lifecycle yet) |
| Channel change | `DISARMED` | **contract only** |
| Project / vehicle change | `DISARMED` | **contract only** |
| Transport fault | `DISARMED` | **contract only** (no transport) |

"Contract only" is a named obligation, not a claim. CAN-X has no device
lifecycle, no channel binding and no transport layer yet, so there is nothing to
observe and nothing to test. Each becomes testable when the subsystem it belongs
to arrives, and each is listed in §20.

---

## 10. Scope model

Every authority carries an `OperationTarget`:

```text
device_id        adapter identity           (future — device manager does not exist yet)
channel          bus channel
target_address   CAN identifier
```

and comparison is deliberately asymmetric:

```text
grant: channel="can1"           request: channel="can1"    → covered
grant: channel="can1"           request: channel=None      → NOT covered
grant: channel unstated          request: channel="can1"    → covered
grant: channel="can1"           request: channel="can2"    → NOT covered
```

Row two is the one that matters. A request that does not say *where* cannot be
proven to be where the grant points, and "cannot be proven" is a refusal.

`ArmScope` adds capabilities and expiry:

```text
Device:      PCAN-USB Pro            (device_id, future)
Channel:     CAN1
Target:      0x7E0
Capability:  CAN_TX
Expires:     <instant>
```

A scope with no capabilities cannot be constructed: an arm that opens nothing is
a state whose only effect is to be misread by whoever looks at it next.

---

## 11. Permission and approval model

### Permission — standing authority, bounded where it matters

Capability-based, least-privilege, scoped, and **empty by default**:

```text
Capability: READ · COMPUTE · WRITE_PROJECT · CAN_TX
            DIAGNOSTIC_MUTATION · ACTUATION · ECU_MUTATION · CRITICAL_OPERATION
```

Every capability is reachable from some operation in the table. A capability no
operation requires would be a grant an operator could hand out that changes
nothing — a control that looks real and is not.

**Dangerous grants expire.** A grant that opens any of `CAN_TX`,
`DIAGNOSTIC_MUTATION`, `ACTUATION`, `ECU_MUTATION` or `CRITICAL_OPERATION` must
carry a finite expiry, and the check lives in the grant itself rather than in
policy (invariant S18):

```text
PermissionGrant({READ})                          valid, unbounded
PermissionGrant({CAN_TX}, expires_at=None)       refused
PermissionGrant({CAN_TX}, expires_at=inf)        refused
PermissionGrant({READ, CAN_TX}, expires_at=None) refused — dangerous as a whole
```

Enforced at construction because a rule that lives only in its consumer is a rule
the next consumer does not have: a grant that cannot be built cannot be handed to
a policy that forgot to ask. A caller that wants an unbounded read grant and a
temporary transmit grant holds two grants, which is also the more honest
description of what it has.

There is no separate `DIAGNOSTIC_READ` capability, and that remains true under the
two-axis model — reading diagnostic data needs `READ` *and* `CAN_TX`, not a
purpose-built token. The authority to read is the same authority; what
SAFETY-01-FIX-1 changed is that the transmit it costs is now required as well.

`PermissionSet` has no mutator. A caller cannot widen its own authority; widening
is a host action that constructs a new set.

### Approval — spent, not consulted

```text
approval_id · capability (exactly one) · target
issued_at · expires_at · issuer · single_use
```

**Provenance is derived, never declared.** The issuer is not a field a caller
fills in — the kernel decides it from the identity that is granting:

```text
CallerKind.HUMAN_UI  →  ApprovalIssuer.HUMAN_OPERATOR
CallerKind.SYSTEM    →  ApprovalIssuer.HOST_SYSTEM
```

A caller requests an approval with an `ApprovalSpec`, which has **no issuer field
at all**. The absence of the field is the control (invariant S16): a parameter
that does not exist cannot be forged, which is a stronger guarantee than a
validator that is supposed to catch a forged one.

Before SAFETY-01-FIX-1 the store checked only that the grantor *may* issue
approvals. The host may — so a host-supplied approval labelled `HUMAN_OPERATOR`
was stored as a human one and went on to satisfy `human_issuer_required`. The
label was doing the work of a credential. Now `ApprovalStore.grant` independently
refuses a label that does not match its grantor, and the kernel refuses anything
that is not an `ApprovalSpec`.

Default model:

| Risk | Approval required | Single-use | Exact target | Human issuer |
| --- | --- | --- | --- | --- |
| READ / COMPUTE / WRITE_PROJECT | no | — | — | — |
| TX | yes | no | yes | no |
| DIAGNOSTIC_MUTATION | yes | yes | yes | no |
| ACTUATION | yes | yes | yes | yes |
| ECU_MUTATION | yes | yes | yes | yes |
| CRITICAL | yes | yes | yes | yes |

* `TX` may reuse an approval until it expires, because a periodic transmit is one
  authorised intent that lasts; its reach is bounded by the ARM scope's own
  expiry instead.
* Consumption is atomic: validation and the spent-mark happen inside one critical
  section, so two callers racing for a single-use approval cannot both win
  (SAFETY-01 §25).

Anti-escalation properties, each covered by test:

```text
an approval for READ      cannot authorise ECU_MUTATION
an approval for target A  cannot authorise target B
an expired approval       cannot authorise anything
a spent approval          cannot authorise a second operation
an approval issued by a machine caller cannot exist at all
```

Approvals are in-memory and session-scoped. There is deliberately no `save`,
`load` or serialisation: a pre-restart authorisation must not outlive the session
that reasoned about it.

---

## 12. Audit contract

Both verdicts are recorded — the refusals are the part of the trail that shows
the kernel was working. So are the actions that move authority.

```text
timestamp · caller kind · caller name (bounded label)
operation id · operation class · risk level
device / channel / target address
decision · reason code · message (kernel text) · detail (kernel coordinates)
approval reference · parameters digest · reason digest
arm state · arm scope expired · emergency stop engaged
outcome
```

**The event shape cannot carry a secret** — and since SAFETY-01-FIX-1 that is a
property rather than a claim. The event once had three free-text slots
(`caller_name`, `message`, `detail`) while this document described it as having
none; an operator reason travelled through two of them verbatim. The correction
(invariant S19):

```text
caller_name      a bounded label: ≤64 chars of [A-Za-z0-9._:-], refused otherwise
message          kernel text — a fixed sentence per action, never caller input
detail           kernel-rendered coordinates (enums, numbers, coordinates)
reason_digest    sha256 of an operator reason; the reason text is not stored
*_digest         sha256 of a parameter bag; the parameters are not stored
```

A reason is still attributable — the same reason always hashes the same way, so
two records can be matched, and a reason can be confirmed after the fact by
whoever already knows it — without the trail ever holding it. Operation parameters
are exactly where a security-access key, seed, token or unlock payload would
travel, and this shape was frozen before any such operation exists.

Rules for any future extension of the trail:

```text
never record credentials, security keys, auth tokens or seed material;
never record raw operation parameters — record a digest;
never record an operator reason as text — record reason_digest;
never add a free-text field a future caller could fill with a payload;
record ALLOW and DENY, never only the operations that proceeded;
never let a sink failure turn into a silent absence of a record.
```

`SafetyAuditSink` is a protocol with a bounded in-memory implementation. **No
SQLite table and no migration was added**: `docs/PROJECT_STATE.md` §3 keeps
SQLite for project metadata and this trail has no natural home there yet. When
persistence arrives it attaches to the protocol, and it will need durability
across restart and tamper evidence — named here so they are not rediscovered as
surprises.

A decision that cannot be recorded is **not handed back**. `evaluate` raises
`SafetyAuditError` instead of returning the verdict, because a verdict nobody can
audit is not one the rest of the system may act on.

---

## 13. Adapter boundary — where the rule becomes enforceable

Invariant S12 cannot be enforced by the safety package: a caller that never asks
the kernel is never refused by it. So the rule is pinned where a transmit path
would first appear.

Current state, verified against this tree:

```text
CanAdapter protocol      open · close · recv · capabilities · statistics
VirtualAdapter           no transmit method
capabilities().tx        False
HTTP surface             /health /runtime/status /capture/* /metrics /tools/execute
                         /runtime/shutdown /stream/frames /project/inspect
                         /trace/* /dbc/*   — no /tx, /inject, /uds
SafetyKernel             evaluate() only — no execute, no dispatch, no send
```

> **No real production TX path currently exists in CAN-X.**

That sentence is load-bearing, and it is asserted by test rather than promised:
`tests/unit/safety/test_device_transmit_boundary.py` fails the moment a
transmit primitive appears on `CanAdapter`, a `send`-like method appears on
`VirtualAdapter`, an execution verb appears on `SafetyKernel`, the safety package
imports a device or transport module, or the HTTP surface grows a dangerous
endpoint.

The intended failure of that test is a prompt, not an obstacle. When a real TX
path is added, the test is updated in the same change that routes transmission
through the kernel — and the reviewer sees both halves.

---

## 14. Agent safety boundary

CAN-X's Agent Runtime already had a risk enum before this stage
(`ToolRisk`, six levels). SAFETY-01 added two consequences to the taxonomy
(`DIAGNOSTIC_MUTATION`, `ACTUATION`) and could have kept the two enums separate
with a translation layer.

It did not. `ToolRisk` **is** `RiskLevel` — an alias, not a copy. Two enums that
mean the same thing drift the first time one is edited, and the failure mode is a
tool whose `risk_level` reads "safe" to the executor and "dangerous" to the
policy. A test asserts the identity.

Agent rules:

```text
the Agent auto-executes READ / COMPUTE / WRITE_PROJECT              (AGENTS.md §17)
everything above WRITE_PROJECT is refused by the ToolExecutor outright;
  authorising it is the safety kernel's decision, and the executor has no arm
  state, no approval and no audit trail to make that decision with
an Agent may REQUEST any operation it likes — asking is not a privilege
an Agent may not arm, may not confirm an arm, may not issue an approval,
  may not widen its permission set, may not extend an approval's expiry,
  may not change safety policy, may not call Adapter.send
```

An Agent that holds a valid approval, a live arm scope and the right capability
**is** authorised, exactly as an operator would be. That is intentional: the
controls are the arm state, the capability and the approval — not the
machine-ness of the requester. Treating "it came from an Agent" as a denial
reason would make the real controls look decorative.

---

## 15. Script / sandbox safety boundary

Restated here because it is a safety boundary, not an implementation detail:

```text
Agent-generated Python / Script Sandbox MUST NOT receive:
  raw CAN device handle
  direct python-can Bus instance
  TX credentials
  direct adapter object
  unrestricted host filesystem
```

A script that needs real CAN capability calls a controlled Tool API, which
crosses the kernel. SAFETY-01 does not implement a new sandbox — the existing
isolation is defined in `SPEC.md` §26 and `AGENTS.md` §18. This stage's
contribution is that the boundary is now written where a future script-capability
task will look for it, and that the kernel has no execution primitive a sandbox
escape could reach.

---

## 16. Emergency stop contract

```text
Emergency Stop
→ globally disarm
→ deny new dangerous operations
→ request cancellation of active dangerous operations
→ audit event
```

* **Any caller kind may engage it.** An Agent, script or automation rule that
  detects danger can pull it. Engaging only ever reduces authority.
* **Only an operator or the host may release it.** Releasing restores the
  *possibility* of dangerous work, which is an authority decision.
* Engaging drops every approval in the session. A re-arm therefore does not
  restore the ability to do dangerous work — authority has to be re-established.
* A subsystem that cannot confirm cancellation is **reported**, not swallowed:
  `EmergencyStopState.cancellation_failures` names it.
* Observational work is not blocked. The stop denies *dangerous* work; reading
  the bus remains available, because hiding the evidence during an incident is
  the opposite of helpful.

`OperationCanceller` is the seam a future transmit engine plugs into.

> **NOT VERIFIED:** interrupting a real device write. There is no real transmit
> path, so there is nothing to interrupt. The contract, the state and the refusal
> are implemented; the interruption is a later integration and is listed in §20.

---

## 17. Fail-safe rules

```text
R1   Dangerous capability defaults to DENY (S1) — an operation nobody classified
     is refused
R2   An internal fault produces DENY, never ALLOW (S9)
R3   An unclassifiable operation is refused, not defaulted
R4   An unknown caller kind cannot be constructed
R5   An unresolvable approval reference is refused, never read as "no approval
     needed"
R6   A state transition outside the machine is refused, not ignored
R7   An expired or lapsed authority is no authority, not a stale one
R8   A non-finite clock reading expires authority rather than extending it
R9   An unrecordable decision is not returned
R10  A cancellation that could not be confirmed is reported
R11  A read that transmits needs transmission authority (S15): semantic effect
     risk never substitutes for the capability the execution costs
R12  A provenance label that does not match its source is refused (S16)
R13  A failed audit rolls authority back — downwards only (S17). A reduction
     that could not be recorded stands; a grant that could not be recorded does
     not
R14  A dangerous grant without a finite expiry cannot be constructed (S18)
R15  Caller-controlled text does not reach the trail (S19)
```

R8 deserves its own line because it is the least obvious. `NaN` compares false
against everything, so a check written `now >= expires_at` answers "not expired"
for a corrupted clock — the failure would *extend* an authority. Every expiry
check in the package routes through `has_lapsed`, written
`not (now < expires_at)`, so the broken input lands on the safe side.

---

## 18. Restart semantics

```text
restart → DISARMED, no approvals, no arm scope, nothing restored
```

There is no `remember armed state`, no `auto-restore dangerous permission`, and
no persistence hook that could become one. The kernel is constructed
`DISARMED` with an empty permission set, and approvals live only in memory.

This is a deliberate refusal of a convenience: an operator who re-opens CAN-X the
next morning has to mean it again.

---

## 19. Concurrency

The runtime may receive a UI request, an Agent request, an automation request and
a script request at the same time. The hazards and their treatments:

```text
check-then-use on a single-use approval
    → validation and consumption happen inside one critical section
      (ApprovalStore.consume); a check performed before the lock would
      reintroduce exactly the race the store exists to close

stale ARM state read
    → ArmController holds state and scope behind one lock; active_scope()
      answers "is there a live authority" and "has it lapsed" together

decision taken against authority changing mid-flight
    → SafetyKernel holds one re-entrant lock across evaluation and every
      authority mutation

approval double consumption
    → the spent-mark is set inside the same critical section that validated it
```

**Named limitation.** The kernel's lock covers the kernel's own state. It cannot
make an *external* execution atomic with the decision — a future caller that
evaluates and then acts still has a gap between the two. Closing that gap belongs
to whoever owns the execution path (an authorization token with its own lifetime,
a reservation, or execution inside the kernel's lock), and is a design obligation
for the TX task, not something this stage can finish. It is recorded here rather
than papered over.

No distributed authorisation, no multi-process coordination, no external lock
service: CAN-X is a single-machine engineering tool and this stage does not
over-build for a shape it does not have.

---

## 20. Known limitations and NOT VERIFIED items

Honest, and deliberately not softened:

```text
Real CAN TX safety                            NOT VERIFIED
Real vehicle behaviour                        NOT VERIFIED
UDS mutation safety                           NOT VERIFIED
Emergency stop against real hardware          NOT VERIFIED
Hardware fail-safe                            NOT VERIFIED
Vehicle qualification                         NOT VERIFIED
Device reconnect / channel change auto-disarm CONTRACT ONLY (§9)
Transport-fault auto-disarm                   CONTRACT ONLY (§9)
Permission-invalidation auto-disarm           CONTRACT ONLY (§9)
Audit durability across restart               NOT IMPLEMENTED
Audit tamper evidence                         NOT IMPLEMENTED
External execution atomic with the decision   NOT IMPLEMENTED (§19)
```

Structural limits of this stage:

* **No execution.** The kernel authorises; it does not perform. There is no
  transmit, injection, replay, diagnostic request or ECU mutation anywhere in the
  package, and no path from it to a device.
* **No persistence.** Approvals and arm state are session-scoped by design; audit
  is in-memory and bounded. No SQLite schema change was made.
* **No device identity in practice.** `OperationTarget.device_id` exists in the
  shape but CAN-X has no device manager to fill it with a real identifier.
* **`windows-latest` is the only CI runner.** A green CI run verifies Windows
  only, and never real hardware.

---

## 21. Future integration rules

Binding on every task that adds a dangerous capability:

```text
1. It enters through the kernel. There is no exception for "it is only the UI",
   "it is only a test tool", or "it is internal".
2. It declares an operation class in the taxonomy, or the taxonomy gains a row —
   never a default.
3. It does not weaken an invariant. If it appears to need to, the architecture is
   wrong and the architecture is what changes, in this document and in `SPEC.md`,
   before the code.
4. It does not get a private path to Adapter.send.
5. It does not add a free-text field to the audit event.
6. It does not persist or restore authority.
7. It comes with its own refusal-path tests, not only a happy path.
8. If it can be interrupted, it registers an OperationCanceller.
9. If it needs more than the standard approval table, the table changes
   explicitly — with the reason written down.
```

Safety invariants cannot be bypassed by a task prompt. If a task instruction
requires breaking one, the correct action is to say so and stop, not to comply
quietly.

---

## 22. Where the code lives

```text
runtime/canx/safety/
├─ risk.py         RiskLevel · Capability · OperationClass · classification
├─ caller.py       CallerKind · CallerIdentity · who may supply authority
├─ scope.py        OperationTarget · ArmScope · has_lapsed
├─ arm.py          ArmState · ArmController · the transition table
├─ permission.py   PermissionGrant · PermissionSet
├─ approval.py     Approval · ApprovalIssuer · ApprovalStore
├─ operation.py    OperationRequest
├─ decision.py     DecisionOutcome · SafetyReason · PolicyDecision
├─ policy.py       SafetyPolicy · SafetyContext · ApprovalRequirement
├─ audit.py        SafetyAuditEvent · SafetyAuditSink · InMemoryAuditSink
├─ emergency.py    EmergencyStopController · EmergencyStopState · OperationCanceller
├─ kernel.py       SafetyKernel — the authority
└─ errors.py       SafetyError and its typed family (safety.*)
```

Tests: `tests/unit/safety/` — refusal paths, fault injection, cross-caller
matrices, the anti-escalation properties, and the device/HTTP boundary guards.

---

## 23. Relationship to the other documents

```text
PRD.md                     why CAN-X exists
SPEC.md §32                the safety rule in architecture terms (TX Policy chain)
AGENTS.md §16–§18, §22     the execution rules for safety, Agent and sandbox
docs/PROJECT_STATE.md      what exists today
this document              what the safety boundary IS, frozen
```

If the code and an invariant here disagree, the code is wrong. If a new need and
an invariant disagree, this document and `SPEC.md` change first — deliberately,
in the open — and then the code.

---

## 24. Acceptance remediation (SAFETY-01-FIX-1)

SAFETY-01's first independent review returned **NOT PASS**:

```text
Final Acceptance: NOT PASS
Status: AWAITING FIX

P0 = 3
P1 = 2
P2 = 1
```

All six findings were correct. Each is recorded here with what it was, why the
model could not hold it, and what now does. S1–S14 were not weakened; S15–S19
were added for the properties the review found missing.

### P0-1 — READ effect and physical TX authority were conflated

The model was `operation → risk → exactly one capability`, which cannot express an
operation that **observes** and still **transmits**. `diagnostic.read` landed on
`Capability.READ` alone, so a future would have been free to let it ride the
low-risk automatic path — past the ARM state, the `CAN_TX` grant and the audit
trail that every other real transmission crosses.

Fixed by splitting the axes (§6): `OperationPolicy` carries `effect_risk` and
`required_capabilities` separately, the chain checks **every** required
capability, and `requires_arm` is true for anything dangerous *or* transmitting.
`ToolDefinition.required_capabilities` replaced the agent registry's permission
strings, and `ToolExecutor` refuses any tool whose set contains `CAN_TX` —
whatever its effect risk says.

```text
RED    5 failed / 19 passed   diagnostic.read returned ALLOW without CAN_TX,
                              while DISARMED, and outside the arm scope
GREEN  24 passed
```

### P0-2 — Approval provenance could be forged

`Approval.issuer` was carried by the approval payload, and `ApprovalStore.grant`
checked only that the grantor *may* issue approvals — never that the label matched
the grantor. The host system could therefore hand over an approval labelled
`HUMAN_OPERATOR`, have it stored as a human one, and satisfy
`human_issuer_required` for actuation, ECU mutation and critical operations.

Fixed by deriving provenance instead of accepting it (§11): `ApprovalSpec` has no
issuer field, `issuer_for` maps the caller kind to the issuer, and the store
independently refuses a mismatch.

```text
RED    probe: caller=system, payload issuer=human.operator
              → ACCEPTED - stored issuer = human.operator
GREEN  probe: → REFUSED (safety.approval_provenance)
```

### P0-3 — Authority could survive a failed audit

`arm`, `confirm_arm`, `grant_approval` and `release_emergency_stop` all mutated
authority and *then* wrote the trail. A sink failure raised `SafetyAuditError`
while the authority stayed changed: the operation looked failed and its effect
survived, leaving the runtime holding authority no record accounted for.

Fixed by `_record_or_rollback` (§17, invariant S17): the mutation commits, the
audit is written, and a failure rolls the authority back — always to a *reducing*
action, so a rollback can never itself create authority. A failed `confirm_arm`
lands on `DISARMED` rather than back on `ARMING`, so a confirmation nobody saw
cannot be completed by the next caller. The reducing operations (disarm, engage,
revoke) are deliberately not wrapped: for them the authority is already gone and
undoing it would be the failure rather than the fix.

```text
RED    6 failed / 4 passed   an armed state, a granted approval and a released
                             stop all survived their own failed audit
GREEN  10 passed
```

### P1-1 — The audit trail still had caller-controlled text

This document claimed the event "cannot carry a secret" while three fields could:
`caller_name`; `message`, which carried `disarm(reason=…)` and
`engage_emergency_stop(reason=…)` verbatim; and `detail`, which rendered
`EmergencyStopState.reason`. A stop reason is exactly where a token ends up.

Fixed by removing the text rather than the claim (§12, invariant S19):
`reason_digest` replaces raw reasons, a control event's `message` is kernel text,
and `CallerIdentity.name` is bounded to a label alphabet and length.

```text
RED    13 failed / 2 passed
GREEN  15 passed
```

### P1-2 — The dangerous-permission expiry contract was documentation only

`PermissionGrant`'s docstring said dangerous grants were "deliberately not
optional" and that "policy enforces that distinction"; policy had no such check. A
`CAN_TX` grant could exist with `expires_at=None` — an authority to transmit that
outlives the reason it was issued.

Fixed by enforcing it at construction (§11, invariant S18), where a grant that
cannot be built cannot be handed to a consumer that forgot to ask.

```text
RED    10 failed / 6 passed
GREEN  16 passed
```

### P2-1 — PR handoff metadata was stale

The pull request body described the head commit from before the last push. It is
regenerated from the live GitHub state rather than from memory.

### What the remediation did not change

```text
S1–S14      unchanged, and none weakened
tests       none deleted, skipped or loosened
ci.yml      untouched
ruleset     untouched
TX / UDS    still absent — the boundary is enforced; no capability was added
```

The five properties the review was reasoning about are now frozen as S15–S19, and
each is asserted by tests rather than by this document.
