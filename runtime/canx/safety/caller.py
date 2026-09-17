"""Who is asking — and what they are *not* allowed to grant themselves.

CAN-X has several kinds of operation requester: a human at the UI, an Agent, a
generated script, an automation rule, and the host runtime itself. They differ
in how much they may be *trusted to ask*, not in what they may *decide*
(invariant S3, S4):

```text
Agent is not a trusted authority.
Script is not a trusted authority.
Automation is not a trusted authority.
```

All of them are requesters. None of them may arm the runtime, issue an approval,
widen its own permission set, extend an approval's expiry or edit policy. The
only two callers with an authority beyond "may ask" are the human operator and
the host system — and even they cannot bypass the kernel; they can only *supply*
the authority the kernel then evaluates.

Because "may ask" is the same for every kind, the interesting surface of this
module is small and negative: :attr:`CallerIdentity.may_issue_approval` and
:attr:`CallerIdentity.may_control_arm` are ``False`` for the three machine
callers, and the kernel refuses those actions for them rather than silently
ignoring the request.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from canx.safety.errors import SafetyCallerError


class CallerKind(StrEnum):
    """The categories of operation requester the kernel distinguishes.

    Extensible by addition: a new kind must be classified as authority-bearing
    or not, and the default for anything unlisted is "not".
    """

    HUMAN_UI = "human.ui"
    AGENT = "agent"
    SCRIPT = "script"
    AUTOMATION = "automation"
    SYSTEM = "system"


#: The only kinds allowed to supply an authority the kernel evaluates. Everything
#: else is a requester, full stop. Written as a frozenset so the check is data,
#: not a chain of ``if``s a future edit could quietly invert.
_AUTHORITY_BEARING_KINDS: frozenset[CallerKind] = frozenset(
    {CallerKind.HUMAN_UI, CallerKind.SYSTEM}
)


@dataclass(frozen=True, slots=True)
class CallerIdentity:
    """A stable, non-secret identity for one operation requester.

    ``name`` is a label for the audit trail — ``"ui.main"``, ``"agent.session-7"``,
    ``"script.cleanup"``. It is deliberately not a credential: the kernel decides
    from ``kind``, and the audit trail records the name so a decision can be
    attributed after the fact. Nothing here is a secret, a token or a key, and
    nothing here is written into an audit event unredacted beyond these two
    fields.
    """

    kind: CallerKind
    name: str

    def __post_init__(self) -> None:
        if not self.name:
            raise SafetyCallerError(
                "A caller identity must carry a non-empty name.",
                details={"kind": str(self.kind)},
            )
        if not isinstance(self.kind, CallerKind):
            raise SafetyCallerError(
                "The caller kind is not part of the CAN-X caller vocabulary.",
                details={"kind": type(self.kind).__name__},
            )

    @property
    def may_issue_approval(self) -> bool:
        """Whether this caller may supply an approval the kernel will evaluate.

        False for Agent, script and automation. This is the structural answer to
        "can an Agent self-approve?" — it cannot, because an approval is only
        accepted from a caller for which this is true (invariant S4).
        """
        return self.kind in _AUTHORITY_BEARING_KINDS

    @property
    def may_control_arm(self) -> bool:
        """Whether this caller may arm or confirm-arm the runtime.

        False for Agent, script and automation: arming is an operator action.
        An agent may *request* a dangerous operation; it may never put the
        runtime into the state that permits one (invariant S3).
        """
        return self.kind in _AUTHORITY_BEARING_KINDS

    @property
    def may_release_emergency_stop(self) -> bool:
        """Whether this caller may release a global emergency stop.

        Engaging the stop is open to every caller — anything that detects danger
        should be able to pull it. Releasing it is an operator action, never a
        machine one (invariant S13).
        """
        return self.kind in _AUTHORITY_BEARING_KINDS
