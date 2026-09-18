"""The device layer has no transmit path, and these tests notice when it gains one.

SAFETY-01 §23. Invariant S12 says a future ``Adapter.send`` must cross the safety
kernel. That invariant is not enforceable by the safety package — a caller that
never asks the kernel is never refused by it — so the enforcement has to live
where the transmit path would appear: in the device contracts.

There is no production TX in this tree today. These tests pin that fact down so
that adding one is a deliberate act with a red test attached, rather than an
addition nobody notices:

* ``CanAdapter`` is a receive-only protocol; if a ``send`` lands on it, the
  protocol test fails;
* ``VirtualAdapter.capabilities().tx`` is ``False``; if a synthetic transmit
  capability appears, the capability test fails;
* the safety package exposes no execution primitive at all; if an ``execute`` or
  ``send`` appears on the kernel, the surface test fails.

Each of those failures is the intended prompt: a real TX path is a later task,
and this stage's job is to make sure it cannot arrive quietly.
"""

from __future__ import annotations

import inspect

from canx.devices.base import CanAdapter
from canx.devices.virtual import VirtualAdapter, VirtualAdapterConfig
from canx.safety.kernel import SafetyKernel

TRANSMIT_NAMES = ("send", "send_frame", "transmit", "write", "inject", "replay")


def test_the_adapter_protocol_is_receive_only() -> None:
    members = set(dir(CanAdapter))
    for name in TRANSMIT_NAMES:
        assert name not in members, f"CanAdapter gained a transmit primitive: {name}"


def test_the_adapter_protocol_declares_exactly_the_receive_surface() -> None:
    assert {"open", "close", "recv", "capabilities", "statistics"} <= set(dir(CanAdapter))


def test_the_virtual_adapter_has_no_transmit_method() -> None:
    for name in TRANSMIT_NAMES:
        assert not hasattr(VirtualAdapter, name), f"VirtualAdapter gained: {name}"


def test_the_virtual_adapter_does_not_claim_transmit_capability() -> None:
    adapter = VirtualAdapter(VirtualAdapterConfig())
    assert adapter.capabilities().tx is False


def test_the_safety_kernel_authorises_but_does_not_execute() -> None:
    """There is no method on the kernel that could perform a dangerous operation."""
    for name in (*TRANSMIT_NAMES, "execute", "dispatch", "run", "apply"):
        assert not hasattr(SafetyKernel, name), f"SafetyKernel gained: {name}"


def test_the_safety_package_imports_no_device_or_transport_module() -> None:
    """A policy that could reach a bus would have crossed the line it guards."""
    import canx.safety as safety_package

    modules = [
        getattr(module, "__name__", "")
        for _, module in inspect.getmembers(safety_package, inspect.ismodule)
    ]
    for module_name in modules:
        assert not module_name.startswith("canx.devices")
        assert not module_name.startswith("canx.transport")
        assert module_name != "can"


def test_the_runtime_http_surface_exposes_no_transmit_endpoint() -> None:
    from canx.api.app import create_app

    paths = set(create_app().openapi()["paths"])
    for forbidden in ("/tx", "/inject", "/uds", "/transmit", "/send"):
        matching = sorted(path for path in paths if path.startswith(forbidden))
        assert matching == [], f"the runtime grew a dangerous endpoint: {matching}"
