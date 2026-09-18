# ADR 0002: Parallel Development, Serial Integration

- Status: Accepted
- Date: 2026-09-18
- Decision owners: CAN-X sole author and repository integration policy
- Scope: AGENT-01 (Multi-Agent Orchestration Foundation)

## Context

AGENT-01 moves CAN-X toward one Main Agent coordinating up to four Sub-Agents.
Development is meant to happen in parallel; integration must not lose ownership
discipline, contract integrity, test evidence or integration safety.

The protected-integration gate (`docs/engineering/INTEGRATION_POLICY.md`,
Maintenance CI-02) requires a green `Quality Gate` on every PR into `main`. Its
ruleset is `main-protected-integration` (id `23600372`, `enforcement: active`,
`bypass_actors: []`), and it declares:

```text
required_status_checks
  required_status_checks: [ { context: "Quality Gate" } ]
  strict_required_status_checks_policy: false
```

`strict_required_status_checks_policy: false` is the gap this ADR exists to
close. With it off, two PRs can each be green on their own base and still be
jointly broken:

```text
PR A green on main@X     →  merges
PR B green on main@X     →  still green by the platform's standard, merges
                              main@Z = A + B, never once built together
```

CI-02 recorded this as one of its two non-blocking P2 items. At one agent it is
a latent nuisance; at one-plus-four it becomes a routine occurrence, because
four branches are in flight at the same time and each one is green on a base
that the others are moving.

The question AGENT-01 must answer:

> How does CAN-X prevent individually-green but jointly-broken parallel PRs?

## Decision

**Parallelise development. Serialise integration. Make the precondition a
machine predicate, not a habit.**

1. **No merge queue, no integration branch, no custom scheduler.** CAN-X keeps
   the existing protected workflow: branch → PR → `Quality Gate` → merge.
2. **Integration is serial by protocol.** The Main Agent merges one PR at a
   time and does not run a second integration while the first is in flight.
3. **Every handoff must be based on the current integration head.**
   `tools/agent/validation.py:check_base` refuses a handoff whose `base_sha` is
   not the integration head, with `agent.base_stale`. This is the executable form
   of "update onto latest, re-run CI, then merge" — it is a returned error code,
   not an instruction in a prompt.
4. **The whole blocker set is reported at once.** `evaluate_integration`
   returns every reason a handoff may not be integrated, so the loop converges
   in one pass instead of one blocker per round trip.
5. **The PR must be green on that head.** A green run on an older base is not
   evidence about the current one (`INTEGRATION_POLICY.md` §9).

AGENT-01-FIX-1 tightened point 3 from "the two shas must be equal" to a pair of
facts the repository has to agree with:

```text
handoff.base_sha == task.base_sha          a handoff may not invent a base
handoff.base_sha == integration head       the stale-base rule above
task.base_sha is an ancestor of the head   proved with git merge-base, not inferred
```

The first is what makes "typing the current `main` into the handoff JSON"
insufficient: the claim is checked against the task contract *and* against real
history, in `tools/agent/validation.py:check_base` and
`tools/agent/evidence.py:collect_repository_evidence` respectively.

FIX-1 also made the boundary between the two gates explicit rather than implied.
The local verdict proves what it can prove (handoff schema, Git-backed evidence,
ownership, base/current-head, dependency completion, conflict state, task
readiness) and reports `github_gate.checked_here = false`; the GitHub Ruleset
independently requires `Quality Gate`. Merge eligibility needs both. No GitHub
client was added to the tooling to blur that line.

The rules, in order:

```text
PR must be updated onto the current integration base
        ↓
full CI re-runs on the updated head
        ↓
only then merge — one PR at a time
```

## Alternatives considered

### A. Enable `strict_required_status_checks_policy` (the platform backstop)

GitHub can enforce "the branch must be up to date with the base branch before
merging". It is the only option that removes the reliance on the Main Agent
following the protocol, and it is complementary to the decision above rather
than an alternative to it.

Not applied inside AGENT-01, deliberately. Editing the ruleset is a repository
configuration change on the same surface that CI-02 declared the break-glass
path; `AGENT-01 §82` requires such a change to be its own documented, tested
change with a verified negative case. Bundling it into a phase that adds no
product code would make the phase's own acceptance depend on a live
administrative action, and would make the AGENT-01 PR itself subject to a
just-changed merge rule.

**Recorded as the recommended follow-up**, with the verification it needs:

```text
change      set strict_required_status_checks_policy = true on ruleset 23600372
verify (+)  a PR that is behind main is blocked with "the branch is not up to date"
verify (-)  a PR that is up to date is not blocked for that reason
verify      a required check still reports and still blocks on failure
revert      if any of the above does not hold
```

### B. A long-lived integration branch

An `integration` branch that PRs target, merged into `main` periodically.

Rejected. It converts one protected branch into two, and the second is normally
the less protected one; it delays the feedback rather than making it joint; it
adds a second stale-base problem at the `integration → main` boundary; and it
weakens the property CI-02 established, that the branch a release would be cut
from is the branch the gate protects.

### C. Merge queue / combined-head validation

GitHub's merge queue builds a temporary combined head for each queued PR and
runs CI on it.

Rejected **for now**. It solves exactly the stated problem, but CAN-X is a
sole-author repository whose current integration load is a handful of PRs per
phase; a merge queue optimises merge throughput that does not exist yet
(`AGENT-01 §61`). If parallel development makes serial integration the
bottleneck, this is the upgrade to revisit — it is a platform feature, not a
custom build, so choosing it later costs configuration rather than engineering.

### D. Trust the protocol without a machine check

Document "always rebase before merging" in the policy and leave it there.

Rejected. This is the option the whole phase exists to avoid: a rule that is
only prose is a rule that holds until an agent is tired. `check_base` is the
difference between a policy and a predicate.

## Consequences

- **Development throughput is unaffected.** Four Sub-Agents can still be in
  flight simultaneously; only the merge step is serialised.
- **A stale PR costs a rebase and a CI re-run**, not a silent bad merge. That is
  the intended trade: latency at integration in exchange for `main` staying
  individually *and* jointly green.
- **The stale-base rule is enforced before review, not after.** A Sub-Agent whose
  work has drifted learns it from an error code rather than from a reviewer.
- **The Main Agent becomes the serialisation point.** Its integration duty is
  now load-bearing, which is why it is stated in the protocol
  (`docs/engineering/MULTI_AGENT_PROTOCOL.md` §9) and implemented in
  `tools/agent/validation.py`.
- **`strict_required_status_checks_policy: false` remains a recorded gap.** It is
  mitigated, not closed. Closing it is the follow-up above, on its own change.
- **Post-merge CI is still the last line of defence.** If a genuinely combined
  breakage reaches `main`, `INTEGRATION_POLICY.md` §8 applies: fix immediately or
  revert immediately.

## Alternatives not revisited here

`AGENT-01` deliberately does not build a merge queue, an integration branch, a
scheduler or any orchestration service. The decision above is expressed as one
comparison (`base_sha == integration_head`) plus a serialisation discipline,
because that is the smallest thing that answers the question.
