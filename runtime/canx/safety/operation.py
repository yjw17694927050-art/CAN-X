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
from collections.abc import Mapping
from dataclasses import dataclass

from canx.safety.caller import CallerIdentity
from canx.safety.errors import SafetyError
from canx.safety.risk import OperationClass
from canx.safety.scope import OperationTarget


@dataclass(frozen=True, slots=True)
class OperationRequest:
    """One request for the safety kernel to authorise."""

    operation_id: str
    operation_class: OperationClass
    caller: CallerIdentity
    target: OperationTarget
    requested_at: float
    approval_id: str | None = None
    parameters_digest: str | None = None

    def __post_init__(self) -> None:
        if not self.operation_id:
            raise SafetyError(
                "An operation request must carry a non-empty identifier.",
                code="safety.invalid_operation",
                details={},
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
            "caller_name": self.caller.name,
            "target": self.target.describe(),
            "requested_at": self.requested_at,
            "approval_id": self.approval_id,
            "parameters_digest": self.parameters_digest,
        }
