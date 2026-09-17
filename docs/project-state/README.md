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

The archive is intentionally a **single lossless file**. Splitting it into per-version
files would add risk of omission, duplication and fact drift for no verifiable gain; a
complete single-file archive is preferred over cosmetic decomposition.

## Integrity

The archive was created by byte copy of the pre-compaction `docs/PROJECT_STATE.md` and
verified by MD5 before any edit to the live file:

```text
md5  df3b73fb1ce51afc00ad0b63a4e84551
```

No historical text was removed from the repository by the compaction — it was moved here.

## Convention going forward

When a phase is formally accepted and closed:

```text
working phase section in docs/PROJECT_STATE.md
  → Final Acceptance: PASS
  → full detail moved to docs/project-state/ (or a finalized acceptance report under docs/acceptance/)
  → docs/PROJECT_STATE.md keeps only a concise summary + a reference
```

See §"PROJECT_STATE compaction rule" in `docs/PROJECT_STATE.md`.

## Related

- `docs/PROJECT_STATE.md` — compact current state (mandatory startup read)
- `docs/acceptance/README.md` — where per-phase acceptance evidence should be written
- `docs/ADR/` — architecture decision records
