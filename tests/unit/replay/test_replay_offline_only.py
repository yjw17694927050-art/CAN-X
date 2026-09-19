"""Offline only: the replay package has no path to a bus.

Real CAN replay needs a transmit path and therefore a safety gate. V0.4-08 is
offline replay: it must not contain one, must not reach one, and must not be able
to grow one by accident. These are negative tests with real assertions — a
structural scan of every module in the package, a textual scan for transmit
vocabulary, and a subprocess that imports the package and checks what it pulled
into the interpreter.
"""

from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import canx.replay

PACKAGE_DIRECTORY = Path(canx.replay.__file__).parent
REPLAY_MODULES = sorted(PACKAGE_DIRECTORY.glob("*.py"))

#: Layers a transmit-capable replay would have to reach: the adapter boundary,
#: the transport/runtime plumbing, the safety kernel and the live capture path.
FORBIDDEN_IMPORT_PREFIXES = (
    "canx.devices",
    "canx.transport",
    "canx.safety",
    "canx.recorder",
    "canx.capture",
    "canx.runtime",
    "canx.api",
    "can",
    "can.interfaces",
    "can.interface",
    "canlib",
)

#: Names that would mean "something is being written to a bus".
FORBIDDEN_TX_NAMES = frozenset({"send", "sendall", "transmit", "send_periodic"})

#: Tokens that must not appear anywhere in the package's source text.
FORBIDDEN_TX_TOKENS = (
    "adapter.send",
    ".send(",
    "send(",
    "python-can",
    "pythoncan",
    "can.Bus",
    "can.interface",
    "Notifier(",
    "tx_gate",
    "safety kernel",
)


def _module_sources() -> dict[Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in REPLAY_MODULES}


def test_the_scan_actually_looks_at_every_module_of_the_package() -> None:
    names = {path.name for path in REPLAY_MODULES}

    assert names == {
        "__init__.py",
        "cancellation.py",
        "clock.py",
        "errors.py",
        "model.py",
        "session.py",
        "sink.py",
        "source.py",
    }


def test_no_module_in_the_package_imports_a_transmit_capable_layer() -> None:
    offenders: list[str] = []
    for path, text in _module_sources().items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            for name in names:
                if any(
                    name == prefix or name.startswith(f"{prefix}.")
                    for prefix in FORBIDDEN_IMPORT_PREFIXES
                ):
                    offenders.append(f"{path.name}: {name}")

    assert offenders == []


def test_no_module_in_the_package_mentions_a_transmit_call() -> None:
    offenders: list[str] = []
    for path, text in _module_sources().items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_TX_NAMES:
                offenders.append(f"{path.name}: .{node.attr}")
            elif (
                isinstance(node, ast.Name)
                and node.id in FORBIDDEN_TX_NAMES
            ):
                offenders.append(f"{path.name}: {node.id}")

    assert offenders == []


def test_no_module_in_the_package_calls_a_send_named_function() -> None:
    offenders: list[str] = []
    for path, text in _module_sources().items():
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.Call):
                continue
            target = node.func
            name = target.attr if isinstance(target, ast.Attribute) else getattr(target, "id", None)
            if name in FORBIDDEN_TX_NAMES:
                offenders.append(f"{path.name}: {name}()")

    assert offenders == []


def test_no_module_in_the_package_contains_transmit_vocabulary() -> None:
    offenders: list[str] = []
    for path, text in _module_sources().items():
        lowered = text.lower()
        for token in FORBIDDEN_TX_TOKENS:
            if token.lower() in lowered:
                offenders.append(f"{path.name}: {token}")

    assert offenders == []


def test_the_public_surface_offers_no_transmit_operation() -> None:
    public = {name for name in dir(canx.replay) if not name.startswith("_")}

    assert public & FORBIDDEN_TX_NAMES == set()
    assert {"ReplaySession", "ReplaySink", "ReplaySource"} <= public


def test_the_scan_would_notice_a_transmit_call_if_one_were_added() -> None:
    # A negative test is worthless if it cannot fail. This is the same walk used
    # above, run over a source that does transmit: it must find it.
    injected = "def emit(self, event):\n    self._adapter.send(event.frame)\n"
    offenders = [
        node.attr
        for node in ast.walk(ast.parse(injected))
        if isinstance(node, ast.Attribute) and node.attr in FORBIDDEN_TX_NAMES
    ]

    assert offenders == ["send"]


def test_importing_the_replay_package_loads_no_device_module() -> None:
    runtime_directory = PACKAGE_DIRECTORY.parents[1]
    script = (
        "import json, sys;"
        " import canx.replay;"
        " print(json.dumps(sorted(m for m in sys.modules"
        " if m == 'can' or m.startswith('can.')"
        " or m.split('.')[0] == 'canx' and m.split('.')[1:2] in"
        " [['devices'], ['transport'], ['safety'], ['recorder'], ['capture']])));"
    )

    completed = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, "PYTHONPATH": str(runtime_directory)},
    )

    assert json.loads(completed.stdout) == []


def test_no_module_builds_a_clock_at_import_time() -> None:
    # The scheduler takes its clock by injection; a module-level clock would make
    # a replay depend on when it was imported instead of on what it was given.
    offenders: list[str] = []
    for path, text in _module_sources().items():
        for node in ast.parse(text).body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = node.value
            else:
                continue
            if isinstance(value, ast.Call) and "Clock" in ast.unparse(value.func):
                offenders.append(f"{path.name}: {ast.unparse(value.func)}")

    assert offenders == []
