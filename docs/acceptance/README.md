# docs/acceptance/ — Per-Phase Acceptance Evidence

> **Role**: Home for the detailed acceptance evidence of each formally completed development
> phase. This directory is created by the *Maintenance — PROJECT_STATE Documentation
> Compaction* task as a **policy stub**.
>
> It intentionally contains no reconstructed historical reports. See "Do not back-fill"
> below.

## Purpose

`docs/PROJECT_STATE.md` is being kept as a compact **current-state** file. The long-form
material that used to accumulate there — full test output, RED→GREEN logs, packaging
numbers, multi-round FINAL remediation detail, per-finding verdicts — belongs in an
acceptance report per phase, not in the mandatory startup document.

## Rule (in force from V0.3-10 onward)

When a phase completes:

```text
detailed tests / RED→GREEN / packaging / known issues / acceptance evidence
  → docs/acceptance/<phase>.md        (one file per formally completed phase)

current status + concise summary + a link to the report
  → docs/PROJECT_STATE.md
```

Suggested report file name:

```text
docs/acceptance/<phase-id>-<slug>.md
e.g. docs/acceptance/v0.3-10-read-only-dbc-workspace-ui-foundation.md
```

A report should record, honestly and with its evidence:

```text
Phase / step id and objective
Final Acceptance verdict (PASS / NOT PASS) and who gave it
Scope actually implemented vs deferred
Tests run, with real command output / counts
RED → GREEN evidence where a defect was fixed
Packaging / packaged-runtime / E2E evidence
Known limitations and NOT VERIFIED items
Independent acceptance conclusion and its source (project owner vs agent)
```

## Verdict attribution

An agent does **not** grant its own phase PASS/CLOSED. A report may record an acceptance
verdict only when it is traceable to an external, verifiable source (the project owner's
explicit instruction). Otherwise the status is `Awaiting independent acceptance` /
`Implemented but not verified`.

## Do not back-fill

This task deliberately does **not** reconstruct one acceptance file per historical phase
(V0.1 → V0.3-09). Manually re-deriving a dozen reports from memory invites fact drift and
adds no verifiable value.

The complete historical record already exists losslessly in:

```text
docs/project-state/PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md
```

plus the phase-specific reports already in the repository (for example
`docs/V0.1.1_ACCEPTANCE_REPORT.md`, `docs/V0.1_VALIDATION_REPORT.md`,
`docs/V0.1_TECH_VALIDATION.md`, `docs/V0.1.1_HANDOFF_AUDIT.md`).

Historical phase records may be migrated into this directory later, on demand, when a
concrete task requires it — not as a speculative bulk rewrite.

## Related

- `docs/PROJECT_STATE.md` — compact current state (mandatory startup read)
- `docs/project-state/README.md` — the lossless history archive
- `docs/ADR/` — architecture decision records
