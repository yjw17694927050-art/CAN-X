# CAN-X direct dependency review

> **Reviewed**: 2026-09-15  
> Exact versions are pinned in lockfiles. Transitive licenses must also pass automated review before release.

| Dependency | Version | Purpose | License | V0.1 review |
|---|---:|---|---|---|
| React / React DOM | 19.3.0 | Desktop UI | MIT | Required by SPEC; mature and cross-platform in the webview. |
| Dockview | 8.3.1 | Dock workspace | MIT | Required by SPEC; contained to workspace UI. |
| Zustand | 5.0.15 | UI-only state | MIT | Required by SPEC; not used for frame history. |
| TanStack Query | 5.102.8 | Runtime/control state | MIT | Required by SPEC. |
| TanStack Virtual | 3.14.13 | Trace viewport | MIT | Required by SPEC. |
| i18next / react-i18next | 26.4.2 / 17.0.14 | UI translations | MIT | Required by SPEC. |
| ECharts | 6.1.0 | Canvas plot prototype | Apache-2.0 | Required by SPEC; renderer boundary remains internal. |
| @msgpack/msgpack | 3.1.3 | Worker-side binary decode | ISC | Small, permissive MessagePack implementation. |
| Tauri API / CLI | 2.11.1 / 2.11.4 | Desktop system layer | Apache-2.0 OR MIT | Required by SPEC; Rust business logic remains excluded. |
| Vite | 8.3.0 | Frontend build | MIT | Node 24 environment satisfies its engine range. |
| TypeScript | 6.0.3 | Strict frontend types | Apache-2.0 | Chosen below the `<6.1.0` peer ceiling of typescript-eslint 8.70.0. |
| Vitest | 5.0.0 | Frontend unit tests | MIT | Shares the Vite pipeline. |
| ESLint / typescript-eslint | 10.10.0 / 8.70.0 | Static checks | MIT | Resolved peer ranges are compatible with TypeScript 6.0.3. |
| Testing Library / jsdom | 16.3.3 / 30.0.1 | UI behavior tests | MIT | Test-only. |

Python and Rust direct dependency versions and licenses are added when their lock-resolved manifests are created.

## Python and Rust

| Dependency | Version | Purpose | License | V0.1 review |
|---|---:|---|---|---|
| FastAPI | 0.141.1 | HTTP/WebSocket interface | MIT | Interface layer only; domain remains independent. |
| Uvicorn | 0.53.0 | Headless ASGI server | BSD-3-Clause | Mature ASGI runtime. |
| Pydantic | 2.13.5 | Boundary validation | MIT | Used at API boundaries, not as the canonical domain dependency. |
| msgpack | 1.2.2 | Runtime binary codec | Apache-2.0 | Compact binary protocol implementation. |
| python-can | 4.6.1 | Future physical CAN adapter base | LGPL-3.0-only | Required by SPEC; dynamic Python dependency, no source copied. Distribution obligations require release review. |
| psutil | 7.2.2 | Best-effort process telemetry | BSD-3-Clause | Returns unavailable explicitly when unsupported. |
| pyarrow | 21.0.0 | Parquet segment writer/reader for the V0.2-02 data persistence foundation | Apache-2.0 | Added in V0.2-02 as a single direct Parquet engine; cp313 win_amd64 wheel verified in the project `.venv`. No pandas/Polars/fastparquet introduced alongside it. |
| pytest / pytest-asyncio / httpx | 9.1.1 / 1.4.0 / 0.28.1 | Python tests | MIT | Test-only. |
| mypy / Ruff | 2.3.1 / 0.16.7 | Python static checks | MIT | Development-only. |
| Tauri / tauri-build | 2.11.5 / 2.6.3 | Desktop shell and build | Apache-2.0 OR MIT | Tauri 2 is required; 3.0 alpha is intentionally excluded. |
| UUID | 1.26.1 | OS-backed ephemeral sidecar session tokens | Apache-2.0 OR MIT | V4 generation only; tokens are never logged. |

Direct versions are confirmed by `pnpm-lock.yaml`, installed Python distribution metadata, and `Cargo.lock`. A transitive dependency license/security audit remains required before distribution.
