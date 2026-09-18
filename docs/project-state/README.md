# docs/project-state/ — Historical Project State Archive

> **Role**: Lossless historical record of `docs/PROJECT_STATE.md`.
> **Not** the mandatory startup read. Read only when you need how CAN-X got here.

This directory stores the frozen history of the project state file. The live, mandatory
context file for every agent task is `docs/PROJECT_STATE.md` (compact current state).

## Purpose

`docs/PROJECT_STATE.md` used to be a single growing log that carried every phase's full
implementation record, test stdout, RED→GREEN output and multi-round FINAL remediation
detail. That made every agent startup expensive and made the *current* truth hard to find.

The rule now in force:

```text
Current truth        → docs/PROJECT_STATE.md      (compact, mandatory read)
Detailed history     → docs/project-state/        (this directory, read on demand)
Per-stage evidence   → docs/acceptance/           (see docs/acceptance/README.md)
```

## Contents

| File | Contents |
| --- | --- |
| `PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md` | Byte-for-byte copy of `docs/PROJECT_STATE.md` as it stood at the V0.3-09 close, before the first compaction. Full history through V0.3-09. |
| `PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md` | Byte-for-byte copy of `docs/PROJECT_STATE.md` as it stood at the RELIABILITY-01 close, before the DOC-GOV-01 compaction (2026-09-18). Carries the verbatim §17 (SAFETY-01) and §18 (AGENT-01) review / remediation narrative, the CI and merge records, and every earlier section. |

The archive is intentionally a **small number of lossless files**. Splitting it into per-version
files would add risk of omission, duplication and fact drift for no verifiable gain; a complete
single-file snapshot is preferred over cosmetic decomposition.

## Integrity

Each archive was created by byte copy of the pre-compaction `docs/PROJECT_STATE.md` and verified by
MD5 before any edit to the live file:

```text
PROJECT_STATE_ARCHIVE_THROUGH_V0.3-09.md            md5  df3b73fb1ce51afc00ad0b63a4e84551
PROJECT_STATE_ARCHIVE_THROUGH_RELIABILITY-01.md     md5  f42339a3fda85298616878a9e6e30158
```

No historical text was removed from the repository by a compaction — it was moved here. The sections
that a closed-phase cross-reference targets are quoted by the **same section numbers** they had in
`docs/PROJECT_STATE.md`, so a reference such as `…ARCHIVE_THROUGH_RELIABILITY-01.md §17` resolves to
the verbatim text.

## Convention going forward

When a phase is formally accepted and closed:

```text
working phase section in docs/PROJECT_STATE.md
  → Final Acceptance: PASS
  → full detail moved to docs/project-state/ (or a finalized acceptance report under docs/acceptance/)
  → docs/PROJECT_STATE.md keeps only a concise summary + a reference
```

See §"PROJECT_STATE compaction rule" in `docs/PROJECT_STATE.md`. DOC-GOV-01 (2026-09-18) applied this
rule to the SAFETY-01 / AGENT-01 / RELIABILITY-01 sections that had accumulated in the live file.

## Related

- `docs/PROJECT_STATE.md` — compact current state (mandatory startup read)
- `docs/CONTEXT_INDEX.md` — which authority a given task class must load
- `docs/engineering/AGENT_CONTEXT_GOVERNANCE.md` — the four context layers and the load order
- `docs/acceptance/README.md` — where per-phase acceptance evidence should be written
- `docs/ADR/` — architecture decision records
