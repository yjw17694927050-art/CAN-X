# CAN-X — Safety Architecture

> **Document**: `docs/architecture/SAFETY_ARCHITECTURE.md`
> **Applies To**: every capability that can change a vehicle, and every caller that might ask for one
> **Status**: implemented, remediated after the first independent review returned NOT PASS, hardened after the second independent review returned NOT PASS, and awaiting independent re-acceptance (SAFETY-01-FIX-1; SAFETY-01-FIX-2 — see §24, §25)
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
S20  An authority-increasing safety action is committed only when its complete
     audit transaction succeeds — event preparation, event construction and the
     audit sink write. If any part of that chain fails, the authority is rolled
     back first and the fault is propagated second.
S21  Safety Audit reference fields are identifiers, and Safety Audit records only
     typed identifiers, enumerated vocabulary values, bounded structured
     coordinates and cryptographic digests. It has no field for arbitrary
     caller-controlled text.
S22  While the emergency stop is engaged, no caller may establish or pre-stage
     dangerous vehicle authority. ARM state and approval are that authority, so
     neither may be created, requested or granted while the runtime is stopped.
S23  Releasing the emergency stop restores only the possibility of rebuilding
     authority. After a successful release the runtime is DISARMED and holds no
     outstanding approval; authority must be re-established explicitly. Release
     is never a resume command.
S24  Every cancellation reference persisted by Safety Audit is a typed identifier
     or a bounded structured failure code. The raw operator reason and arbitrary
     subsystem text do not cross the cancellation audit boundary.
```

S1–S14 were frozen by SAFETY-01. S15–S19 were added by SAFETY-01-FIX-1 after the
first independent review returned `NOT PASS`, and they are the five things that
review found the model could not yet hold (§24). S20–S21 were added by
SAFETY-01-FIX-2 after the second independent review returned `NOT PASS`, and they
are the two things that review found it still could not hold (§25). S22–S24 were
added by SAFETY-01-FIX-3 after the **third** independent review returned
`NOT PASS`, and they are the two properties that review found the model could not
hold, plus the boundary that follows from the second of them (§26).

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
| S17 | `_commit_authority_change_with_audit` commits then rolls back a failed audit to a *reducing* action; a failed rollback raises `SafetyRollbackError` rather than a softer fault | `safety/kernel.py` |
| S18 | `PermissionGrant.__post_init__` refuses a dangerous grant without a finite expiry | `safety/permission.py` |
| S19 | `reason_digest` replaces raw reasons; a control event's `message` is kernel text; `caller_id` is a bounded identifier | `safety/audit.py`, `safety/caller.py`, `safety/emergency.py` |
| S20 | The whole preparation *and* the sink write are inside the commit guard's `try`; `SafetyAuditEvent` refuses a non-finite `recorded_at`; `_record_control`/`_record_decision` normalise every audit-chain exception into `SafetyAuditError` | `safety/kernel.py`, `safety/audit.py`, `tests/unit/safety/test_authority_audit_atomicity.py` |
| S21 | Every reference field is validated by one central contract at construction — the domain types *and* `SafetyAuditEvent.__post_init__` | `safety/identifiers.py`, `safety/audit.py`, `safety/caller.py`, `safety/operation.py`, `safety/scope.py`, `safety/approval.py`, `tests/unit/safety/test_audit_identifiers.py` |
| S22 | `_require_emergency_stop_released` gates `arm`, `confirm_arm` and `grant_approval` *before* any mutation and inside the kernel lock; a broken clock or a malformed canceller answer cannot prevent the stop itself | `safety/kernel.py`, `safety/emergency.py` |
| S23 | `release_emergency_stop` disarms and clears approvals as part of the release, deliberately outside the commit guard so a failed audit cannot put them back; only the stop's engagement is rolled back | `safety/kernel.py`, `tests/unit/safety/test_emergency_stop.py` |
| S24 | `OperationCanceller` returns `OperationId` and receives `reason_digest`; the controller revalidates every returned value and records a structured `CancellationFailure`; `CancellerId` replaces `type(x).__name__`; `EmergencyStopState.__post_init__` re-checks its audit-facing fields | `safety/emergency.py`, `safety/identifiers.py`, `safety/kernel.py`, `tests/unit/safety/test_emergency_stop.py` |

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
operation_id        validated identifier, for correlation
operation_class     what it does (RiskLevel is derived, never supplied)
caller              CallerIdentity (whose caller_id is itself an identifier)
target              OperationTarget — device / channel / target address
requested_at        the instant the caller asked; must be finite
approval_id         optional validated identifier of an approval to spend
parameters_digest   sha256 of the parameters, never the parameters
```

Every textual field here is a **validated identifier** rather than a string that
happens to be short (invariant S21). ``operation_id`` and ``approval_id`` both
reach the trail, so both are governed by the same contract as the caller identity
— the runtime mints its own references with
``new_operation_id()`` / ``new_approval_id()``, and a reference supplied from
outside is accepted only when it already satisfies it.

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

`CallerIdentity.caller_id` is an **identifier**, not a display name and not a
credential (invariant S21). It was named `name` until SAFETY-01-FIX-2, which
implied human-readable text it never was. A display label is not authority, it is
not attributable, and it is exactly the shape a payload takes when somebody puts
one in an identity — so there is deliberately **no** display-label field, and a UI
that wants to show "YJW (bench operator)" keeps that string in the UI.

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
* **While the emergency stop is engaged, `arm()` and `confirm_arm()` refuse**
  with `SafetyEmergencyStopError` (invariant S22). The refusal happens before
  `ArmController` is asked, so `ARMING` is never entered and there is no
  half-armed state left to complete later. The stop removes authority; it does not
  hold it in escrow for whoever releases it.
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
device_id        adapter identity — a DeviceId        (future: no device manager yet)
channel          bus channel — a ChannelId
target_address   CAN identifier — already a bounded integer
```

`device_id` and `channel` are **identifiers**, not descriptions (invariant S21):
`pcan-usb-1`, `can1` and `virtual-0` are the shape, and "my device password is …"
is not. No vendor grammar is frozen — CAN-X has no device manager yet, and
inventing one would be fiction — but the *kind* of value is frozen, so a
coordinate cannot become a place to write prose.

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

**No approval may be granted while the emergency stop is engaged** (invariant
S22). An approval is dangerous authority, not a formality, so staging one during a
stop would shorten the chain the stop exists to lengthen: release, and the
pre-staged approval is already there. `SafetyKernel.grant_approval` therefore
refuses with `SafetyEmergencyStopError` before constructing anything, and the stop
clears every approval when it engages — so a released runtime finds none waiting.

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
event id (runtime-generated identifier) · timestamp (finite)
caller kind · caller id (identifier)
operation id (identifier) · operation class · risk level
device id / channel (identifiers, or absent) / target address (integer, or absent)
decision · reason code · message (kernel text) · detail (kernel coordinates)
approval reference (identifier, or absent) · parameters digest · reason digest
arm state · arm scope expired · emergency stop engaged
outcome
```

### The audit transaction

"The audit" means the **whole** commit path, not the sink call at its end
(invariant S20):

```text
prepare      generate the event id, read the clock, validate every reference
             ↓
construct    build the SafetyAuditEvent (which re-checks every reference)
             ↓
write        audit_sink.record(event)
             ↓
recorded     the authority change now has a record
```

Any exception at any of those three stages is an audit that did not happen. For
an **authority-increasing** action (`arm`, `confirm_arm`, `grant_approval`,
`release_emergency_stop`) the kernel first returns the runtime to a safe state and
only then propagates the fault:

```text
any stage fails → roll back to a reducing action → raise a typed safety fault
```

The fault is normalised rather than passed through: a clock that raises
``RuntimeError``, a UUID provider that raises, a coordinate that fails validation
and a ``json.dumps`` fault all reach the caller as :class:`SafetyAuditError`, with
the original exception preserved as ``__cause__``. A caller must never have to
tell "the safety audit could not commit" apart from "the product broke" by
inspecting a message.

**Rollback failure is its own, stronger fault.** If the audit fails *and* the
rollback fails, authority may remain with no record accounting for it, and the
runtime's safety state can no longer be trusted. That is reported as
:class:`SafetyRollbackError` — deliberately **not** a ``SafetyAuditError``, so a
caller that catches the ordinary audit fault cannot accidentally swallow the
unknown one. It carries the action, both failure types and both exception objects,
and no payload. There is no invented ``FAULTED`` arm state: the loud fault is the
contract, and with no real device attached there is nothing to fail safe *into*.
When a real transmit path exists, reaching this state must additionally trigger
the global fail-safe / emergency semantics (§17.1).

**The reducing direction is not symmetric, on purpose.** `disarm`,
`engage_emergency_stop` and `revoke_approval` are not routed through the commit
guard. For them the authority is already gone when the audit runs, and undoing a
successful safety reduction in order to "keep the transaction consistent" would be
the failure rather than the fix:

```text
audit failure may cause authority to be lost
audit failure must NEVER cause authority to be restored
```

Fail safe — not fail transactionally symmetric.

### The identifier contract

**Safety Audit reference fields are identifiers, not arbitrary caller text**
(invariant S21). This is the correction SAFETY-01-FIX-2 made, and it is stated as
a contract rather than a filtering rule:

```text
identifier     1..64 characters of [A-Za-z0-9._:-] — the shared contract
digest         64 lowercase hex characters (sha256)
message        kernel text — a fixed sentence per action, never caller input
detail         kernel-rendered coordinates (enums, numbers, coordinates)
```

Which fields are which:

```text
event_id        AuditEventId    runtime-generated
caller_id       CallerId        from the caller's identity
operation_id    OperationId     from the request (or `kernel.<action>`)
approval_id     ApprovalId      or absent
device_id       DeviceId        or absent
channel         ChannelId       or absent
parameters_digest / reason_digest   sha256 hex, or absent
caller_kind · operation_class · decision · reason_code · arm_state · outcome
                enum values — themselves bounded identifiers
```

`detail` is not a loophole in that list. It is a *rendered projection of kernel
coordinates*, and those coordinates are held to the same contract: since
SAFETY-01-FIX-3 the emergency stop's rendered state contains booleans, a finite
timestamp or `None`, a digest, `OperationId` values and structured
`CancellationFailure` records — a `CancellerId` (identifier) and a
`CancellationFailureCode` (bounded vocabulary). No free-form text is constructed
into it anywhere, which is why the canceller return value is revalidated rather
than quoted (invariant S24).

**Why the rule is an alphabet and not a detector.** The shortcut is to reject
values that "look like secrets" — containing ``SECRET``, or ``+``/``/``/``=``, or
matching a base64 shape. That is not a security boundary, because a secret can be
*any* string: ``abc123`` may be a password, and ``runtime.host`` may be a token
that looks like a hostname. A rule that admits a value because it does not resemble
the secret it is, fails on the one input that matters. So the contract is the
shape, and it is applied without exception.

**Enforced twice, deliberately.** Every domain type that can reach the trail
(`CallerIdentity`, `OperationRequest`, `OperationTarget`, `ApprovalSpec`,
`Approval`) validates its references at construction, *and*
``SafetyAuditEvent.__post_init__`` re-checks all of them. That is defence in depth
rather than duplication: the audit trail is the boundary that persists, so an
event built by some future path that skipped the domain constructors must still be
unstorable. A stored event holds typed identifiers, not bare strings.

`requested_at` and `recorded_at` must be **finite**: a ``NaN`` timestamp is a
malformed record, and ordering a trail by a value that compares false against
everything is not ordering it at all. A non-finite clock reading therefore cannot
be stamped — for an authority-increasing action it fails the transaction and rolls
back; for a decision it means no verdict is handed back.

**What this claim is, and is not.** It is *not* "no secret can ever enter a log" —
that is not provable and not what the contract says. What is frozen is narrower and
checkable:

> Safety Audit accepts only typed identifiers, enumerated vocabulary values,
> bounded structured coordinates and cryptographic digests. Arbitrary
> caller-controlled text and raw operation parameters have no field in the Safety
> Audit contract.

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
never add a reference field without giving it an identifier contract;
record ALLOW and DENY, never only the operations that proceeded;
never let a failure in *any* stage of the audit commit become a silent absence
  of a record — roll back the authority first, then raise.
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
→ block the creation of new authority
→ request cancellation of active dangerous operations
→ audit event
```

The stop is a **safety epoch boundary, not a pause** (invariants S22, S23):

```text
before the stop    authority may exist
while stopped      no dangerous authority can be created or pre-staged
after the release  no dangerous authority exists
                   authority has to be explicitly rebuilt afterwards
```

* **Any caller kind may engage it.** An Agent, script or automation rule that
  detects danger can pull it. Engaging only ever reduces authority — which is
  also why nothing on the engage path can fail it: a clock that cannot be read
  records `engaged_at = None`, a canceller that raises or answers with prose is
  reported in the state, and the stop still engages. A reduction that could not
  be recorded stands (invariant S17).
* **Only an operator or the host may release it.** Releasing restores the
  *possibility* of dangerous work, which is an authority decision. The caller
  check runs before anything is mutated, so a refused release cannot leave the
  runtime half-released.
* **Releasing is never a resume command** (S23). After a successful release the
  runtime is `DISARMED` and holds no approval. Both reductions run as part of the
  release and deliberately *outside* the audit commit guard: a release whose
  audit could not be written restores the stop's engagement and nothing else —
  putting authority back would be the failure, not the fix.
* **No authority may be pre-staged while the stop is engaged** (S22). `arm`,
  `confirm_arm` and `grant_approval` each refuse with `SafetyEmergencyStopError`
  (`safety.emergency_stop_active`), before mutating anything, so there is nothing
  to roll back. `confirm_arm` carries its own gate rather than relying on
  `arm`'s: a runtime that was already `ARMING` when the stop engaged reaches
  `ARMED` through the confirmation and never calls `arm` again.
* **Engaging drops every approval in the session.** Combined with the gate above,
  a released runtime cannot find a live approval waiting for it.
* **The cancellation boundary is typed** (S24). A canceller is registered under
  a stable `CancellerId` (`"tx.periodic"`, not `type(x).__name__`), receives a
  reason **digest** rather than the operator's words, and returns
  `tuple[OperationId, ...]`. Because the return type is a typing promise rather
  than a runtime guarantee, the controller revalidates every value: identifiers
  it can keep are kept, anything else becomes a structured `CancellationFailure`
  and the text itself is never stored.
* A subsystem that cannot confirm cancellation is **reported**, not swallowed:
  `EmergencyStopState.cancellation_failures` names it, by validated canceller id
  and bounded failure code — never by the exception's message.
* Observational work is not blocked. The stop denies *dangerous* work; reading
  the bus remains available, because hiding the evidence during an incident is
  the opposite of helpful.

`OperationCanceller` is the seam a future transmit engine plugs into:

```python
register_canceller("tx.periodic", canceller)
canceller.cancel_active_operations(reason_digest=…) -> tuple[OperationId, ...]
```

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
R16  A failure anywhere in the audit *transaction* — preparation, construction or
     the sink write — rolls the authority back, not only a sink failure (S20)
R17  An authority change that could be neither audited nor rolled back raises a
     typed fault of its own rather than a softer one (S20): the runtime state can
     no longer be trusted and the caller must not be told otherwise
R18  A reference field reaches the trail only as a validated identifier, and a
     non-finite timestamp never reaches it at all (S21)
R19  An authority-increasing control action is refused while the emergency stop
     is engaged, before anything is mutated, so the refusal is a gate and not a
     rollback (S22)
R20  A release that could not be audited restores the stop's engagement and
     nothing else: the authority it removed stays removed (S23)
R21  A cancellation answer that violates the typed contract is recorded as a
     structured failure, never defeats the stop, and never reaches the trail as
     text (S24)
```

R8 deserves its own line because it is the least obvious. `NaN` compares false
against everything, so a check written `now >= expires_at` answers "not expired"
for a corrupted clock — the failure would *extend* an authority. Every expiry
check in the package routes through `has_lapsed`, written
`not (now < expires_at)`, so the broken input lands on the safe side.

R16 and R17 are the pair SAFETY-01-FIX-2 added, and they are one idea from two
sides. R16 says the transaction is the whole commit path, so a fault *before* the
sink rolls back like a fault at the sink. R17 says the one case that cannot be
recovered must be reported as what it is: if the rollback also failed, the runtime
is holding unaudited authority and every subsequent decision is suspect. Neither
rule invents a new state — the fault is the contract.

R19–R21 are the set SAFETY-01-FIX-3 added, and they close one idea from three
sides. R19 says a stop blocks authority instead of parking it; R20 says the
release therefore cannot hand any back; R21 says the cancellation reporting that
accompanies both enters the trail under the same typed contract as everything
else. Together they make the emergency stop an epoch boundary rather than a mode.

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
Rollback-failure fail-safe *actuation*        CONTRACT ONLY (§12, §17.1) — the
                                              typed fault is raised; with no
                                              device there is nothing to fail
                                              safe into
```

Structural limits of this stage:

* **No execution.** The kernel authorises; it does not perform. There is no
  transmit, injection, replay, diagnostic request or ECU mutation anywhere in the
  package, and no path from it to a device.
* **No persistence.** Approvals and arm state are session-scoped by design; audit
  is in-memory and bounded. No SQLite schema change was made.
* **No device identity in practice.** `OperationTarget.device_id` exists in the
  shape but CAN-X has no device manager to fill it with a real identifier, so the
  identifier contract is enforced without a vendor grammar to enforce.
* **An identifier is not a redaction.** The contract makes arbitrary text
  unrepresentable in a reference field; it does not scan free-form values that a
  *future* task might add. Adding such a field is what invariant S21 and §21.5
  forbid.
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
8. If it can be interrupted, it registers an OperationCanceller — under a stable
   `CancellerId`, returning `OperationId` values, accepting a reason digest
   rather than the operator's words (S24).
9. If it needs more than the standard approval table, the table changes
   explicitly — with the reason written down.
```

Safety invariants cannot be bypassed by a task prompt. If a task instruction
requires breaking one, the correct action is to say so and stop, not to comply
quietly.

**Deferred, and named rather than half-built.** A general *authority epoch* —
a monotonic counter that would invalidate old authority on an emergency stop,
a restart, a device reconnect or a channel change alike — is the natural
generalisation of S22/S23 and is **not** in this stage. `DISARMED` + no approvals
expresses the whole requirement the third review set, so FIX-3 stops there rather
than inventing a fourth piece of authority state. When a second invalidation
source arrives (device reconnect, channel change), the epoch is the shape to
reach for — and it would then be S22/S23's mechanism rather than a replacement
for them.

---

## 22. Where the code lives

```text
runtime/canx/safety/
├─ risk.py         RiskLevel · Capability · OperationClass · classification
├─ caller.py       CallerKind · CallerIdentity · who may supply authority
├─ identifiers.py  the audit-safe identifier + digest contract (S21, S24)
├─ scope.py        OperationTarget · ArmScope · has_lapsed
├─ arm.py          ArmState · ArmController · the transition table
├─ permission.py   PermissionGrant · PermissionSet
├─ approval.py     Approval · ApprovalIssuer · ApprovalStore
├─ operation.py    OperationRequest
├─ decision.py     DecisionOutcome · SafetyReason · PolicyDecision
├─ policy.py       SafetyPolicy · SafetyContext · ApprovalRequirement
├─ audit.py        SafetyAuditEvent · SafetyAuditSink · InMemoryAuditSink
├─ emergency.py    EmergencyStopController · EmergencyStopState · OperationCanceller ·
│                  CancellationFailure · CancellationFailureCode (S22–S24)
├─ kernel.py       SafetyKernel — the authority · the audit commit guard
└─ errors.py       SafetyError and its typed family (safety.*)
```

Tests: `tests/unit/safety/` — refusal paths, fault injection (including the audit
transaction's own failure points), cross-caller matrices, the anti-escalation
properties, the identifier contract and the device/HTTP boundary guards.

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

---

## 25. Acceptance remediation (SAFETY-01-FIX-2)

SAFETY-01's **second** independent review returned `NOT PASS`:

```text
Final Acceptance: NOT PASS
Status: AWAITING FIX-2

P0 = 1
P1 = 1
P2 = 1
```

All three findings were correct. Each is recorded here with what it was, why the
model could not hold it, and what now does. S1–S19 were not weakened; S20–S21 were
added for the two properties the review found missing, and the third finding was a
documentation defect.

### P0 — the audit transaction was still not fully fail-safe

SAFETY-01-FIX-1 wrapped the authority mutations in `_record_or_rollback`, and that
guard caught exactly one exception type:

```python
try:
    record()
except SafetyAuditError:      # ← and nothing else
    rollback()
    raise
```

`record()` is `_record_control(...)`, which does considerably more than write to
the sink. Before the sink is reached it generates an event id, reads the clock,
constructs the event and renders kernel coordinates into `detail`. A failure in
any of those steps — a clock provider that raises, a UUID provider that raises, a
serialisation fault, a malformed coordinate — was **not** a `SafetyAuditError`, so
it escaped the guard uncaught:

```text
mutate authority → audit preparation raises → rollback never runs → authority survives
```

The caller saw a failure. The runtime stayed armed, or held a new approval, or was
no longer stopped. That is S17 violated by exactly the path S17 was written for:
"the audit" had been read as "the sink call", and the sink call is the last step,
not the whole thing.

Fixed by making the transaction the whole commit path (invariant S20):
`_record_control` and `_record_decision` normalise **any** exception from
preparation or construction into `SafetyAuditError` with `__cause__` preserved,
and `_commit_authority_change_with_audit` catches broadly, rolls back through a
*reducing* action, and only then propagates.

```text
RED    195 failed / 48 passed   (the new fault-injection matrix)
       headline: safety.confirm_arm() raised the injected RuntimeError and left
                 arm_state == ARMED — an unaudited authority, reported as a failure
GREEN  522 passed
```

### P0's new edge — rollback failure

Fixing the above created a case the earlier design had never had to name: what if
the audit fails **and** the rollback fails? Then authority was created, no record
accounts for it, and the runtime's safety state can no longer be trusted.

Reporting that as an ordinary `SafetyAuditError` would tell the caller the rollback
succeeded when it is unknown. It is therefore a distinct, stronger type —
`SafetyRollbackError`, deliberately **not** a subclass of `SafetyAuditError` — that
carries the action, both failure types and both exception objects, and no payload.

No `FAULTED` arm state was invented for it. Inventing one would put a fourth state
into a machine whose three states are load-bearing, for a condition the fault
already describes; and with no device attached there is nothing to fail safe
*into*. The obligation is recorded instead: when a real transmit path exists,
reaching this state must trigger the global fail-safe / emergency semantics
(§12, §17.1, §20).

```text
RED    the injected rollback failure surfaced as a bare RuntimeError, with the
       authority still present and nothing typed to say the state was untrusted
GREEN  raising SafetyRollbackError, with audit_failure / rollback_failure and
       details["action"], and the still-present authority asserted explicitly
```

### P1 — four audit references were still "any non-empty string"

Invariant S19 said no arbitrary caller-controlled text may be persisted in a
safety audit event. After FIX-1 that was true of the three *obvious* free-text
slots, and false of five reference fields, four of which only had to be non-empty:

```text
caller_name      bounded label (FIX-1)
operation_id     non-empty string
approval_id      non-empty string
device_id        non-empty string
channel          non-empty string
```

`OperationRequest(operation_id="rotate the key hunter2-please")` was a legal
request, and the trail recorded that string verbatim as an identifier. The event's
no-secret property rested on the caller's restraint.

Fixed by giving every reference field a formal domain contract (invariant S21), in
`runtime/canx/safety/identifiers.py`:

```text
identifier      1..64 characters of [A-Za-z0-9._:-]
typed            CallerId · OperationId · ApprovalId · DeviceId · ChannelId ·
                 AuditEventId — str subclasses whose construction is the validation
runtime-minted   new_operation_id() · new_approval_id() · new_audit_event_id()
digests          64 lowercase hex, validated rather than trusted
enforced twice   every domain type at construction, and SafetyAuditEvent.__post_init__
```

`CallerIdentity.name` became `CallerIdentity.caller_id`, and the event's
`caller_name` became `caller_id`. It was always an identifier; the name implied
otherwise, and renaming is the honest fix.

The design decision worth recording is what this rule is *not*. It does not try to
detect secrets — no "contains SECRET", no base64 heuristic, no character-frequency
guess. A secret can be any string (`abc123` may be a password), so a rule that
admits a value because it does not resemble a secret fails on the one input that
matters. The contract is the shape, and the shape admits no prose.

```text
RED    OperationRequest(operation_id="operator entered emergency because the rig
       was smoking") was constructed, evaluated and persisted
       ApprovalSpec(approval_id=<free text>) and OperationTarget(channel=<free text>)
       likewise; SafetyAuditEvent accepted a NaN recorded_at and a non-digest
GREEN  every one refused at construction, and again by the event itself
```

### P2 — documentation had drifted from the implementation

`S1–S14` was still the stated invariant range in places, the acceptance history
did not record the second `NOT PASS`, and the tree's test counts were not the
tree's test counts. Reconciled in this document, `AGENTS.md` §16, `SPEC.md` §32 and
`docs/PROJECT_STATE.md` §17 — with the first *and* second independent verdicts kept
rather than replaced.

### What the remediation did not change

```text
S1–S19       unchanged, and none weakened
FIX-1        every FIX-1 protection intact — two-axis authority, derived
             provenance, finite dangerous-permission expiry, reason digests
tests        none deleted, skipped or loosened; the two FIX-1 clock tests were
             re-expressed against the stronger contract, and the properties they
             pinned (expiry fails closed, an unauditable verdict is not returned)
             are still asserted directly
ci.yml       untouched
ruleset      untouched
TX / UDS     still absent — the boundary is enforced; no capability was added
```

S20 and S21 are the two properties the second review found the model could not
hold, and each is asserted by tests rather than by this document.

---

## 26. Acceptance remediation (SAFETY-01-FIX-3)

SAFETY-01's **third** independent review returned `NOT PASS`:

```text
Final Acceptance: NOT PASS
Status: AWAITING FIX-3

P0 = 1
P1 = 1
P2 = 0
```

Both findings were correct. Neither was a redesign: the review named two places
where the frozen contract was stated and the code did not hold it. Each is
recorded here with what it was, why the model could not hold it, and what now
does. S1–S21 were not weakened; S22–S24 were added.

### P0 — the emergency stop was a pause, not an epoch boundary

Engaging the stop did the right things in the right order:

```text
engage E-stop → DISARM → clear approvals → stop stays ENGAGED
```

and `evaluate` refused dangerous work for as long as it was engaged. What was
missing was the other half. `arm`, `confirm_arm` and `grant_approval` were not
gated on the stop at all, so authority could be *pre-staged while the runtime was
stopped*:

```text
E-stop engaged → arm() → confirm_arm() → grant_approval()
→ release E-stop → runtime already ARMED, approval already present
→ the next dangerous operation proceeds without anyone rebuilding anything
```

The stop did not remove authority so much as park it, and the release was a
resume. That is the opposite of what an operator pulling an emergency stop is
asking for, and it defeated the reason engaging drops the session's approvals in
the first place.

Fixed in two layers, deliberately, rather than at whichever one was easier:

```text
Layer 1  block the creation of authority while stopped (S22)
         arm · confirm_arm · grant_approval
         → _require_emergency_stop_released(action=…)
         → SafetyEmergencyStopError (safety.emergency_stop_active)
         raised before any mutation, inside the kernel lock

Layer 2  assert the release postcondition instead of assuming it (S23)
         disarm + clear approvals as part of the release, outside the commit
         guard, so a failed audit cannot put them back
```

`confirm_arm` carries its own gate because it is a real bypass, not a duplicate:
a runtime that was already `ARMING` when the stop engaged reaches `ARMED` through
the confirmation and never calls `arm` again.

The gate is a typed *fault*, not a `PolicyDecision.DENY`. These are control-plane
authority mutations that never reach `evaluate`; there is no verdict for a `DENY`
to be the verdict of, and a caller needs to tell "the stop is engaged" apart from
"the arm machine has no such transition".

```text
RED    arm / confirm_arm / grant_approval each SUCCEEDED while the stop was
       engaged, and a released runtime came back ARMED with 1 outstanding
       approval — reproduced with a .rivet/scratch/ probe before any edit
GREEN  all three raise SafetyEmergencyStopError while engaged; after release
       arm_state == DISARMED, active_scope is None, approvals.outstanding() == ()
```

### P1 — the cancellation boundary was outside the identifier contract

`OperationCanceller.cancel_active_operations` returned `tuple[str, ...]`, and that
tuple went into `EmergencyStopState.requested_cancellations` unvalidated and then
into the audit event's `detail` through `EmergencyStopState.describe()`. The
failure list was worse: it was assembled as prose at the failure site.

```python
failures.append(f"{type(canceller).__name__}: {type(error).__name__}")
```

So a subsystem could put anything at all into Safety Audit's `detail` by returning
it — the exact back door S21 closed for `operation_id`, `approval_id`, `device_id`
and `channel`, reopened one layer up:

```text
canceller returns ("tx-1", "operator secret is hunter2")
→ detail: {"requested_cancellations": ["tx-1", "operator secret is hunter2"], …}
```

The same route carried the operator's raw reason out of the safety domain: every
canceller was handed `reason=…` verbatim, and a subsystem outside Safety Audit
may log what it is given.

Fixed by giving the cancellation boundary the same typed contract as the rest of
the trail (S24), in `runtime/canx/safety/emergency.py`:

```text
OperationCanceller      cancel_active_operations(reason_digest=…) -> tuple[OperationId, …]
registration            register_canceller(CancellerId, canceller) — not type(x).__name__
revalidation            every returned value is re-run through OperationId(),
                        because the return type is a typing promise, not a
                        runtime guarantee
CancellationFailure     canceller_id · failure_code · failure_type — all bounded,
                        no message text ever
CancellationFailureCode a closed vocabulary: canceller.raised ·
                        canceller.invalid_reference · canceller.contract_violation
EmergencyStopState      __post_init__ re-checks every audit-facing field, the
                        same defence in depth SafetyAuditEvent applies to itself
```

Two properties were preserved while doing it, and both are asserted:

* **The stop still engages.** A malformed answer is a failure to *report*, never a
  veto. The identifiers it did name are kept; the rest become a structured
  failure; the stop engages either way.
* **Nothing silently disappears.** S13/S14 require an operator to see *which*
  operation cancellation was requested and *which* subsystem did not answer. The
  answer is a structured, bounded `detail` — not `detail=None`, and not
  observability deleted to make a leak go away.

```text
RED    requested_cancellations == ("tx-1", "operator secret is hunter2"); the text
       appeared verbatim in the engaged event's detail; the canceller received
       reason == "bench secret xyz"
GREEN  requested_cancellations == ("tx-1",); the rest recorded as
       CancellationFailure(canceller_id="tx.periodic",
                           failure_code="canceller.invalid_reference",
                           failure_type="str"); the text is absent from state and
       trail; the canceller receives only digest_reason(...)
```

### A test that encoded the defect

`test_the_stop_survives_a_re_arm_attempt_during_the_emergency` — and the
architecture prose beside it — documented re-arming during the stop as *allowed*.
The third review found the expected behaviour itself unsafe, so the test was
rewritten as `test_rearming_is_forbidden_while_emergency_stop_is_engaged` and the
prose replaced. This is a corrected contract, not a weakened test: the old
assertion pinned a pause, the new one pins an epoch boundary. No other safety test
was deleted, skipped or loosened.

### What the remediation did not change

```text
S1–S21       unchanged, and none weakened
FIX-1        every FIX-1 protection intact — two-axis authority, derived
             provenance, finite dangerous-permission expiry, reason digests
FIX-2        every FIX-2 protection intact — the whole-transaction audit commit,
             SafetyRollbackError, the identifier contract
tests        none deleted, skipped or loosened; the one E-stop expectation that
             encoded the defect was corrected
ci.yml       untouched
ruleset      untouched
TX / UDS     still absent — the boundary is enforced; no capability was added
```

S22–S24 are the properties the third review found the model could not hold, and
each is asserted by tests rather than by this document.
