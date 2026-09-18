# CI-03 docs-only routing probe (throwaway)

This file exists only to produce a **docs-only** change set on a stacked pull
request, so that the CI-03 tiered quality gate can be observed on real GitHub
Actions: the three domain jobs must be *skipped* and `Quality Gate` must still
report `success`.

The branch that carries it is deleted after the observation; the file never
reaches `main`.

Observed run: see the `CI-03 Completion Report`.
