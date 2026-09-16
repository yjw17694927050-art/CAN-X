# CAN-X

CAN-X is an **Agent-native Professional CAN Engineering Workbench**.

The repository is in the V0.1 Technology Proof phase. Its purpose is to validate the separation and data path described in [PRD.md](PRD.md), [SPEC.md](SPEC.md), and [docs/V0.1_TECH_VALIDATION.md](docs/V0.1_TECH_VALIDATION.md).

## Development prerequisites

- Windows development host
- Node.js 24 and pnpm 11
- Python 3.13
- Rust stable toolchain

## Standard checks

```powershell
pnpm install --frozen-lockfile
pnpm test
pnpm lint
pnpm typecheck
pnpm build

python -m pip install -e ".[dev]"
python -m pytest
python -m ruff check .
python -m mypy runtime

pnpm tauri build
```

On the current Windows development host, use `scripts\\rust-check.cmd` and `scripts\\tauri-build.cmd` so the Visual C++ environment is loaded consistently.

## Runtime control plane

The headless Runtime exposes `/health`, `/runtime/status`, `/capture/start`, `/capture/stop`, `/metrics`, `/stream/frames`, `/tools/execute`, and the authenticated desktop-only `/runtime/shutdown` route on loopback. V0.1 registers only the read-only `trace.summary` Agent tool.

The reproducible benchmark entrypoints are `tools/benchmarks/v01_pipeline.py` for one configuration and `tools/benchmarks/v01_matrix.py` for the documented 90-point matrix. Raw output belongs under the ignored `benchmark-results/` directory.

Physical CAN hardware and macOS are not available in the V0.1 validation environment. No compatibility claim is made without a real test result.
