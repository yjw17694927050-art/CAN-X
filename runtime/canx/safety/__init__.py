"""CAN-X Safety Kernel — the boundary every dangerous capability must cross.

Invariant S1 states the whole design: **dangerous operations default to DENY.**
This package exists to make that sentence executable rather than aspirational.

```text
UI / Agent / Script / Automation / Protocol Workflow
                    ↓  OperationRequest
              SafetyKernel.evaluate
                    ↓  PolicyDecision
             controlled execution (later task)
                    ↓
                 Adapter
```

The package is a pure Python domain: no device, no bus, no adapter, no HTTP, no
UI shape. That is what lets every rule below be tested without a vehicle, and it
is also what makes the boundary meaningful — a policy that could open a device
would be a policy that had already crossed the line it guards.

**What this stage is.** Risk taxonomy, caller model, ARM state machine, scope,
capability-based permissions, the approval contract, the policy decision engine,
the audit contract, the audit-safe identifier contract and the emergency-stop
contract, with tests that attack the refusal paths rather than the happy path.

**What this stage is not.** There is no transmit, no injection, no replay send,
no diagnostic request and no ECU mutation anywhere in this package, and no path
from it to a device. SAFETY-01 builds the gate; the operations that will pass
through it are later tasks, and each of them is required to enter here
(invariant S12).

The frozen invariants S1-S21 are recorded in
``docs/architecture/SAFETY_ARCHITECTURE.md``. That document is the authority on
what must not be broken; this package is its implementation.
"""

from __future__ import annotations

from canx.safety.approval import (
    Approval,
    ApprovalIssuer,
    ApprovalSpec,
    ApprovalStore,
    issuer_for,
)
from canx.safety.arm import ArmController, ArmState
from canx.safety.audit import (
    DEFAULT_AUDIT_CAPACITY,
    InMemoryAuditSink,
    SafetyAuditEvent,
    SafetyAuditSink,
)
from canx.safety.caller import CallerIdentity, CallerKind
from canx.safety.decision import DecisionOutcome, PolicyDecision, SafetyReason, allow, deny
from canx.safety.emergency import (
    EmergencyStopController,
    EmergencyStopState,
    OperationCanceller,
)
from canx.safety.errors import (
    SafetyApprovalError,
    SafetyApprovalExpiredError,
    SafetyApprovalProvenanceError,
    SafetyApprovalReusedError,
    SafetyAuditError,
    SafetyCallerError,
    SafetyError,
    SafetyIdentifierError,
    SafetyPolicyError,
    SafetyRollbackError,
    SafetyScopeError,
    SafetyStateError,
    SafetyUnknownOperationError,
)
from canx.safety.identifiers import (
    AUDIT_IDENTIFIER_ALPHABET,
    MAX_AUDIT_IDENTIFIER_LENGTH,
    SHA256_HEX_LENGTH,
    ApprovalId,
    AuditEventId,
    AuditIdentifier,
    CallerId,
    ChannelId,
    DeviceId,
    OperationId,
    new_approval_id,
    new_audit_event_id,
    new_operation_id,
    validate_audit_identifier,
    validate_sha256_digest,
)
from canx.safety.kernel import SafetyKernel
from canx.safety.operation import OperationRequest
from canx.safety.permission import PermissionGrant, PermissionSet
from canx.safety.policy import (
    ApprovalRequirement,
    SafetyContext,
    SafetyPolicy,
    approval_requirement_for,
)
from canx.safety.risk import (
    DANGEROUS_CAPABILITIES,
    Capability,
    OperationClass,
    OperationPolicy,
    RiskLevel,
    approval_capability_for,
    classify_operation,
    is_dangerous_capability,
    operation_policy,
)
from canx.safety.scope import ArmScope, OperationTarget, has_lapsed

__all__ = [
    "AUDIT_IDENTIFIER_ALPHABET",
    "DANGEROUS_CAPABILITIES",
    "DEFAULT_AUDIT_CAPACITY",
    "MAX_AUDIT_IDENTIFIER_LENGTH",
    "SHA256_HEX_LENGTH",
    "Approval",
    "ApprovalId",
    "ApprovalIssuer",
    "ApprovalRequirement",
    "ApprovalSpec",
    "ApprovalStore",
    "ArmController",
    "ArmScope",
    "ArmState",
    "AuditEventId",
    "AuditIdentifier",
    "CallerId",
    "CallerIdentity",
    "CallerKind",
    "Capability",
    "ChannelId",
    "DecisionOutcome",
    "DeviceId",
    "EmergencyStopController",
    "EmergencyStopState",
    "InMemoryAuditSink",
    "OperationCanceller",
    "OperationClass",
    "OperationId",
    "OperationPolicy",
    "OperationRequest",
    "OperationTarget",
    "PermissionGrant",
    "PermissionSet",
    "PolicyDecision",
    "RiskLevel",
    "SafetyApprovalError",
    "SafetyApprovalExpiredError",
    "SafetyApprovalProvenanceError",
    "SafetyApprovalReusedError",
    "SafetyAuditError",
    "SafetyAuditEvent",
    "SafetyAuditSink",
    "SafetyCallerError",
    "SafetyContext",
    "SafetyError",
    "SafetyIdentifierError",
    "SafetyKernel",
    "SafetyPolicy",
    "SafetyPolicyError",
    "SafetyReason",
    "SafetyRollbackError",
    "SafetyScopeError",
    "SafetyStateError",
    "SafetyUnknownOperationError",
    "allow",
    "approval_capability_for",
    "approval_requirement_for",
    "classify_operation",
    "deny",
    "has_lapsed",
    "is_dangerous_capability",
    "issuer_for",
    "new_approval_id",
    "new_audit_event_id",
    "new_operation_id",
    "operation_policy",
    "validate_audit_identifier",
    "validate_sha256_digest",
]
