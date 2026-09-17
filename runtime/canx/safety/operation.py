"""What a caller asks the kernel to authorise.

An :class:`OperationRequest` is a pure domain value. It names *what* is being
asked, *who* is asking, *where* it would act and *when* it was asked; it holds no
adapter, no bus, no protocol object and no UI shape. That restriction is what
keeps the safety kernel testable without any CAN hardware and what keeps it from
acquiring a dependency on whichever device happens to exist later.

The request carries a **digest** of its parameters rather than the parameters
themselves. Two reasons, and the second is the important one:

* the kernel never interprets operation parameters — it decides about risk,
  authority and scope — so it has no use for their contents;
* parameters are where a credential would hide. A security-access key, a seed, a
  token or an unlock payload would travel in exactly this position, and an audit
  trail must never be able to record one (SAFETY-01 §20). Storing a digest makes
  that leak structurally impossible instead of relying on every future caller to
  remember to redact.

``requested_at`` is supplied by the caller because the kernel does not read a
clock while deciding: a decision that consulted the wall clock in one place and
the caller's timestamp in another would be reproducible only by accident, and
"which time did we judge the expiry against?" has to have one answer.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping
from dataclasses import dataclass

from canx.safety.caller import CallerIdentity
from canx.safety.errors import SafetyError
from canx.safety.identifiers import (
    PARAMETERS_DIGEST_ROLE,
    ApprovalId,
    OperationId,
    validate_sha256_digest,
)
from canx.safety.risk import OperationClass
from canx.safety.scope import OperationTarget


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """One request for the safety kernel to authorise.

    ``operation_id`` and ``approval_id`` are **identifiers**, not free text, and
    they are validated at construction (invariant S21). Both reach the audit
    trail, so an unvalidated reference field would be the last place a
    caller-controlled string could travel into the safety record — the exact
    defect SAFETY-01-FIX-2 closes. ``requested_at`` must be finite for the same
    reason from the other direction: a ``NaN`` timestamp is a malformed record,
    and ordering a trail by a value that compares false against everything is not
    ordering it at all.
    """

    operation_id: str
    operation_class: OperationClass
    caller: CallerIdentity
    target: OperationTarget
    requested_at: float
    approval_id: str | None = None
    parameters_digest: str | None = None

    def __post_init__(self) -> None:
        # ``object.__setattr__`` because the dataclass is frozen: the fields are
        # *normalised* to the typed identifier rather than merely checked, so a
        # request that exists cannot hold an unvalidated reference.
        object.__setattr__(self, "operation_id", OperationId(self.operation_id))
        if self.approval_id is not None:
            object.__setattr__(self, "approval_id", ApprovalId(self.approval_id))
        if self.parameters_digest is not None:
            validate_sha256_digest(self.parameters_digest, role=PARAMETERS_DIGEST_ROLE)
        if not math.isfinite(self.requested_at):
            raise SafetyError(
                "An operation request must carry a finite requested_at timestamp.",
                code="safety.invalid_operation",
                details={"field": "requested_at"},
            )

    @staticmethod
    def digest_parameters(parameters: Mapping[str, object]) -> str:
        """Return a stable digest of operation parameters.

        Key order is normalised and the separator is compact so that two
        callers describing the same parameters in a different order produce the
        same digest — a digest that changed with dictionary ordering would be
        useless for correlating two records of one operation.

        The digest is one-way: it lets an audit reader confirm that two requests
        carried the same parameters without ever reading them.
        """
        canonical = json.dumps(
            parameters,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def describe(self) -> dict[str, object]:
        """Return the audit-safe shape of this request.

        Deliberately a projection rather than ``asdict``: the signature of what
        an audit event collects is fixed here, so a field added to this
        dataclass later cannot silently start travelling into the trail.
        """
        return {
            "operation_id": self.operation_id,
            "operation_class": str(self.operation_class),
            "caller_kind": str(self.caller.kind),
            "caller_id": self.caller.caller_id,
            "target": self.target.describe(),
            "requested_at": self.requested_at,
            "approval_id": self.approval_id,
            "parameters_digest": self.parameters_digest,
        }
