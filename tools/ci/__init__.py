"""CI control-plane tooling for CAN-X.

Standard-library only, and *not* part of the shipped Runtime: nothing here is
imported by ``canx`` and nothing here is packaged into the wheel
(``pyproject.toml`` packages only ``runtime/canx``).

``classify_changes`` decides which validation jobs a change requires;
``evaluate_gate`` turns that decision plus the job results into the pass/fail
verdict of the required ``Quality Gate`` check.
"""
