# CAN-X — Project State & Long-Term Development Context

> **Document**: `docs/PROJECT_STATE.md`  
> **Purpose**: Cross-session / cross-agent project handoff  
> **Updated**: 2026-09-16  
> **Current Phase**: V0.3 — Professional Trace & DBC Foundation · Step V0.3-03 DBC Project Registry & Persistence Foundation
> **Project Owner**: CAN-X sole author  
> **Development Model**: Document-Driven Development

---

# 1. Project Identity

正式项目名：

# CAN-X

产品定位：

> **Agent-native Professional CAN Engineering Workbench**

CAN-X 的目标不是开发一个普通 CAN Viewer，也不是在传统 CAN 软件中增加一个 AI Chat 页面。

最终目标是：

> 建立一个 CAN 工程师能够高强度、长期日常使用的专业工程工作台，在同一工程上下文中完成采集、Trace、DBC、Plot、诊断、回放、自动化、脚本、测试以及 Agent 驱动的工程分析。

长期产品形态：

```text
Professional CAN Workbench
+
Engineering Automation Runtime
+
CAN Engineering Agent
+
Engineering Memory
```

---

# 2. Relationship with CAN-Space

CAN-X 是全新项目。

CAN-Space 已进入：

**Frozen Reference State**

CAN-X：

- 不是 CAN-Space V2；
- 不是 CAN-Space 重命名；
- 不是 CanLab 的继续二次开发；
- 不继承原 PyQt 应用架构。

CAN-Space 仅用于：

- 功能参考；
- 算法参考；
- 协议实现参考；
- 行为对照；
- legacy code candidate。

任何实际迁入 CAN-X 的旧代码都必须：

```text
Inspect
→ License check
→ Dependency check
→ Remove UI coupling
→ Test
→ Record in REUSE_LEDGER
→ Minimal reuse
```

未经审计不得整目录复制。

---

# 3. Source of Truth

CAN-X 使用 Document-Driven Development。

文档职责：

```text
PRD.md
= 产品目标、范围、用户和版本方向

SPEC.md
= 技术架构、数据结构、安全边界、技术约束

AGENTS.md
= AI / Codex / Harness 开发执行规则

docs/PROJECT_STATE.md
= 当前阶段、长期路线、最近验收状态和跨 Agent 交接
```

如果代码与 SPEC 冲突：

默认修改代码。

如果需求改变：

先修改 PRD。

如果架构改变：

先修改 SPEC 或创建 ADR。

---

# 4. Development Philosophy

开发循环固定为：

```text
Requirement
↓
Design
↓
Code
↓
Unit Test
↓
Integration Test
↓
Performance / Safety Validation
↓
Documentation
↓
Independent Review
```

禁止：

```text
大量编码
→ 最后一次性测试
```

每个阶段只推进一个 coherent increment。

---

# 5. Core Architecture

当前架构原则已经冻结：

```text
UI
≠
CAN Runtime
≠
Agent Runtime
≠
Storage
```

总体架构：

```text
┌────────────────────────────────┐
│ CAN-X Desktop                  │
│                                │
│ Tauri 2                        │
│ ┌────────────────────────────┐ │
│ │ React + TypeScript UI      │ │
│ │                            │ │
│ │ Trace / DBC / Plot         │ │
│ │ Diagnostics / Script       │ │
│ │ Replay / Test / Agent      │ │
│ └────────────┬───────────────┘ │
└──────────────┼─────────────────┘
               │
       Control │ Realtime Data
               │
┌──────────────▼─────────────────┐
│ Python CAN Runtime             │
│                                │
│ Device Manager                 │
│ Capture Engine                 │
│ Protocol Engine                │
│ DBC Engine                     │
│ Recorder                       │
│ Replay Engine                  │
│ Query Engine                   │
│ Automation Runtime             │
└────────┬──────────────┬────────┘
         │              │
┌────────▼───────┐ ┌────▼──────────────┐
│ Data Engine    │ │ Agent Runtime     │
│ SQLite         │ │ Planner           │
│ Parquet        │ │ Tool Registry     │
│ DuckDB         │ │ Permission Engine │
│ Arrow etc.     │ │ Memory            │
└────────────────┘ │ Task Runtime      │
                   │ Python Sandbox    │
                   │ Audit Log         │
                   └───────────────────┘
```

---

# 6. Technology Baseline

Desktop：

```text
Tauri 2
Rust
```

Frontend：

```text
React 19
TypeScript
Vite
Dockview
Zustand
TanStack Query
TanStack Virtual
ECharts
Monaco Editor
i18next
```

Runtime：

```text
Python 3.13
FastAPI
python-can
cantools
```

IPC：

```text
Control Plane
HTTP + JSON

Realtime Data Plane
WebSocket + Binary MessagePack batches
```

Data：

```text
SQLite
= metadata / project state

Parquet
= large datasets

DuckDB
= analytical queries
```

Rust 初期只用于：

- Tauri；
- desktop lifecycle；
- process management；
- OS integration；
- packaging；
- 经 profiling 证明的性能热点。

禁止全 Rust 重写 CAN Runtime。

---

# 7. Performance Principles

优先级：

```text
1 CAN RX integrity
2 Recorder integrity
3 Timestamp integrity
4 Safety
5 Runtime responsiveness
6 UI interaction
7 Trace refresh
8 Plot refresh
9 Decorative animation
```

UI 高负载时可以：

- 降低 Trace refresh；
- Plot downsample；
- 减少动画。

不得因为 UI 卡顿停止或污染后台采集。

React 不保存完整 CAN 历史。

实时链路应遵循：

```text
CAN
→ Runtime
→ batching
→ WebSocket
→ Worker
→ bounded frontend store
→ virtualized Trace / Plot
```

---

# 8. Data Scale

V1 目标：

**10–50 GB 工程数据稳定处理**

架构长期预留：

**100 GB+**

禁止：

```text
entire log → pandas DataFrame
```

禁止：

```text
all frames → React state
```

大型数据必须：

- streaming；
- chunking；
- indexing；
- query-on-demand；
- downsampling。

---

# 9. Timestamp Architecture

Frame 必须保留：

```text
hardware_timestamp
host_timestamp
normalized_timestamp
clock_domain
timestamp_quality
```

长期建立统一 Timeline。

未来需要支持时间同步：

```text
CAN
CAN FD
LIN
DoIP
Automotive Ethernet
Video
Sensor
```

---

# 10. Hardware Strategy

Hardware abstraction：

```text
CAN-X CanAdapter
        ↓
python-can
        ↓
supported vendor interfaces
```

必要时增加：

```text
VectorAdapter
PcanAdapter
KvaserAdapter
ZlgAdapter
PandaAdapter
...
```

Vendor-specific Adapter 只用于 python-can 未暴露的能力，例如：

- hardware timestamp；
- hardware sync；
- advanced trigger；
- vendor-specific statistics。

Windows：

主要实时硬件验证平台。

macOS：

完整软件分析能力 + 经验证硬件子集。

当前：

- 无真实 CAN 硬件；
- 无 Mac 真机。

不得声称相关兼容性已验证。

---

# 11. Professional Workspace Goal

UI 定位：

> Modern lightweight shell + professional high-density workspace

主要体验：

- Dock；
- Split；
- Float；
- Pop-out；
- Multi-window；
- Multi-monitor；
- Layout Save；
- Layout Restore；
- Workspace Preset。

长期工作区：

```text
Trace
DBC
Plot
Diagnostics
Replay
Automation
Script
Test
J1939
Bus Monitor
Agent
```

---

# 12. Agent North Star

CAN-X 最终不是 Chatbot。

Agent 应通过结构化 Tool 操作工程系统。

例如：

```text
trace.query
trace.filter
dbc.decode
dbc.create_signal
plot.create
uds.request
replay.start
test.run
script.execute
project.search
memory.query
```

Agent 工作示例：

```text
用户：
找出踩油门时最可能变化的 CAN 信号。

Agent：
1 读取数据
2 识别事件窗口
3 找 CAN ID
4 bit-level 分析
5 周期分析
6 相关分析
7 创建候选信号
8 自动生成 Python
9 Sandbox 验证
10 创建 DBC 草稿
11 创建 Plot
12 输出证据和置信度
```

---

# 13. Agent Autonomy

模式：

# Semi-Autonomous Agent

允许自动执行：

```text
READ
COMPUTE
WRITE_PROJECT
```

高风险动作：

```text
CAN TX
Injection
UDS Write
ECU Reset
Routine Control
Security Access
Flashing
Fuzzing
```

必须经过：

```text
Tool Request
↓
Permission Engine
↓
ARM State
↓
Approval Gate
↓
Execution
↓
Audit Log
```

安全必须由 Runtime 执行。

不得只依赖 UI。

---

# 14. Agent Runtime Long-Term Architecture

```text
AgentRuntime
├── Planner
├── ToolRegistry
├── ToolExecutor
├── PermissionEngine
├── ApprovalGate
├── TaskRuntime
├── Checkpoint
├── AuditLog
├── ProjectMemory
├── PersonalEngineeringMemory
├── PythonSandbox
└── ModelProvider
```

Agent 支持：

```text
Plan
→ Execute
→ Observe
→ Re-plan
→ Continue
```

并支持：

- pause；
- resume；
- retry；
- checkpoint；
- task history；
- failure recovery。

---

# 15. Python Script / Automation Goal

长期包含专业 Script Workspace：

```text
Monaco Editor
Python
autocomplete
diagnostics
runtime output
test results
Agent generated code
Agent code editing
Sandbox execution
```

Python Sandbox 与 Device Control 是两个不同安全域。

Sandbox 不持有真实 CAN Device Handle。

---

# 16. AI Models

必须支持：

```text
Cloud Models
+
Local Models
```

通过统一：

```text
ModelProvider
```

抽象。

长期可支持：

- cloud providers；
- OpenAI-compatible API；
- Ollama；
- local models。

敏感工程数据不能由 Agent 自行决定发送到云端。

---

# 17. Commercial Direction

长期倾向：

```text
CAN-X Community
CAN-X Professional
CAN-X Agent
CAN-X Enterprise
```

方向：

**Open Core + Professional / Agent commercial layer**

但 V0.x 不开发：

- account system；
- subscription；
- license server；
- cloud sync。

只预留边界。

---

# 18. Version Development Roadmap

## V0.1 — Technology Proof

目标：

验证基础架构。

已完成核心能力：

```text
Tauri
React
Python Runtime
Virtual CAN
Frame / FrameBatch
Capture
Recorder
MessagePack
WebSocket
Worker
Virtualized Trace
Basic Plot
Tool Registry
trace.summary
Benchmark
```

状态：

# Conditional Pass

---

## V0.1.1 — Acceptance Hardening

当前下一阶段。

目标不是增加业务功能。

重点：

```text
Recorder backpressure P0            ✅
Shared frontend realtime stream     ✅
Recorder saturation tests           ✅
Recorder soak tests                 ✅
Packaged Python runtime proof       ✅
Desktop smoke test                  ◑ runtime verified; window NOT VERIFIED
Performance instrumentation         ◑ worker decode measured; UI/runtime latency NOT VERIFIED
Documentation consistency           ✅
```

V0.1.1 implementation is complete; see `docs/V0.1.1_ACCEPTANCE_REPORT.md` for evidence.

独立验收（2026-09-16）：

```text
V0.1.1 independent acceptance:
Conditional PASS
Approved to enter V0.2
```

三项 PARTIAL / NOT VERIFIED（windowed desktop launch、runtime→WebSocket 与 Trace/Plot 延迟、
真实 CAN 硬件、macOS）状态未变，且与本增量无关。

---

## V0.2 — Runtime & Data Foundation

### Step V0.2-01 — Project Foundation

建立了正式 Project Model、工程目录结构与 SQLite project metadata 生命周期基础：

```text
Project Model                 ✅ runtime/canx/project/{model,errors}.py
Project manifest              ✅ project.json — deterministic UTF-8 JSON
SQLite project metadata       ✅ project.db — PRAGMA user_version = 1
Project lifecycle             ✅ create / open / close / reopen
Identity survives reopen      ✅ verified
Existing paths protected      ✅ create never overwrites user data
Corruption detected           ✅ malformed / unsupported / identity mismatch rejected
```

独立验收结论：**Conditional PASS**（两个 P1 待修）。定向修复（V0.2-01-FINAL）已完成：

```text
P1-A  create() 泄漏裸 ValueError，且放行纯空白 display_name
      → ProjectValidationError，code = project.invalid_display_name（strip 后为空即拒绝）
P1-B  close() 在 SQLite 确认关闭前就把 handle 置为 closed，失败时连接引用丢失
      → 仅 close 成功才置 closed；失败抛 project.close_failed，handle 保持 open 且保留连接
```

本机验证（2026-09-16，V0.2-01 修复后当时重新执行）：`pytest` **157 passed**、
`ruff check runtime tests tools` exit 0、`mypy runtime` exit 0（35 source files）。

当前状态：

```text
V0.2-01 Project Foundation
Implementation complete
Independent acceptance: Conditional PASS
Final remediation completed
V0.2-01 Final Acceptance: PASS
```

未实现（刻意留在 V0.2 后续增量）：DuckDB、Query Service、UI。

---

### Step V0.2-02 — Data Session & Parquet Segment Persistence

建立了正式的数据会话与 Parquet 分段持久化基础：

```text
SQLite schema v2                ✅ project.db: data_sessions + data_segments
v1 → v2 migration               ✅ in place, transactional, identity preserved
Data domain model               ✅ runtime/canx/data/{errors,model}.py
Session lifecycle               ✅ start / append / finalize / reopen
Bounded segment writer          ✅ max_frames_per_segment flush, bounded working set
Atomic segment commit           ✅ *.parquet.tmp → successful close → os.replace
SQLite-after-file ordering      ✅ registration failure advances no counter
Canonical Frame Parquet         ✅ FRAME_PARQUET_SCHEMA_VERSION = 1 + canx.* metadata
Explicit recovery               ✅ ACTIVE → INTERRUPTED, never on project open
Integrity inspection            ✅ temporary / orphan / missing / size mismatch
Relative paths only             ✅ project-relative, path-escape guarded
```

固定语义：

```text
frame_count / segment_count     只统计已落盘 segment；缓冲中的帧不计
empty session                   允许：start → 无帧 → finalize → COMPLETED，计数为 0
sequence 完整性                 跨 batch 必须严格递增；回退/重叠 → DataIntegrityError
timestamp 完整性                跨 batch 归一化时间戳不得倒退；批量内首 <= 尾
max_frames_per_segment          默认 65536（foundation 常量，不是 SPEC 值）
integrity inspection            只检测与报告，从不自动修复或删除
recovery                        必须显式触发；ProjectService.open() 不改动 session
published segment name          只可能出现在成功 os.replace 之后
```

新增直接依赖：

```text
pyarrow == 21.0.0
  · cp313 win_amd64 wheel 已在本机 .venv 实测安装并读写通过
  · 单一 Parquet 引擎；未引入 pandas / Polars / fastparquet
  · 打包 runtime 尚未引用数据模块 → Parquet 打包执行路径 NOT VERIFIED（见下）
```

本机验证（2026-09-16，V0.2-02 实现完成时）：

```text
focused pytest (unit/project + unit/data + integration)   237 passed
full pytest                                               322 passed
ruff check runtime tests tools                            exit 0
mypy runtime                                              exit 0 (42 source files)
Parquet smoke                                             50,000 frames / 10 segments / clean
scripts\package-windows.cmd                               exit 0
  · runtime build + staged sidecar                         PASS
  · packaged-runtime smoke (canx-runtime.exe)              PASS (1 passed)
  · Tauri MSI build + artifact check                       PASS (CAN-X_0.1.0_x64_en-US.msi)
Packaged Parquet execution path                           NOT VERIFIED
  (canx.data 不在打包 import graph 内；PYZ TOC 中 pyarrow 出现 0 次)
```

独立验收结论：**Conditional PASS**（两项待修，见下）。定向修复（V0.2-02-FINAL）已完成：

```text
A  start() 失败原子性
   session 目录先行创建，再登记 SQLite 行；任一步失败都只清理本次新建的
   data/sessions/<session_id>，并抛 typed DataStorageError
   （data.session.directory_create_failed / data.session.start_failed）。
   OSError / PermissionError / sqlite3.Error 不再外泄，
   失败后既不留 ACTIVE 会话，也不留半创建的会话目录。

B  V2 schema 完整性
   仅有 user_version = 2 不再足够：create_database / migrate / open 三处都校验
   data_sessions 与 data_segments 存在且具备本版本所需列。损坏的 V2 在
   ProjectService.open() 即被拒（project.database_schema_invalid，
   details 含 missing_tables / table / missing_columns），
   而不是等到 DataSessionService 被调用才暴露。
```

定向修复后本机验证（2026-09-16，重新执行）：

```text
focused pytest (unit/data + test_storage_migration + test_project_v1_to_v2)
                                                          157 passed
full pytest                                               335 passed
ruff check runtime tests tools                            exit 0
mypy runtime                                              exit 0 (42 source files)
packaged-runtime smoke                                    PASS (1 passed，单独重跑)
Packaged Parquet execution path                      仍为 NOT VERIFIED
  本次未扩大 packaged import graph；canx.project 与 canx.data 都不在其中
```

状态：

```text
V0.2-02 Data Session & Parquet Segment Persistence
Implementation complete
Independent acceptance: Conditional PASS
Final remediation completed
V0.2-02 Final Acceptance: PASS
```

本阶段刻意未进入：DuckDB、Query Service、SQL query API、Trace/Plot historical
query、Recorder capture-pipeline migration、WebSocket / frontend 改动、
50GB benchmark。

主要目标：

建立正式 Runtime/Data 基础。

预计包括：

```text
Project Model
SQLite project metadata
Project lifecycle
Data session model
Parquet data segments
DuckDB query layer
Query Service
Large-data indexing
Session metadata
Data import/export boundaries
Runtime persistence
```

重点验证：

```text
large dataset query
bounded memory
project recovery
long-running capture data organization
```

V0.2 不急于开发大量协议功能。

---

### Step V0.2-03 — DuckDB Query Foundation & Bounded Historical Query Service

建立了 CAN-X 第一版正式历史数据 Query Foundation：

```text
Project
→ Data Session
→ registered Parquet segments
→ DuckDB
→ structured bounded query
→ typed Frame results / aggregate results
```

实现清单：

```text
DuckDB direct dependency          ✅ duckdb == 1.5.5（cp313 win_amd64 wheel 本机实测）
Query domain models               ✅ runtime/canx/query/model.py
                                    不 import duckdb / sqlite3 / pyarrow / FastAPI
Query error contract              ✅ QueryError + 5 子类（code/message/details/recoverable/source）
Structured query only             ✅ 无 arbitrary SQL 公共 API
Frame filters                     ✅ sequence / normalized_timestamp / channel /
                                    arbitration_id / direction / is_extended / is_fd
Segment planning & pruning        ✅ 复用 DataSegment 元数据裁剪候选 segment
DuckDB execution                  ✅ 列名·操作符·ORDER BY 全为 code-owned 常量 + 值参数绑定
Bounded pagination                ✅ FETCH limit + 1 → has_more；sequence cursor；无 OFFSET
Frame summary                     ✅ count / min / max 在 DuckDB 内聚合
Arbitration-id counts             ✅ GROUP BY 在 DuckDB 内，top_n 有界（默认 100 / 硬上限 1000）
Lightweight footer validation     ✅ canx.data.parquet.validate_segment_header（只读 footer）
Path safety                       ✅ 复用 resolve_within_root；DuckDB glob 元字符转义
Integrity rejection               ✅ missing / foreign / wrong identity / unsupported schema /
                                    corrupt / path escape 全部 mapped 到 typed Query error
Empty session                     ✅ 空页 + count = 0 + bounds = None
Lifecycle independence            ✅ ACTIVE / INTERRUPTED / FAILED 只要 committed segment 存在即可查询
DuckDB connection ownership       ✅ 每次操作 short-lived in-memory connection
No whole-dataset materialization  ✅ 只取回 limit + 1 行；无 pandas，无整段 Arrow 结果
```

固定语义（snapshot boundary）：

```text
Query 看到的是 committed segments 的 metadata snapshot。
未落盘的缓冲帧不可见。
snapshot 之后新落盘的 segment 只在下一次 query 出现。
Query scope 限定单个 Data Session（一个 session 已横跨多个 Parquet segment）。
```

新增直接依赖：

```text
duckdb == 1.5.5
  · duckdb-1.5.5-cp313-cp313-win_amd64.whl 在本机 .venv 实测安装并读写通过
  · MIT；无强制传递依赖（pandas / numpy / pyarrow 仅出现在包的 `all` extra，未使用）
  · 未引入第二个 query engine；未引入 pandas / Polars
```

本机验证（2026-09-16，V0.2-03 实现完成时）：

```text
focused pytest (unit/query + test_parquet_header + 4 个 query integration)
                                                          210 passed
full pytest                                               552 passed
ruff check runtime tests tools                            exit 0
mypy runtime                                              exit 0 (48 source files)
100k-frame multi-segment query smoke                      PASS (100,000 frames / 20 segments)
scripts\package-windows.cmd                               exit 0
  · runtime build + staged sidecar                         PASS
  · packaged-runtime smoke (canx-runtime.exe)              PASS (1 passed)
  · Tauri MSI build + artifact check                       PASS (CAN-X_0.1.0_x64_en-US.msi)
Packaged DuckDB query execution path                      NOT VERIFIED
  (canx.query 与 duckdb 均不在打包 import graph 内；PYZ TOC 中 duckdb 出现 0 次)
```

Query smoke 记录（timing 仅供信息，不是性能门槛）：

```text
total frames                 100,000
segments                     20（每段 5,000 帧）
write                        ~1.0 s（informational）
unbounded limit=1000         returned 1,000 rows / has_more / 0.217 s
arbitration_id=0x103         matched 12,500 / returned 1,000 / candidate 20/20 / 0.149 s
sequence window 40000-40099  returned 100 / candidate 1/20 / 0.025 s
10-50 GB target              NOT VERIFIED
```

独立验收结论：**Conditional PASS**（一项 P1）。定向修复（V0.2-03-FINAL）已完成：

```text
P1  不安全的 timestamp segment pruning（silent false negative）
    现象：planner 把 DataSegment.first_timestamp / last_timestamp 当作 segment 的
          完整时间范围做剪枝，但 V0.2-02 writer 只保证 batch 首 <= batch 尾，
          且跨 batch 不倒退，并不保证 segment 内逐帧 timestamp 单调。
          一个合法 segment 可以持有 [0, 100, 1]（记录为 first = 0 / last = 1），
          此时查询 timestamp = 100 会剪掉真正含命中帧的 segment，
          返回 0 行而不是 1 行 —— silent false negative。

    修复：关闭 timestamp segment pruning；sequence / after_sequence 剪枝保留；
          timestamp 过滤继续由 DuckDB 的 frame-level predicate 承担。
          未改动 persistence 格式、SQLite schema、Parquet schema 与公共 Query API。

    证据：tests/integration/test_query_timestamp_regression.py（9 例，走真实
          Project → Data Session → Parquet → QueryService → planning → DuckDB → Frame）
          修复前（探针复现）：plan candidates = 0，query(ts=100) = []，summary count = 0
          修复后：              plan candidates = 1，query(ts=100) = [1]，summary count = 1
          回归：timestamp-only filter 现在保留全部 registered segment（smoke 20/20）；
               sequence window 仍能剪枝（smoke 20 → 1）。
```

定向修复后本机验证（2026-09-16，V0.2-03-FINAL 重新执行）：

```text
focused pytest (unit/query + query_across_segments + timestamp_regression + smoke)
                                                          182 passed
full pytest                                               561 passed
ruff check runtime tests tools                            exit 0
mypy runtime                                              exit 0 (48 source files)
scripts\package-windows.cmd                               exit 0
  · runtime build + staged sidecar                         PASS
  · packaged-runtime smoke (canx-runtime.exe)              PASS (1 passed)
  · Tauri MSI build + artifact check                       PASS (CAN-X_0.1.0_x64_en-US.msi)
Packaged DuckDB query execution path                 仍为 NOT VERIFIED
  （本次未扩大 packaged import graph；canx.query 与 duckdb 都不在其中）
```

未决优化（不在本阶段实现）：

```text
Timestamp segment pruning 只有在持久化契约真正提供可靠的 per-segment
min/max timestamp metadata（或等价保证）之后，才可安全重新启用。
```

状态：

```text
V0.2-03 DuckDB Query Foundation & Bounded Historical Query Service
Implementation complete
Independent acceptance: Conditional PASS
Final remediation completed
V0.2-03 Final Acceptance: PASS
```

本阶段刻意未进入：FastAPI query endpoint、Trace / Plot historical UI、
frontend store / Worker / WebSocket 改动、Agent query tool、自然语言查询、
arbitrary SQL console、Recorder capture-pipeline migration。

---

### Step V0.2-04 — Project-Backed Capture Persistence Integration

把 V0.2 已建立的 Project / DataSession / Parquet / Runtime Capture 四个独立基础模块
第一次连成一条**可失败、可恢复、可查询、可打包验证**的正式实时持久化链路。
本阶段只有一个 coherent increment：让一次实时采集真正落进一个 CAN-X 工程。

真实数据路径：

```text
Virtual CAN / future Hardware
        ↓
CapturePipeline
        ├──────────────► stream subscriber ──► FrameBatch ──► WebSocket
        └── archive ───► Runtime recorder boundary
                              ↓
                        ProjectRecorder          （runtime/canx/recorder/project_recorder.py）
                              ↓  asyncio.to_thread + 单实例锁
                        DataSessionWriter        （V0.2-02，未复制任何持久化逻辑）
                              ↓
                        bounded Parquet segments
                              ↓
                        SQLite session / segment metadata
                              ↓
                        QueryService / DuckDB    （V0.2-03，未改动）
```

`archive` 与 `stream` 仍是 sibling subscriber：Recorder 不是 UI stream 的下游，
UI / WebSocket 也从未成为 Recorder 的上游。

实现清单：

```text
Project-backed recorder          ✅ ProjectRecorder（state / failure / recorded_frames /
                                    data_session_id / fail / reset_session / start / append / stop）
DataSessionWriter 公共失败语义   ✅ ACTIVE → FAILED 公共 fail()，重复调用安全
Blocking IO 边界                 ✅ Parquet / SQLite / 文件系统写入全部 asyncio.to_thread
顺序与竞争                       ✅ 单实例 threading.Lock 串行化 writer 访问
                                    （asyncio 无法取消线程 → finalize 必须等 append 落盘）
Sequence 连续性                  ✅ 出现无法解释的 gap → recorder.sequence_gap + FAILED
Project 校验                     ✅ 复用 ProjectService.open（不复制 validator，
                                    拒绝时不在任意目录创建 project.db / data/sessions）
启动失败回滚                     ✅ DataSession 建立后任何启动异常 → session FAILED，不留 ACTIVE 孤儿
Runtime API                      ✅ start_capture(..., project_path=...)、data_session_id
FastAPI 契约                     ✅ POST /capture/start 新增 project_path，
                                    response 新增 data_session_id（无 project 时为 null）
Recording target 冲突            ✅ project_path + recording_path 同时给出 →
                                    capture.recording_target_conflict（结构化，非静默选择）
错误模型                         ✅ canx.runtime.errors.CaptureConfigurationError
                                    （code / message / details / recoverable / source）
```

固定语义（failure semantics）：

```text
正常 stop                        ACTIVE → flush pending → finalize → COMPLETED
recorder failure（任意来源）      → DataSessionWriter.fail() → FAILED
  · archive backpressure / 写盘失败 / SQLite 登记失败
  · recorder cleanup deadline 过期
  · sequence gap
  · 启动中途异常（DataSession 已建立）
FAILED 时                        已 committed segment 与其计数原样保留；
                                 缓冲区未 commit 的帧被丢弃，绝不计入 frame_count
end_at                            FAILED 不带 ended_at（模型只为 COMPLETED / INTERRUPTED 要求）
未持久化的 FAILED                只能被显式 recovery 变成 INTERRUPTED，永远不会变成 COMPLETED
```

未改动（本阶段明确保持）：

```text
Frame canonical schema          未改
Parquet schema                  未改（FRAME_PARQUET_SCHEMA_VERSION 仍为 1）
SQLite schema                   未改（user_version 仍为 2）
backpressure policy             未改（ADR 0001 语义原样保留）
V0.1 MsgpackRecorder            保留为 compatibility backend，未重写、未删除
新增依赖                        无（pyarrow / duckdb / FastAPI 已足够）
```

本机验证（2026-09-16，V0.2-04 实现完成时）：

```text
full pytest                                               618 passed
  （本增量前基线：561 passed；新增 57 例）
  tests/unit/data/test_session_writer_failure.py          12 passed
  tests/unit/recorder/test_project_recorder.py            15 passed
  tests/unit/runtime/test_project_capture.py              15 passed
  tests/unit/api/test_capture_project_target.py            6 例（新增）
  tests/integration/test_project_backed_capture.py         7 passed
  tests/integration/test_project_recording_soak.py         1 passed
  tests/integration/test_packaged_runtime_smoke.py         2 passed（新增 project-backed 一例）
ruff check runtime tests tools                            exit 0
mypy runtime                                              exit 0 (50 source files)
scripts\package-windows.cmd                               exit 0
  · runtime build + staged sidecar                         PASS
  · packaged-runtime smoke (canx-runtime.exe)              PASS (2 passed)
  · Tauri MSI build + artifact check                       PASS (CAN-X_0.1.0_x64_en-US.msi)
```

Performance / soak 记录（Windows 11 10.0.26200 / Python 3.13.15 / 本机 .venv /
单进程 / 无真实 CAN 硬件；timing 仅供信息，不是性能门槛）：

```text
修改前 baseline（20 kHz 配置，1.5 s bounded run，msgpack record path）
  generated 13,000 / captured 13,000 / recorded 13,000
  recorder queue peak 6 / failures 0 / gaps 0 / elapsed 1.546 s

修改后 project-backed（同配置、同机）
  generated 12,500 / captured 12,500 / persisted 12,500（segment 1 个）
  recorder queue peak 5 / failures 0 / gaps 0 / elapsed 1.620 s
  → 与 V0.1 prototype 同一量级；差值在 run-to-run 噪声范围内

in-suite bounded soak（rate 20 kHz / 2.0 s / max_frames_per_segment 4096）
  generated 16,505 / captured 16,505 / persisted 16,505
  segments 5 / max segment 4096 / sequence gaps 0 / recorder failures 0
  dropped 0 / recorder queue peak 176 / elapsed 2.060 s / state completed

10–50 GB target                                            NOT VERIFIED
```

Packaged Parquet 执行路径（本阶段由 NOT VERIFIED 变为 VERIFIED）：

```text
build\runtime-dist\canx-runtime.exe                 46,491,142 bytes
  · PyInstaller hook-pyarrow.py 命中，pyarrow.libs DLL 已收集
  · PYZ 中 duckdb 出现 0 次（query domain 不在打包 import graph 内）
packaged project-backed smoke                       PASS
source 侧读回（由 .exe 进程产出的文件；帧数随运行时序而异，以下为一次实测快照）
  project                        临时 CAN-X project（source 进程创建）
  segment                        data/sessions/3cf00cb7-…/segments/000000.parquet
  segment bytes                  37,316
  footer rows                    1,230（1 row group）
  canx metadata                  can-x-frame-segment / schema_version 1 /
                                 session_id / stream_id / segment_index=0 全部一致
  DataSession                    completed / frames 1,230 / segments 1
  stream_id 一致                 是（与 /capture/start 返回的 stream_id 相同）
  QueryService.summarize_frames  1,230
  integrity inspection           clean
  · 断言是「DataSession COMPLETED + 段文件字节数与行数与注册元数据一致 +
    query 计数等于会话帧数 + integrity clean」，不依赖具体帧数
```

已知限制 / 边界（均为设计内行为，不是未修的缺陷）：

```text
1  archive path 是无损的，因此 sequence gap 只可能出现在已经可观测的失败之后；
   recorder 仍显式拒绝 gap（recorder.sequence_gap），不会把不完整录制呈现为完整。
2  cleanup timeout 只在「终态判定确实已落定」时才算一次 recorder failure：gate 关闭
   后该 DataSession 再也不可能变成 COMPLETED，已经 committed 的 Parquet segment 仍然
   有效可查，session 以 FAILED 终止。若 deadline 到期时终态写入已经不可撤销地执行中，
   runtime 不再猜测结论，而是进入 finalizing 并等待 worker 的真实结果。
   （V0.2-04 独立验收曾在此发现 P1；见 V0.2-04-FINAL 与 V0.2-04-FINAL-2。）
3  DataSessionWriter.fail() 的落库是 best-effort：内存状态在该 writer 生命周期内是
   权威；未落库的 FAILED 只能被显式 recovery 变成 INTERRUPTED。
4  data_session_id 只在 recorder 仍持有活动 session 时非空（即 start 之后、stop 之前）。
5  bare recording_path 仍是 V0.1 的 .canxmsg 文件，不是 CAN-X 工程存储格式。
6  timestamp segment pruning 仍关闭（V0.2-03 决定，未变）。
7  max_frames_per_segment 默认仍为 65536（foundation 常量，不是 SPEC 值）。
```

NOT VERIFIED（本阶段未改变）：

```text
Packaged DuckDB query execution path   NOT VERIFIED
  （未新增 Query HTTP API；packaged exe 内 duckdb 出现 0 次）
10–50 GB engineering dataset           NOT VERIFIED
real Vector / PCAN / Kvaser / ZLG       NOT VERIFIED
macOS real-machine validation          NOT VERIFIED
windowed desktop launch                NOT VERIFIED（沿用 V0.1.1 结论）
```

状态：

```text
V0.2-04 Project-Backed Capture Persistence Integration
Implementation complete
Local verification complete
Independent acceptance: Conditional PASS（一项 P1）
Final remediation completed（V0.2-04-FINAL，见下）
Final Acceptance: NOT PASS
Remaining P1: terminal SQLite commit in-flight race
FINAL-2 remediation completed（V0.2-04-FINAL-2，见下）
FINAL-3 remediation completed（V0.2-04-FINAL-3，见下）
```

### Step V0.2-04-FINAL — Recorder Cleanup Timeout Lifecycle Consistency

独立验收给出 **Conditional PASS**，唯一阻塞项：

```text
P1 — recorder cleanup timeout lifecycle race
```

根因（已在修复前确定性复现）：

```text
ProjectRecorder.stop()
  → asyncio.to_thread(DataSessionWriter.finalize)
  → Runtime cleanup deadline 到期
  → asyncio 取消的是 coroutine，不是线程
  → 被放弃的 worker 继续跑完 finalize()
  → DataSession 落成 COMPLETED
  同时 Runtime 已记录 recorder.cleanup_timeout

结果：Runtime 说失败，数据库说完成 —— 违反 “recorder failure ⇒ 不得呈现为 COMPLETED”。
```

复现方式（确定性，不是 sleep 碰运气）：把 `DataSessionWriter.finalize` 用一个
`threading.Event` 卡住 → 等 cleanup deadline 到期 → 释放 worker → 重开 project
读状态。修复前读到 `COMPLETED`（探针与 RED 测试均已验证）。

修复架构（commit gate + 持久化仲裁）：

```text
DataSessionWriter
  finalize()          = flush_pending() + commit_completed()      （行为不变）
  flush_pending()     → 写出尾部 Parquet segment；session 保持 ACTIVE
  commit_completed()  → 唯一的 ACTIVE → COMPLETED 转换；
                        持久写入条件为「行仍为 ACTIVE」，被抢先则抛
                        data.session.completion_refused
  fail()              → 条件式 ACTIVE → FAILED
  repository.advance_session_state / fail_active_session
                      → 由数据库裁决两个并发终态写入者

ProjectRecorder
  arm_stop_deadline(seconds)   owner 在 await stop() 前武装预算
  stop()                      一个 worker：flush（长，可取消）→ 终态决策（短）
  终态决策                     gate = failure flag + armed deadline + 剩余预算；
                               只在 gate 打开且预算足够时提交 COMPLETED
  终态锁                       声明 deadline 与决策互斥，不会交错
  close_finalization_gate()    有序裁定，返回三态（V0.2-04-FINAL-2 加入第三态）：
                               · 已 COMPLETED → COMPLETED（不报失败）
                               · 取得终态锁 → 关闭 gate + 一次条件式 ACTIVE → FAILED
                                 仲裁 → FAILED（此时 FAILED 已真正赢下）
                               · 拿不到终态锁（终态写入已不可撤销地执行中）→ PENDING，
                                 不发布任何 terminal verdict
  finalization_outcome         COMPLETED / FAILED / PENDING：唯一的终态事实来源
  finalization_pending / wait_for_finalization  证明被放弃的 worker 是否真的退出

RuntimeService
  _cleanup_recorder           stop() 前 arm_stop_deadline
  _handle_recorder_timeout    由 recorder 裁定：COMPLETED → 不上报；FAILED → 上报
                              recorder.cleanup_timeout；PENDING → 进入 finalizing
  _begin_pending_finalization 启动唯一一个 owned settlement task（每 capture 至多一个，
                              不是 fire-and-forget）
  _settle_pending_finalization 等 worker 真正退出 → 读 finalization_outcome：
                              COMPLETED 则 settle 成 idle；否则才发布 cleanup_timeout
  start_capture               上一 capture 终态未落定时拒绝新 project capture
                              （capture.finalization_pending，recoverable）
  aclose                       shutdown：bounded 等待 settlement，超时取消并留给
                              worker / 显式 recovery，绝不猜结论
```

固定语义：

```text
healthy stop        flush → 预算内提交 → COMPLETED，runtime failure = None
recorder failure    backpressure / 写盘失败 / sequence gap → FAILED
cleanup timeout     → deadline 到期时若终态判定尚未开始：FAILED（durable，worker 之后
                      恢复也无法翻盘，已 committed 的 segment 全部保留且可查）
                    → deadline 到期时若终态写入已不可撤销地执行中：不发布 verdict，
                      进入 finalizing，等 worker 的真实结果（V0.2-04-FINAL-2 修正：
                      此时「deadline 到期 ⇒ FAILED」并不成立，见下）
worker 在超时后恢复  在 gate 已关闭的路径上仍不能提交 COMPLETED：内存 gate 拒绝，
                      数据库终态仲裁兜底
repeated stop       幂等、有界，不会把 FAILED 改回 COMPLETED
timeout 的意义      未改变（仍是 bounded stop 的 observability/safety 机制）；
                      bounded stop 可以返回 finalizing，而不是伪造一个终态
```

本机验证（2026-09-16，V0.2-04-FINAL）：

```text
full pytest                                 641 passed
  （本增量前 618；新增 23 例）
  tests/integration/test_recorder_cleanup_timeout.py      6 passed
    · timeout 不可 COMPLETED（Test A/D）
    · timeout 后已 committed segment 保留 + integrity clean（Test B）
    · 慢但预算内的 finalize 仍 COMPLETED、failure None（Test C）
    · 重复 stop 安全（Test E）
    · 旧 worker 未退出时拒绝新 capture，settle 后可正常新建
    · worker 卡在 commit_completed 内部仍必须输（持久化仲裁）
  tests/unit/data/test_session_writer_failure.py         20 passed（新增 8 例拆分/仲裁）
  tests/unit/recorder/test_project_recorder_gate.py      10 passed（新增）
ruff check runtime tests tools              exit 0
mypy runtime                                exit 0 (50 source files)
scripts\package-windows.cmd                 exit 0（packaged project-backed smoke PASS）
in-suite project soak                       generated 15,998 / persisted 15,998 /
                                            0 gaps / 0 failures / state completed
                                            （无回退）
```

已知边界（诚实记录）：

```text
抢在 deadline 之前就已提交的 COMPLETED 是真实结果，不会被事后改写成 FAILED；
此时 runtime 不报 cleanup_timeout，不存在 “Runtime FAILED + DataSession COMPLETED”
的矛盾态。
被放弃的 worker 若永久挂死在磁盘 IO 上，session 可能保持 ACTIVE 而无法及时落成
FAILED；但它同样永远不可能变成 COMPLETED，且可被显式 recovery 变成 INTERRUPTED。
（本增量当时的 close_finalization_gate 只有两态，在 “终态写入已执行中” 的窗口里
会发布一个尚未被持久化事实支撑的 verdict；V0.2-04-FINAL-2 用第三态修正，见下。）
```

状态：

```text
V0.2-04-FINAL remediation complete
Awaiting final independent acceptance
→ 该轮独立验收：V0.2-04 Final Acceptance: NOT PASS
  剩余唯一阻塞项：P1 — terminal SQLite commit may already be in-flight
  定向修复见 V0.2-04-FINAL-2
```

本轮只做了实现 + 自验证。**不自行宣布 Final Acceptance: PASS**；
最终独立验收由 ChatGPT / 项目负责人执行。

---

### Step V0.2-04-FINAL-2 — Terminal Commit / Cleanup Timeout Atomicity

V0.2-04 的最终独立验收结论为 **NOT PASS**，剩余唯一阻塞项：

```text
P1 — terminal SQLite commit may already be in-flight
     when Runtime declares recorder.cleanup_timeout
```

根因：

```text
V0.2-04-FINAL 的 gate 把「owner 的 deadline 声明」与「worker 的终态决策」互斥起来，
但只把 owner 拿得到终态锁的情形处理完整。剩余窗口：

ProjectRecorder worker
  → _gate_open() == True（在决定之前就已经通过 gate 判定）
  → 取得 _terminal_lock → 进入 writer.commit_completed()
  → SQLite BEGIN IMMEDIATE 真正阻塞（另一个连接持有写锁）
同时 Runtime cleanup deadline 到期
  → close_finalization_gate 拿不到终态锁
  → 旧实现仍继续 _settle_durable_state 并（写锁拿不到、仲裁 join 超时后）发布
    recorder.cleanup_timeout
  → 之后 external lock 释放，worker 的 COMPLETED 事务完全可能先 commit

结果：Runtime 已说 FAILED，数据库说 COMPLETED —— 仍是同一个矛盾。
旧测试 test_a_worker_blocked_inside_its_own_completion_still_loses 把整个
commit_completed 包起来、在进入真实函数之前阻塞，构造的其实是
blocked BEFORE terminal SQLite transaction，没有覆盖这个窗口。
```

可行性结论（先回答，再改代码；方法论：deadline 到期只说明 bounded stop 必须返回，
不说明必须伪造一个 terminal verdict）：

```text
Question A  已在另一个不可取消 Python 线程里执行的 SQLite 终态事务，能否在
            bounded deadline 内被 owner 强制阻止提交？
Answer A    No。asyncio 无法取消线程；该 connection 属于 worker 线程，owner 既无
            句柄也无撤销语义；BEGIN IMMEDIATE 发出后，release 时谁先拿到写锁
            由 SQLite 决定，不受 owner 控制。不存在「保证 timeout 声明先赢」的实现。

Question B  能否在不阻塞 realtime event loop 的前提下引入真正可撤销的 durable
            commit protocol？
Answer B    No（可行但换不回正确性）。任何 fence 都要先写库，owner 写 fence 与
            worker 写 COMPLETED 争同一把 SQLite 写锁，同样无法在 bounded 时间内
            保证顺序；改文件 sentinel 则 worker 可在检查之后、COMMIT 之前越过，
            仍需要一个不可撤销的仲裁点。结论：本架构内无法证明
            「once timeout published, COMPLETED can never win」。

Question C  Runtime 是否应在终态事务已 in-flight 时不再宣布 cleanup_timeout，
            改为 pending 直到 durable 终态已知？
Answer C    Yes —— 本轮采用（Option A：pending finalization semantics）。
```

采用的 lifecycle 语义：

```text
ProjectRecorder.close_finalization_gate() 返回三态 FinalizationOutcome：
  COMPLETED  已 durably COMPLETED（deadline 只是报表晚了，不报失败）
  FAILED     owner 取得了终态锁 → 关闭 gate + 条件式 ACTIVE → FAILED 仲裁：
             此时 FAILED 已经真正赢下，worker 之后恢复也无法提交 COMPLETED
  PENDING    owner 拿不到终态锁 → 终态写入已不可撤销地执行中 →
             不发布任何 terminal verdict，也不碰数据库

ProjectRecorder.finalization_outcome（新）：COMPLETED / FAILED / PENDING
  唯一的终态事实来源；未 finalize 或 worker 未退出时返回 PENDING，
  绝不 default 成 FAILED。

RuntimeService（新增 CaptureSessionState.FINALIZING）
  PENDING → _begin_pending_finalization()：
    启动唯一一个 owned settlement task（每 capture 至多一个），
    capture_state = finalizing，failure 保持 None
  _settle_pending_finalization()：
    await worker 真正退出 → 读 finalization_outcome
      COMPLETED → settle 成 idle，failure 保持 None
      否则      → 此时 gate 已关、durable 已是 FAILED → 才发布 cleanup_timeout

不允许的路径：先发布 FAILED、后 DataSession COMPLETED（本轮消灭）。
```

正常 / 失败 / 四种 timeout 情形：

```text
healthy stop        RUNNING → stop → FINALIZING → 预算内 COMPLETED → IDLE
recorder failure    RUNNING → backpressure / 写盘失败 / sequence gap → FAILED
deadline 在终态事务开始之前到期（worker 仍在 flush）
                    gate 关闭 → durable FAILED → 发布 recorder.cleanup_timeout
deadline 在终态事务已不可撤销地执行中到期
                    不发布 terminal failure → 返回 bounded control → finalizing
                    background finalization settle
                      ├─ COMPLETED → idle（failure 始终为 None）
                      └─ FAILED    → FAILED + diagnostic
```

bounded stop / next capture / shutdown：

```text
bounded stop     stop_capture() 仍有界：终态未确定时返回 finalizing，而不是
                 await persistence forever，也不伪造终态。
                 /capture/stop 现在返回
                 {"status": "stopped", "finalization_pending": <bool>}
                 「stopped」仍表示 capture ingress 已停止；finalization_pending
                 单独表达 durable 终态是否已落定。

next capture     finalization 未落定期间拒绝一切新 capture（project-backed 与
                 realtime-only 都拒绝，capture.finalization_pending，recoverable）。
                 原因：settlement task 会写 _capture_state / _failure，新 capture
                 若在其下启动会被晚到的 settle 覆盖。settle 之后新 capture 正常。

repeated stop    幂等、有界；不会 spawn 第二个 settlement task，也不改变结论。

shutdown         FastAPI lifespan 改为 service.aclose()：bounded 等待 settlement，
                 超预算则取消该 owned task，把 durable 行留给 worker 或显式
                 recovery（ACTIVE → INTERRUPTED）。进程退出不制造矛盾态，也不留
                 未处理的 task 异常。settlement task 始终 owned（保存引用、显式
                 await/cancel），不是 fire-and-forget。
```

真实 SQLite contention 测试（本轮硬要求）：

```text
不 monkeypatch commit_completed 的入口来伪造阻塞。
用第二个真实 SQLite connection 持有 writer lock：
    sqlite3.connect(project.db, isolation_level=None) → BEGIN IMMEDIATE
让 worker 真正阻塞在 SQLite 的锁竞争点。

· tests/unit/recorder/test_project_recorder_gate.py
    test_a_deadline_landing_inside_the_terminal_commit_reports_pending
    buffer 为空（正好填满一个 segment）→ flush 不碰库 → 轮询到
    recorder._terminal_lock.locked() 证明 worker 已在终态临界区 →
    close_finalization_gate → PENDING 且 failure 仍为 None →
    释放锁 → finalization_outcome == COMPLETED，DataSession == COMPLETED
· tests/integration/test_recorder_cleanup_timeout.py
    test_a_terminal_commit_already_in_flight_is_never_pre_judged_failed
    Runtime 级：stop 返回时 failure 必须为 None、capture_state == finalizing；
    释放锁 → settle → failure 仍为 None、capture_state == idle、
    DataSession == COMPLETED
```

RED → GREEN 证据：

```text
修复前（同一真实 contention 测试）：
  tests/integration/test_recorder_cleanup_timeout.py:418
  AssertionError: assert RecorderFailure(code='recorder.cleanup_timeout', ...) is None
即：终态尚不可知时 Runtime 已经发布了 terminal failure。
修复后：全部通过（见下）。
```

本机验证（2026-09-16，V0.2-04-FINAL-2）：

```text
full pytest                          646 passed
  （本增量前 641；净增 5 例）
  tests/integration/test_recorder_cleanup_timeout.py
    · 新增：真实 SQLite contention 下不预判失败（Test A）
    · 新增：pending 阻塞新 capture（project-backed + realtime-only）/ 重复 stop
      幂等且不产生重复 settlement（Test D/E）
    · 新增：pending 时 shutdown 有界且不制造矛盾（Test F）
    · 改写：worker 越过 gate 判定后被 settle 而非被 condemn
      （原 test_a_worker_blocked_inside_its_own_completion_still_loses）
    · 保留：timeout 在终态事务开始前到期 → FAILED（Test B）
  tests/unit/recorder/test_project_recorder_gate.py
    · 新增：真实锁竞争下 PENDING → 释锁后 COMPLETED
    · 更新：close_finalization_gate 三态断言
  tests/unit/data/test_session_writer_failure.py（未改）
    · 保留：completion_refused 数据层仲裁（Test C）
  tests/unit/api/test_capture_project_target.py
    · 新增：健康 stop 返回 {"status": "stopped", "finalization_pending": false}
ruff check runtime tests tools       exit 0
mypy runtime                         exit 0 (50 source files)
scripts\package-windows.cmd          exit 0
  · packaged-runtime smoke           2 passed
  · packaged project-backed smoke    COMPLETED + Parquet + query readback
  · build\runtime-dist\canx-runtime.exe   46,502,727 bytes
  · MSI                              CAN-X_0.1.0_x64_en-US.msi
in-suite project soak               generated 14913 / captured 14913 /
                                    persisted 14913 / 0 gaps / 0 failures /
                                    state completed（无回退）
```

Data semantics（未改变）：

```text
committed Parquet segments survive
frame_count 只反映已注册 segment
FAILED 不伪造 ended_at；COMPLETED 必有 ended_at
ACTIVE 仍可由显式 recovery 变成 INTERRUPTED
Frame / Parquet / SQLite schema 均未改变；user_version 未升级；未新增依赖
```

已知限制（诚实记录）：

```text
被放弃的 worker 若永久挂死在磁盘 IO 上，session 会保持 ACTIVE，runtime 停留在
finalizing；此时新 capture 被持续拒绝（capture.finalization_pending），直到
worker 退出，或进程退出后由显式 recovery 把它变成 INTERRUPTED。
这是设计选择：终态未知时保持 pending，而不是编造一个 verdict。
若进程在 finalizing 中退出，durable 行可能是 ACTIVE，由显式
DataSessionService.recover_incomplete_sessions() 变成 INTERRUPTED —— 允许，且已记录。
```

状态：

```text
V0.2-04-FINAL-2 remediation complete
Awaiting final independent acceptance
```

本轮只做了实现 + 自验证。**不自行宣布 Final Acceptance: PASS**；
最终独立验收由 ChatGPT / 项目负责人执行。

---

### Step V0.2-04-FINAL-3 — Runtime Status Truthfulness During Finalization

V0.2-04-FINAL-2 的核心修复（terminal commit 已 in-flight 时不再猜测 terminal verdict）
独立验收判定为 **PASS**，但该轮 V0.2-04 Final Acceptance 仍为 **NOT PASS**，剩余唯一阻塞项：

```text
P1 — transient false FAILED runtime status during stop / finalization
    正常或 pending 停止期间，Runtime 会短暂对外暴露
    capture_state = FAILED 且 failure = null
    —— 一个没有任何 failure verdict 支撑的失败判定。
```

根因：

```text
RuntimeService.capture_state 旧实现是一个推导属性：

    if self.has_session and not self.capture_active:
        return CaptureSessionState.FAILED
    return self._capture_state

正常 stop 的流程是 pipeline.stop() → ingress 停止，但 _pipeline 在整个
finalization / cleanup 结束之前仍被 Runtime 持有：

    capture_active = False   （pipeline 已不再 is_running）
    has_session    = True    （_pipeline 仍非 None）

于是 stop 全程 property 都返回 FAILED，而此时 _failure 仍为 None：
    capture_state = failed
    failure       = null

该窗口覆盖了「ingress 已停、会话尚未释放」的整个区间（drain + recorder.close +
finalization settle），并发 GET /runtime/status 能真实读到它。RED 已确定性复现：
{'capture_state': 'failed', 'failure': None}。
```

修复（只改 Runtime lifecycle 可观察性，不触碰持久化协议）：

```text
1  _stop_capture()：在有 active session、开始停止 ingress 之前，若尚无 failure，
   显式声明 capture_state = FINALIZING。此前 pipeline 一变成非 active 就会暴露
   FAILED，现在从 stop 一开始就是诚实的 finalizing；已有 failure 时保持既有
   DEGRADED/FAILED，由 stop 末尾 settle。

2  capture_state property：删除 has_session && !capture_active ⇒ FAILED 的隐式推导，
   直接返回 _capture_state。状态机只由 lifecycle owner 在每次跃迁时显式写入，
   绝不再从「pipeline 是否在跑」反推失败。

不改动的边界：
  · ProjectRecorder 的终态协议（COMPLETED / FAILED / PENDING）
  · terminal commit gate / SQLite 仲裁 / DataSessionWriter finalization（未触碰）
  · capture_active 语义（ingress 是否在跑）：finalizing 期间它保持 False，合法
  · has_session 语义（Runtime 是否仍持有 session）：未与 capture_active 强行合并
  · /capture/stop 的 finalization_pending 语义与 RuntimeStatusResponse 字段
```

可观察不变量（本轮固定为契约）：

```text
capture_state == FAILED  ⇒  failure != None
  FAILED 是 verdict，不是「pipeline 停了」的描述；只有真实 failure diagnostic
  存在时才可发布。
FAILED + failure        = 真实 failure verdict（backpressure / write / gap / cleanup）
FINALIZING + failure    = None（终态未知，绝不猜）
IDLE                    = failure 为 None，会话已释放
RUNNING                 = failure 为 None，ingress 在跑
```

Concurrent status（真实 SQLite contention，非 patched seam）：

```text
setup                          第二个连接 hold project.db 写锁；worker 真实阻塞在
                               terminal commit_completed 的 BEGIN IMMEDIATE
status while stop in progress  GET /runtime/status 在 stop_capture() 运行期间返回
                               capture_state=finalizing / failure=null
                               （修复前：failed / null —— RED 已复现）
status while commit pending    bounded stop 返回后 finalization_pending=true 且
                               capture_state=finalizing / failure=null
status after settlement        释放写锁 → settle → capture_state=idle / failure=null /
                               DataSession=COMPLETED
```

真实 failure 回归：cleanup deadline 在终态写入开始之前到期 → gate 关闭 → durable
FAILED → capture_state=failed 且 failure.code=recorder.cleanup_timeout（既有测试保持）。

本机验证（2026-09-16，V0.2-04-FINAL-3）：

```text
full pytest                          648 passed
  （本增量前 646；新增 2 例）
  tests/integration/test_recorder_cleanup_timeout.py      11 passed
    · 新增：真实 contention 下 stop 进行中 / bounded 返回后 status=finalizing
      （Test A/B/C，走真实 GET /runtime/status）
    · 新增：FAILED ⇒ failure != None 生命周期不变量（Test E）
    · 保留：FINAL-2 全部真实 SQLite contention 测试
ruff check runtime tests tools       exit 0
mypy runtime                         exit 0 (50 source files)
scripts\package-windows.cmd          exit 0
  · runtime build + staged sidecar     PASS
  · packaged-runtime smoke             2 passed（含 project-backed Parquet + query readback）
  · Tauri MSI build + artifact check   PASS（CAN-X_0.1.0_x64_en-US.msi）
in-suite project soak               generated 16333 / captured 16333 /
                                    persisted 16333 / 0 gaps / 0 failures /
                                    state completed（无回退）
```

未改动：terminal commit protocol / SQLite arbitration / DataSessionWriter
finalization；Frame / Parquet / SQLite schema 均未变；未新增依赖。

状态：

```text
V0.2-04-FINAL-3 remediation complete
V0.2-04 Final Acceptance: PASS
  （独立验收结论：FINAL-3 修复的 runtime status truthfulness 通过；
    此前 "transient false FAILED runtime status during stop/finalization"
    为 V0.2-04 最后一处阻塞项，已修复并保留并发 status 回归证据）
```

V0.2-04 至此关闭。后续增量（V0.3-01 起）不再改动 recorder 终态协议 /
terminal commit gate / SQLite 仲裁 / DataSessionWriter finalization。

---

### Step V0.3-01 — Trace Query & Filtering Foundation

本阶段的任务不是"做一个 Trace 页面"，而是建立**一个专业 Trace 功能可以长期依赖的
查询契约**：bounded、typed、deterministic 的 CAN frame 查询语义，让未来的 Trace UI、
DBC decoder、Plot、Export 与 Agent 全部消费同一个事实来源。

唯一 coherent increment：在 V0.2-03 已有 Query Foundation 上补齐
**CAN ID range 与 ID mask** 两个过滤轴，并把这条链路第一次通过**类型化 HTTP Trace API**
暴露出来。**没有引入第二套 query engine**——没有 TraceQueryEngine、
TraceDuckDBEngine 或 TraceStorageEngine。

架构（复用，不复制）：

```text
CAN-X Project
   ↓  ProjectService.open（唯一项目身份边界，绝不 QueryService(Path(path))）
   ↓
DataSession（committed segment metadata snapshot）
   ↓
FrameFilter（本阶段扩展 ID range / ID mask）
   ↓
planning（仅 sequence 剪枝；ID 轴不假造 segment 级剪枝）
   ↓
DuckDB（列名 / 操作符 / 排序全部 code-owned；调用者值全部绑定参数）
   ↓
bounded canonical Frame page（FETCH limit + 1，无 OFFSET）
   ↓
POST /trace/query · POST /trace/summary（typed JSON）
```

复用 V0.2 组件（未复制任何查询逻辑）：
`QueryService` / `QueryEngine` / `query.planning` / `QueryError` 契约 /
`validate_segment_header` / `resolve_within_root` / `repository` 元数据 snapshot。

新增组件：

```text
runtime/canx/api/trace.py        Trace HTTP adapter（request/response models + router）
runtime/canx/api/errors.py       共享错误 envelope + 诊断→状态码映射
FrameFilter.arbitration_id_start / arbitration_id_end
FrameFilter.arbitration_id_mask / arbitration_id_mask_value
FrameFilter.arbitration_id_mask_target（掩码右值归一，语义单点定义）
```

Filter 契约（本轮完整轴）：

```text
sequence                 sequence_start / sequence_end            两端 inclusive
normalized_timestamp     normalized_timestamp_start / end        两端 inclusive
channel                  channel_ids                             轴内 OR
exact CAN ID             arbitration_ids                         轴内 OR
CAN ID range             arbitration_id_start / arbitration_id_end 两端 inclusive
CAN ID mask              (id & mask) == (value & mask)
RX / TX                  directions
standard / extended      is_extended（与 ID 数值独立，绝不从 id 推断帧类型）
CAN / CAN FD             is_fd
组合语义                  不同轴之间 AND；同一轴内多值 OR；不引入通用查询 AST
```

掩码语义固定为 `(id & mask) == (value & mask)`：mask 与 value 必须成对出现
（半个掩码 → `query.invalid_arbitration_id_mask`），否则一个孤立的 mask 会静默
退化成"恒真"谓词。range 反向 → `query.invalid_arbitration_id_range`；
越界 → `query.invalid_arbitration_id`。掩码右值由 domain 归一
（`arbitration_id_mask_target`），engine 只绑定参数，不重复定义语义。

Pagination（延续 V0.2-03，未改）：

```text
cursor      after_sequence（exclusive，取上一页最后一个 returned sequence）
ordering    ORDER BY sequence ASC（唯一确定性排序；本轮不允许任意 ORDER BY）
fetch       limit + 1 → has_more；无 OFFSET
顺序        过滤在分页之前（WHERE filters AND sequence > cursor … FETCH limit + 1）
```

HTTP 契约：

```text
POST /trace/query
  request   project_path · session_id · filters{...} · after_sequence · limit
  response  session_id · frames[] · has_more · next_after_sequence
POST /trace/summary
  request   project_path · session_id · filters{...}
  response  session_id · matching_frame_count · 四条 bounds（空结果全为 null）
payload     data = uppercase hex（如 "1122AABB"），全 endpoint 统一
frame 投影  与 Parquet 列一一对应的 16 个 canonical 字段（含 timestamp provenance：
            hardware_timestamp / host_timestamp / clock_domain / timestamp_quality），
            后续 Trace timestamp mode 无需再改 schema
```

错误映射（共享 envelope：`code` / `message` / `details` / `recoverable` / `source`）：

```text
filter / 分页语义非法    400   透传 query 域 code（如 query.invalid_arbitration_id_range）
project 不可读           400   project.* code，source = project
session 未注册           404   query.session_not_found
segment 文件缺失         409   query.segment_missing（recoverable，明确不是 404 session）
segment 损坏 / 外来      500   query.segment_unreadable / query.segment_invalid
引擎执行失败             503   query.execution_failed
```

API 边界另外保证两件事：

```text
project_path 一律经 ProjectService.open 验证（路径不可信输入）
查询在 worker thread 执行（asyncio.to_thread），不阻塞 realtime WebSocket 所在 event loop
```

本轮测试面（`648 → 796 passed`，新增 148 例）：

```text
unit/query    FrameFilter ID 轴校验、越界/反向/半掩码的 typed 错误、
              向后兼容（新字段追加在末尾，位置参数语义不变）
              engine 可观察选择：range 两端 inclusive 边界、mask 归约语义
              （能区分 (id&m)==(v&m) 与 (id&m)==v）、组合 AND、
              SQL 注入边界（caller 值恒为绑定参数）
integration   多 segment 真实链路 ProjectService → DataSessionService → Parquet →
              QueryService → DuckDB：ID range / mask / 组合 / 过滤后 cursor 分页 /
              空结果 / planner 不因 ID 过滤剪枝
              专门构造"每个轴都有一个只违反该轴的见证帧"的组合 fixture：
              逐轴放宽必须恰好新增那一帧 —— 证明没有谓词被静默忽略
unit/api      POST /trace/query · /trace/summary 契约与全部语义级非法输入的结构化错误、
              missing/corrupt segment 的诊断语义、注入尝试、空 session、
              interrupted 与 failed session 的已 committed 数据仍可查
100k smoke    100,000 frames / 20 segments：ID range / mask / channel / direction /
              组合 / 全量与过滤后分页无损重建（无重复、无缺失、顺序确定）
```

100k correctness / performance smoke（Windows 11 10.0.26200 / Python 3.13.15 /
本机 .venv / 单进程；timing 仅供信息，不是门槛）：

```text
total frames                    100,000（20 segments × 5,000）
ID range 全覆盖                 matched 100,000 / candidate segments 20/20（不剪枝）
ID range 0x101-0x103            matched 37,500
ID mask 0x04（half the volume） matched 50,000
combined（range+mask+channel+
  direction+sequence）          matched 250
全量分页（page size 10,000）     10 页；拼接 == 0..99,999；无重复无缺失；cursor 严格递增
过滤后分页（50,000 匹配）        5 页；与单次查询结果一致
内存                            每页最多 limit + 1 行；无整会话 materialization；无 pandas
```

Packaged DuckDB query path（**由 NOT VERIFIED 变为 VERIFIED**）：

```text
build\runtime-dist\canx-runtime.exe       59,550,936 bytes
  · canx.query 与 duckdb 现已在打包 import graph 内（exe 内 duckdb 字符串出现 13 次；
    此前各版本均为 0 次）
packaged-runtime smoke                    PASS（3 passed）
  · 新增 test_packaged_runtime_answers_a_trace_query_from_persisted_parquet：
    同一个 canx-runtime.exe 先 capture 写出 Parquet segment，再通过
    POST /trace/query 与 /trace/summary 读回；帧数与会话 frame_count 一致；
    ID 过滤条件由 exe 自己返回的帧构造，因此不依赖虚拟适配器的具体 ID
  · 断言还覆盖：range 与 exact 结果一致、mask 谓词逐帧成立、空结果无 cursor
Tauri MSI                                 PASS（CAN-X_0.1.0_x64_en-US.msi）
```

本机验证（2026-09-16，V0.3-01 实现完成时，全部为实际执行结果）：

```text
full pytest                           796 passed（本增量前 648；新增 148 例）
ruff check runtime tests tools        exit 0
mypy runtime                          exit 0（52 source files）
scripts\package-windows.cmd           exit 0
  · runtime build + staged sidecar     PASS
  · packaged-runtime smoke             3 passed（含 Trace query 端到端）
  · Tauri MSI build + artifact check   PASS（CAN-X_0.1.0_x64_en-US.msi）
  注：packaged smoke 必须在重新打包后执行；用旧 exe 运行只能证明旧镜像。
```

Schema 与依赖：

```text
SQLite schema      未改（user_version 仍为 2）
Parquet schema     未改（FRAME_PARQUET_SCHEMA_VERSION 仍为 1）
Frame schema       未改（ID range / mask 是查询语义，不是持久化字段）
新增依赖            无（duckdb / pyarrow / FastAPI / pydantic 已足够）
```

本阶段刻意未进入（属 V0.3 后续子阶段）：DBC import / parse / decode、
Plot、完整 Trace 前端（Dockview / virtualized table）、markers persistence、
changed-byte 渲染、freeze/follow、列定制、Agent `trace.query` / `trace.filter` tool、
自然语言查询、CSV/ASC export、arbitrary SQL、regex over raw payload、
cross-session project-wide query。

已知限制（诚实记录）：

```text
1  generic Trace search syntax 仍未定义：本轮不猜 regex / full-text / payload
   wildcard / SQL LIKE 的语义，等 PRD 明确后再做。ID / channel / direction /
   time 四个轴是当前唯一受支持的过滤面。
2  ID 轴不做 segment 级剪枝：DataSegment 没有 min/max arbitration_id 元数据，
   假造一个 range 会制造 silent false negative，因此保持全部 registered segment
   为 candidate。只有 persistence 契约真正提供可信 ID 边界后才可重新考虑。
   （未为此修改 SQLite schema。）
3  timestamp segment pruning 仍关闭（V0.2-03 决定，未变）。
4  delta 与 changed bytes 不在本轮持久化，也不在响应中直接返回；Query API 已提供
   计算所需的原始字段（normalized_timestamp / sequence / data / ID / channel）。
5  HTTP 请求体的**类型级**错误（例如 limit 传非整数）仍由框架校验返回非结构化
   422；所有**语义级**非法输入（越界、反向 range、半掩码、空数组、非法 direction
   等）都是结构化 400。
6  查询范围仍是单个 Data Session；跨 session / project-wide 查询属后续增量。
7  ACTIVE 会话只暴露已 committed segment，writer buffer 中的帧不可见
   （Live Trace 是 WebSocket 路径，两条路径未耦合）。
8  /trace/id-counts 未暴露：QueryService.count_by_arbitration_id 已存在且本轮未改，
   但本阶段只暴露 query 与 summary 两个最必要的端点，保持 API surface 最小。
```

状态：

```text
V0.3-01 Trace Query & Filtering Foundation
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做了实现 + 自验证。**不自行宣布 acceptance PASS**；
独立验收由 ChatGPT / 项目负责人执行。

---

### Step V0.3-01-FINAL — HTTP Request Validation Envelope Closure

V0.3-01 独立验收结论：主体实现没有新的阻塞问题（ID range / mask 及
`(id & mask) == (value & mask)` 语义、QueryService 单一查询事实来源、bounded DuckDB
query、参数绑定与 SQL injection boundary、`sequence ASC`、`after_sequence` exclusive
cursor、`limit + 1`、无 OFFSET、`ProjectService.open` 工程身份验证、segment header /
path integrity、`/trace/query`、`/trace/summary`、worker-thread 查询执行、
packaged DuckDB query path 全部通过），但发现**一个阻塞 Final Acceptance 的缺口**：

```text
FastAPI / Pydantic 在路由进入 domain 之前拒绝请求体时，响应仍是框架原生的
{"detail": [...]} —— 一个没有 code、没有 source、并且会把调用方原始输入
（"input": ...）回显回去的第二套错误协议。

复现（修复前，真实 ASGI 调用）：
  limit="abc"      → 422 {"detail":[{"type":"int_parsing","loc":["body","limit"],
                                    "msg":"...","input":"abc"}]}
  缺失 session_id   → 422 {"detail":[{"type":"missing","loc":["body","session_id"],
                                    "msg":"Field required","input":{...}}]}
  malformed JSON   → 422 {"detail":[{"type":"json_invalid",...}]}
  /capture/start   → 同样 {"detail": [...]}
```

SPEC §38 要求所有跨 API 边界的可诊断错误使用统一五字段结构，而该缺口是 domain
契约之外的**框架层**错误路径——`QueryError` / `ProjectError` 已统一，只有
`RequestValidationError` 尚未进入该契约。

修复原因（唯一的 coherent increment）：把框架层请求校验失败纳入同一 envelope，
同时**保留其独立语义**——统一的是错误 body contract，不是 HTTP status。

实际错误契约：

```text
HTTP status   422（保持框架语义；未与 domain 的 400/404/409/500/503 合并）
code          api.request_validation_failed
message       "The request payload does not match the API contract."
details       {"errors": [{"location": [...], "type": "...", "message": "..."}]}
recoverable   false
source        api
```

`details` 只保留 `location` / `type` / `message`：

* Pydantic 的 `input` 与 `ctx` **被丢弃**——不回显调用方 payload，也不泄漏 validator
  内部上下文；
* `location` 段只保留字段名与数组下标，任何非 `str`/`int` 段降级为类型名（如 `dict`），
  因此异常位置自身也不可能变成 payload echo；
* 不返回 traceback、exception repr、raw exception object、SQL、DuckDB / SQLite /
  pyarrow internals、credentials/token。

实现位置（共享 API boundary，未在 endpoint 内复制逻辑）：

```text
runtime/canx/api/errors.py   REQUEST_VALIDATION_FAILED_{CODE,MESSAGE,STATUS}
                             request_validation_envelope() + issue sanitizer
runtime/canx/api/app.py      单个 app-level @app.exception_handler(RequestValidationError)
```

因此 `/trace/query`、`/trace/summary`、`/capture/start` 以及后续任何 endpoint 共用同一
边界，不会形成"Trace 专属错误协议"。未修改 QueryEngine / FrameFilter / QueryService；
未新增第二套 error framework；未在路由内手工复制 try/except。

层次分离（本轮固定为契约）：

```text
domain semantic validation        400   code = query.* / project.*，source = query / project
request schema / type validation  422   code = api.request_validation_failed，source = api
```

两者不得互相降级：把 `arbitration_id_start = -1` 这类**取值**问题报成
`api.request_validation_failed` 是错的（它是合法 payload、非法取值），已有专门测试
锁住这一点。

新增测试（39 例，`tests/unit/api/test_request_validation_envelope.py`）：

```text
/trace/query     15 例：limit 非整数 / cursor 非整数 / cursor 为浮点 / limit 为数组 /
                 缺失 project_path / 缺失 session_id / is_fd 为对象 / is_fd 为数组 /
                 filters 为字符串 / sequence_start 为对象 / mask 为对象 /
                 channel_ids 为整数 / project_path 为整数 / session_id 为数组 /
                 body 为数组
/trace/summary    6 例：filter flag 类型 / directions 类型 / 缺失必填字段 / 空 body /
                 body 为数组
malformed JSON    4 例：截断对象 / 空 body / 缺值 / 单引号
跨 endpoint       7 例：/capture/start 的 channel_count（非字面量、超出字面量）、
                 rate_hz / batch_size / is_fd / project_path / recording_path 类型
定位可诊断        3 例：location 指向真正出错的字段（body + 字段名）；
                 JSON 解析失败只报告位置，不回显收到的字节
无副作用          3 例：被拒的 capture 不产生 session（has_session / capture_active /
                 data_session_id 与磁盘均不变）；被拒的请求不创建 project、不改变既有
                 project 内容；被拒的 Trace 请求**不进入 route handler**
                 —— 用对照组证明：同一 absent project + 合法 payload 得到
                 400 project.not_found（handler 执行了），非法 payload 得到 422
                 （handler 根本没执行）
层次不合并        1 例：domain 语义错误仍是 400 + query.* 且 source = query
```

每条断言同时校验：`status == 422`、body 键恰为五字段、`code` / `source` /
`recoverable` 的精确值、`details.errors[*]` 只有 location/type/message，
以及响应文本中不含 `traceback` / `pydantic` / `duckdb` / `sqlite` / `pyarrow` /
`"input"` 等泄漏标记。

本机验证（2026-09-16，V0.3-01-FINAL，全部为实际执行结果）：

```text
full pytest                           835 passed（本增量前 796；新增 39 例）
ruff check runtime tests tools        exit 0
mypy runtime                          exit 0（52 source files）
scripts\package-windows.cmd           exit 0
  · runtime build + staged sidecar     PASS
  · packaged-runtime smoke             3 passed
  · Tauri MSI build + artifact check   PASS（CAN-X_0.1.0_x64_en-US.msi）
build\runtime-dist\canx-runtime.exe   59,552,228 bytes
```

packaged runtime 差异验证：packaged smoke 现在额外断言**打包后的
`canx-runtime.exe` 对非法 payload 同样返回统一 envelope**
（422 / `api.request_validation_failed` / `source = api` / `recoverable = false`），
因此该 handler 在打包镜像中与 source 侧行为一致，而不是只在 pytest 进程内成立的假象。

Schema 与依赖：

```text
SQLite / Parquet / Frame schema   未改
新增依赖                          无
```

回归验证（domain 语义错误未受影响，仍为原 status 与 code）：

```text
invalid CAN ID / inverted range / half mask / invalid direction /
invalid session UUID / sequence 反向            → 结构化 400（code 仍为 query.*）
session not found                               → 404
missing segment                                 → 409
corrupt segment                                 → 500
```

状态：

```text
V0.3-01-FINAL HTTP Request Validation Envelope Closure
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做了实现 + 自验证，**未自行宣布 PASS**。

独立验收由 ChatGPT / 项目负责人执行，结论如下（该结论由项目负责人给出，
不属于 Agent 自行验收）：

```text
V0.3-01 Trace Query & Filtering Foundation
Final Acceptance: PASS
Status: CLOSED
```

V0.3-01-FINAL HTTP Request Validation Envelope Closure 的独立验收结论同样为 PASS，
V0.3-01 阶段（含 FINAL）自此关闭。项目负责人已正式批准从 V0.3-01 进入
**V0.3-02 — DBC Domain Foundation**。

---

### Step V0.3-02 — DBC Domain Foundation

Objective：

建立 **CAN-X 自有 DBC canonical domain model + cantools adapter + 只读 DBC
import / parse / validation foundation**。本阶段结束后的能力边界是：

```text
.dbc file
↓  safe file / import boundary
↓  cantools parser adapter
↓  CAN-X canonical DBC domain model
↓  typed result / typed DBC error
```

本阶段**不是** DBC 功能阶段。没有 decode、没有 project persistence、没有
HTTP API、没有前端、没有 Agent tool、没有 legacy code 复用（SPEC 已选择
`cantools`，`Legacy reuse = none`）。

架构（单向边界；`import cantools` 只允许出现在一个模块里）：

```text
.dbc file
   ↓  DbcImportService.import_file         唯一 filesystem 边界（READ ONLY）
   ↓  校验 → 读取 bytes → 按声明编码 decode → sha256 provenance
   ↓  CantoolsDbcParser.parse_text          唯一 cantools 边界
   ↓  cantools.database.load_string(database_format="dbc",
   ↓                                 strict=True, sort_signals=None)
   ↓  third-party objects → canonical 转换（一次性、完整、不逃逸）
   ↓
DbcDocument(
    database = DbcDatabase(messages, nodes, version),
    source   = DbcSource(name, path, sha256, size_bytes, encoding),
)
```

模块边界：

```text
runtime/canx/dbc/
├── __init__.py   包导出（canonical model + typed errors），不导入 cantools
├── model.py      CAN-X canonical immutable DBC models
├── errors.py     typed DBC domain / import errors
├── parser.py     唯一 cantools dependency boundary + third-party → canonical 转换
└── service.py    只读 import orchestration：filesystem → decode → parser
```

`import canx.dbc` 与 `import canx.dbc.model` **不会**导入 cantools（有独立子进程
测试证明）；只有 `import canx.dbc.parser`（以及使用它的 `canx.dbc.service`）
才会把第三方引擎拉进 import graph。

canonical domain models（全部 `@dataclass(frozen=True, slots=True)`，所有集合都是
`tuple`）：

```text
DbcDatabase    messages · nodes · version
DbcMessage     frame_id · name · length · is_extended · is_fd ·
               senders · signals · comment · cycle_time
DbcSignal      name · start_bit · length · byte_order · is_signed ·
               factor · offset · minimum · maximum · unit ·
               receivers · choices · is_multiplexer ·
               multiplexer_signal · multiplexer_ids · comment
DbcNode        name · comment
DbcChoice      value · label
DbcSource      name · path · sha256 · size_bytes · encoding
DbcDocument    database · source
DbcByteOrder   little_endian · big_endian（StrEnum）
```

关键 canonical 语义：

```text
标识空间      (frame_id, is_extended) 才是 message key，绝不是 frame_id 单独
              standard 0x000..0x7FF / extended 0x00000000..0x1FFFFFFF，
              与 Frame domain 的 ID 范围一致
extended 编码 DBC 文件里的 id | 0x80000000 存储细节不进入 canonical model：
              frame_id 始终是正常 arbitration ID，is_extended 独立表达
byte order    CAN-X 自己的 DbcByteOrder；"Intel"/"Motorola" 与 cantools 内部
              字符串都不扩散到 Runtime 其它部分
start_bit     原样沿用 DBC/cantools 的信号定义语义（big-endian 信号保留其
              DBC 起始位，如 fixture 中的 39）；本阶段不重写 Motorola bit layout
scale         factor / offset / minimum / maximum 一律归一为 float；
              physical = raw * factor + offset 只保存定义，不执行
choices       tuple[DbcChoice, ...]，按源文件 VAL_ 声明顺序，不暴露
              cantools 的 NamedSignalValue
不变式        name 非空 · frame_id 合法 · length 合法（非 FD 不超过 8）·
              start_bit >= 0 · length > 0 · factor/offset/min/max 有限 ·
              minimum <= maximum · mux 元数据自洽 · message 名唯一 ·
              (frame_id, is_extended) 唯一 · node 名唯一 · signal 名唯一
```

cantools boundary 与版本：

```text
cantools                              44.0.0（exact pin，见 pyproject.toml）
license                               MIT（SPDX Classifier: MIT License）
Requires-Python                       >=3.10；附带 py.typed
实际安装与验证环境                    项目 .venv / Python 3.13.15 / Windows 11
                                      10.0.26200（cp313 win_amd64）
transitive dependencies               bitstruct 8.23.0（MIT，有 cp313 win_amd64
                                      wheel）· textparser 0.26.2（MIT，pure
                                      Python，Requires-Python >=3.10）·
                                      argparse-addons 0.12.0（MIT）·
                                      crccheck 1.3.1（MIT）· python-can 4.6.1
                                      （已 pin，复用）
未引入                                pandas / Polars / numpy / fastparquet /
                                      第二个 DBC parser / cantools[cache]
                                      （diskcache）/ cantools[plot]（matplotlib）
仅使用的公开 API                      cantools.database.load_string(...) 与
                                      UnsupportedDatabaseFormatError.e_dbc；
                                      message/signal/node 只读公开属性
typing 处理                           cantools 自带 py.typed，未新增任何
                                      ignore_missing_imports；untyped 边界被
                                      限制在 runtime/canx/dbc/parser.py 内部，
                                      runtime/canx/dbc/model.py 保持 strict
```

encoding policy（本阶段固定的契约）：

```text
默认编码         utf-8-sig —— UTF-8，且容忍并剥离开头的 BOM
失败语义         严格解码：任何非法字节 → typed dbc.decode_failed（含
                 byte_offset），绝不静默替换
显式覆盖         import_file(path, encoding="cp1252") 等；实际使用的 codec
                 记录进 DbcSource.encoding
未知 codec       → dbc.decode_failed
与 cantools 的差异
                 cantools 自己的 add_dbc_file() 默认 cp1252 且 errors='replace'，
                 会把无法解码的字节静默替换成替代字符。CAN-X 刻意不继承这条
                 路径：DBC 文件没有自描述的编码声明，"跟随系统/引擎默认并被
                 静默修补" 不是策略。legacy 文件由调用方显式声明编码。
```

typed DBC errors（`source = "dbc"`，与 project / data / query 域同一五字段契约）：

```text
dbc.file_not_found      source 不是可读常规文件                     recoverable=false
dbc.read_failed         source 存在但读失败（锁 / 瞬时 IO）          recoverable=true
dbc.unsupported_format  不是 .dbc 来源（扩展名不匹配）               recoverable=false
dbc.decode_failed       声明的编码无法解码该 bytes / codec 不存在     recoverable=false
dbc.parse_failed        文本不是合法 DBC                             recoverable=false
dbc.invalid_model       文档合法但违反 CAN-X canonical 不变式         recoverable=false
```

parse 失败的诊断只暴露**结构化、无内容**的信息：`source_name`（由 service 层
补上，parser 只说文本）、`line` / `column`（当第三方异常公开了位置时）、
`parser_error`（异常类名）、以及必要时一个长度受限的单行 `reason`。语法错误的
`str()` 本身会内嵌出错源码行（`Invalid syntax at line 1, column 1: "…"`），
因此**不**采用；有专门测试断言错误详情里不出现文档内容、traceback 或第三方
对象 repr。

Fixtures（`tests/fixtures/dbc/`，全部人工最小化、license-clean、未复制任何
第三方 DBC）：

```text
basic_standard.dbc       standard CAN ID / 单 message / 多 signal / BO_+SG_ comment
extended.dbc             extended CAN ID（0x123 + is_extended）
endian_signed_scale.dbc  little/big endian · signed/unsigned · factor · offset ·
                         min/max · unit
choices.dbc              VAL_ 枚举值
multiplexed.dbc          multiplexer + multiplexed signals（m0/m1/m2）
metadata.dbc             node / sender / 多 receiver / comment / GenMsgCycleTime
can_fd.dbc               VFrameFormat = StandardCAN_FD · 64 byte payload
non_ascii.dbc            编码策略验证（UTF-8 非 ASCII comment 与 unit）
malformed.dbc            deterministic parse failure
```

Tests added（176 例）：

```text
tests/unit/dbc/test_dbc_model.py     87    canonical model 全部不变式、标识空间、
                                          byte order、scale 归一、choices、
                                          multiplexing 自洽、immutability、tuple
tests/unit/dbc/test_dbc_errors.py    12    五字段契约、code 唯一、recoverable 语义
tests/unit/dbc/test_dbc_parser.py    34    字段级映射（message/signal/sender/
                                          receiver/choice/mux/comment/cycle_time）、
                                          源顺序、重复 parse deterministic、
                                          standard vs extended、CAN FD、
                                          转换不变式 → invalid_model、
                                          parse 失败位置与不泄漏、
                                          无 cantools 对象的图遍历证明、
                                          两个子进程 import 边界证明
tests/unit/dbc/test_dbc_service.py   23    扩展名、存在性、读取失败、编码默认与
                                          覆盖、未知 codec、malformed、provenance、
                                          source 不被修改、不产生新文件/目录
tests/integration/test_dbc_import.py 20    8 个 fixture 的真实文件链路、repeated
                                          import、真实项目目录（ProjectService
                                          create）前后 fingerprint 完全一致、
                                          失败路径
```

其中三条是刻意的**负向**证据：

```text
1  导入前后对源文件做 size + mtime_ns + sha256 fingerprint，必须完全一致
2  在真实 project 目录（含 project.json / project.db / project.db-wal / 空的
   dbc/ 目录）内导入，导入前后整个目录树 fingerprint 必须完全一致，
   且 dbc/ 仍然为空 —— 没有 dbc table、没有 registry、没有源文件副本
3  canonical object graph 递归遍历，每个非原始对象都必须是 canx.dbc.model
   里声明的 dataclass（不靠类名字符串判断），且全新子进程里
   import canx.dbc.model 后 sys.modules 不含 cantools
```

本机验证（2026-09-16，V0.3-02 实现完成时，全部为实际执行结果）：

```text
full pytest                           1011 passed（本增量前 835；新增 176 例）
  · tests/unit/dbc                     156 passed
  · tests/integration/test_dbc_import   20 passed
ruff check runtime tests tools        exit 0（All checks passed!）
mypy runtime                          exit 0（57 source files，strict）
scripts\package-windows.cmd           exit 0
  · runtime build + staged sidecar     PASS
  · packaged-runtime smoke             3 passed in 13.00s
  · Tauri MSI build + artifact check   PASS（CAN-X_0.1.0_x64_en-US.msi）
build\runtime-dist\canx-runtime.exe   59,551,374 bytes
  · exe 内 "cantools" / "textparser" / "bitstruct" 出现次数均为 0
    （V0.3-01-FINAL 为 59,552,228 bytes，duckdb 仍为 13 次）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi  62,373,888 bytes
```

关于 packaged runtime 的诚实说明：本阶段**不主张** packaged runtime 提供 DBC
能力。`canx.dbc` 尚未出现在 runtime 入口的 import graph 中（所以 cantools 未被
打进 exe），这是本阶段 scope 的直接结果——只读 domain foundation 不接线到
`canx.api.app`。打包回归的通过只证明一件事：新增依赖与新增包**没有**破坏既有
runtime build / packaged smoke / Tauri package。

Schema 与依赖：

```text
SQLite schema      未改（user_version 仍为 2）
Parquet schema     未改（FRAME_PARQUET_SCHEMA_VERSION 仍为 1）
Frame schema       未改
HTTP schema        未改（本阶段未新增任何 endpoint）
WebSocket schema   未改
新增依赖            cantools==44.0.0（见上）
```

已知限制（诚实记录）：

```text
1  只接受 .dbc 扩展名（大小写不敏感）。其它扩展名一律
   dbc.unsupported_format，不做内容嗅探。
2  默认编码 utf-8-sig 且严格解码。真正的 cp1252 / cp932 / latin-1 文件必须由
   调用方显式声明 encoding=，否则得到 dbc.decode_failed。这是刻意选择，不是
   待办项：静默修补比报错更危险。
3  nested / extended multiplexing 的**拓扑**不在 canonical model 中表达。
   cantools 把嵌套结构放在 message.signal_tree 里，而 canonical DbcSignal 只
   保留扁平的 multiplexer_signal / multiplexer_ids。基本 multiplexing（单个
   开关 + 被复用信号）完整保留并有 fixture 与字段级断言覆盖。
4  start_bit 原样沿用 DBC/cantools 的定义，CAN-X 不重新解释 Motorola /
   big-endian 起始位语义。bit layout 的正确性由后续 decode 阶段独立验证。
5  choice 级 comment（CM_ VAL_）不表达。
6  cycle_time 若存在必须 >= 1；声明为 0 的 GenMsgCycleTime 会得到
   dbc.invalid_model。
7  canonical model 会拒绝 cantools 能接受的若干畸形文档：反转的 [min|max]、
   重复 message 名、重复 (frame_id, is_extended)、重复 node 名。这些一律
   得到 dbc.invalid_model，而不是"静默取最后一个"。
   反向区间 `[100|0]` 已实测：cantools 会接受，CAN-X 会拒绝 —— 有测试锁住。
8  本阶段不实现 Frame → Physical Signal 的 decode pipeline（见 Deferred）。
9  本阶段不读取 legacy CAN-Space / CanLab 的任何 DBC 代码（Legacy reuse = none）。
```

Deferred（明确留给后续 coherent increment，本阶段一行都没做）：

```text
DBC project persistence / registry / <project>/dbc/ 副本 / SQLite dbc schema
DBC HTTP API（POST /dbc/import · GET /dbc · GET /dbc/messages · POST /dbc/decode）
DBC Editor UI / react / Dockview / Monaco
Trace decoded columns / live signal decoding / historical signal decoding
Plot signal binding
Agent dbc.* tools
DBC editing / save / export / diff / merge
reverse engineering / automatic signal discovery
multiplexed frame decode / dynamic mux UI / mux tree editor
```

状态：

```text
V0.3-02 DBC Domain Foundation
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做了实现 + 自验证。**不自行宣布 V0.3-02 Acceptance PASS**；
最终验收由 ChatGPT / 项目负责人独立执行。

**独立验收结果（项目负责人，已执行）**：

```text
V0.3-02 — DBC Domain Foundation
Final Acceptance: PASS
Status: CLOSED
```

这是项目负责人人工执行的独立验收结论，不是 Agent 自行宣布的 PASS。项目负责人已正式
批准从 V0.3-02 进入 **V0.3-03 — DBC Project Registry & Persistence Foundation**。

### Step V0.3-03 — DBC Project Registry & Persistence Foundation

Objective：

把 V0.3-02 的只读临时导入能力，升级为 **Project 拥有的、具有稳定身份、持久化 metadata、
项目内文件副本和完整性校验的工程资产**：

```text
external .dbc
↓  Project identity validation
↓  V0.3-02 DbcImportService validation
↓  project-owned immutable copy   <project>/dbc/<asset_id>.dbc
↓  SQLite dbc_assets registry（project schema V3）
↓  close → reopen → list / get / load by asset_id
↓  canonical DbcDocument
```

本阶段**不是** decode 阶段。不做 Frame → Signal decode、不做 HTTP API、不做前端、
不做 Agent tool、不定义全局 active DBC、不做 delete / rename / replace。

---

## V0.3 — Professional Trace & DBC Foundation

目标：

把基础 CAN 数据使用体验提升到专业级。

Trace：

```text
filter
search
channel
ID mask/range
RX/TX
timestamp mode
delta
freeze/follow
markers
changed bytes
large-history query
export
```

DBC：

```text
import
parse
decode
messages
signals
factor
offset
endian
signed
enum
multiplexing foundation
validation
```

Agent 逐步获得：

```text
trace.query
trace.filter
dbc.decode
```

---

## V0.4 — Plot / Recorder / Deterministic Replay

Plot：

```text
multiple signals
cursor
measurement
zoom/pan
markers
event overlay
raw/decoded comparison
timeline synchronization
downsampling
```

Recorder：

转向正式持久化格式与 session 管理。

Replay：

```text
original timing
speed multiplier
pause
step
loop
trigger
range
channel mapping
CAN FD preservation
```

目标是支持：

**professional deterministic replay**

---

## V0.5 — Automation / Project Workflow Foundation

预计包含：

```text
Python Script Workspace foundation
Sandbox
Automation tasks
Test Runner
Tool API expansion
Task persistence
Checkpoint
Audit foundation
Project workflows
```

为 Agent V1 做准备。

---

# 19. V1.0 — Professional CAN/CAN FD Workbench

V1.0 的目标：

> 真正可供 CAN 工程师持续使用，而不是技术 Demo。

正式模块至少包括：

```text
Project
CAN / CAN FD
Device abstraction
verified Windows hardware subset
Trace
DBC
Plot
Recorder
Deterministic Replay
Automation foundation
Basic Agent
```

所有进入 V1.0 的功能必须达到专业级，而不是“功能存在”。

---

# 20. V1.1 — Diagnostics

主要：

```text
ISO-TP
UDS
Diagnostic Sessions
Read DID
DTC
ECU Reset
Routine Control
Write DID
Security Access architecture
Diagnostics Workspace
```

危险操作接入 Permission / Approval / Audit。

DoIP 根据架构成熟度进入本阶段或后续。

---

# 21. V1.2 — Vehicle Protocol & Automation Expansion

主要：

```text
J1939
Periodic TX
Message Sending
Automation
Test Sequences
Python scripting expansion
Agent automation tools
```

---

# 22. V1.3 — Reverse Engineering Intelligence

主要：

```text
entropy analysis
periodicity
correlation
change detection
counter detection
checksum inference
signal candidate generation
DBC inference
evidence tracking
confidence model
Agent-assisted reverse engineering
```

CAN-Space 中部分算法可能在此阶段作为 candidate 被审计复用。

不能整体迁移。

---

# 23. Later V1.x / V2 Direction

可能包括：

```text
XCP
DoIP advanced diagnostics
ECU Flashing
Advanced test orchestration
Hardware synchronization
LIN
Automotive Ethernet
Remote Runtime
Team workspace
Plugin SDK
Agent long-term engineering memory
Enterprise permissions
Private AI deployment
```

是否进入具体版本由届时 PRD 决定。

---

# 24. Current V0.1 Acceptance Result

2026-09-15 独立验收结果：

# Conditional Pass

已确认优点：

- CAN-X 是真正的新代码架构；
- 没有继续继承 PyQt；
- Frame domain 独立；
- Runtime 和 UI 解耦；
- Recorder 和 UI stream 已形成逻辑 sibling；
- binary realtime stream 已建立；
- frontend bounded Worker 模型已建立；
- Agent Tool Registry 已建立；
- trace.summary 是 read-only Tool；
- 测试和验证报告没有伪造真实硬件/macOS结论。

---

# 25. P0 — Recorder Backpressure Saturation

**Status: RESOLVED in V0.1.1.** Normative decision in `docs/ADR/0001-recorder-pressure-policy.md`. Capture now continues after a recorder pressure or write failure while the session becomes `degraded`; the archive enqueue has a finite deadline, loss is explicit and observable, and stop stays bounded. Evidence: `tests/unit/capture/test_pipeline.py`, `tests/integration/test_recorder_independence.py` (saturation + slow-disk soak), and the API lifecycle tests. See `docs/V0.1.1_ACCEPTANCE_REPORT.md`.

> Historical V0.1 issue description below.

# Recorder Backpressure Saturation

现有 lossless archive subscriber 使用 bounded queue。

CapturePipeline 顺序 await subscriber publication。

当 Recorder 长时间慢于 Capture，archive queue 被填满时，可能：

```text
archive queue full
→ await queue.put()
→ CapturePipeline blocked
→ adapter not consumed
→ stream also stops progressing
```

当前 slow-recorder test 没有把 archive queue 真正压满。

因此 V0.1.1 必须：

- 定义有限、明确的 backpressure policy；
- 禁止 silent recorder loss；
- 禁止 indefinite deadlock；
- 添加 saturation test；
- 添加 slow-disk/soak test；
- 提供 observable failure state。

---

# 26. Current Gaps

V0.1.1 status (evidence in `docs/V0.1.1_ACCEPTANCE_REPORT.md`):

```text
RESOLVED      AGENTS.md naming consistency
RESOLVED      root source-of-truth layout (root guides restored, docs/ duplicates removed)
RESOLVED      shared frontend realtime stream (single store; Trace + Plot)
RESOLVED      shared-stream partial-init resource leak (atomic Worker/WebSocket init)
RESOLVED      packaged Python runtime proof (canx-runtime.exe)
RESOLVED      Tauri distribution proof (MSI bundle includes the sidecar)
RESOLVED      single Windows packaging entry (scripts/package-windows.cmd)
PARTIAL       desktop smoke test — runtime half verified, window NOT VERIFIED
PARTIAL       realtime/UI latency — worker decode measured, UI/runtime latency NOT VERIFIED
DEFERRED      UI pressure performance
NOT VERIFIED  real CAN hardware
NOT VERIFIED  macOS validation
```

其中：

真实硬件和 macOS 不是当前 blocker。

---

# 27. Current Collaboration Model

本周主要采用：

## Human Project Owner

负责：

- 产品方向；
- 优先级；
- 最终决策；
- 最终验收。

## ChatGPT

主要负责：

```text
Architecture
Roadmap
Requirement clarification
Task decomposition
Prompt generation
Risk identification
Code/document review
Phase acceptance
Next-phase planning
```

ChatGPT 不直接承担主要代码生产。

---

## 天枢 Harness + DeepSeek v4.1 Flash

本周主要代码执行者。

负责：

```text
inspect repository
make scoped code changes
write/update tests
run tests
fix failures
update docs
produce completion report
```

不得自行改变产品路线和核心架构。

---

## Codex

由于额度有限：

只优先用于：

```text
complex architecture refactor
difficult concurrency problems
hard debugging
security-sensitive implementation
major independent review
critical release audit
```

普通功能开发尽量交给天枢 Harness。

---

# 28. DeepSeek Flash Prompt Strategy

针对 DeepSeek v4.1 Flash：

不要给一个巨大且高度自主的任务。

每个 Prompt 应尽量只包含：

```text
ONE coherent goal
+
exact relevant files
+
exact architectural constraint
+
expected implementation behavior
+
tests to add
+
commands to execute
+
forbidden actions
+
documentation updates
+
completion report format
```

任务规模通常控制在：

**一个明确工程问题 / 一个小功能 / 一个架构小步**

不要一次要求：

```text
设计 + 重构整个系统 + 加 8 个模块 + 完成 UI
```

---

# 29. Required Harness Loop

天枢每个任务必须：

```text
READ
↓
INSPECT
↓
PLAN SMALL CHANGE
↓
IMPLEMENT
↓
UNIT TEST
↓
INTEGRATION TEST
↓
LINT / TYPE CHECK
↓
UPDATE DOCS
↓
SELF REVIEW
↓
REPORT
```

禁止只改代码不测试。

---

# 30. Prompt Stop Conditions

只有遇到以下情况 Harness 才应该停止等待决策：

```text
PRD/SPEC conflict
core architecture must change
new commercial dependency required
security model must change
irreversible data/repository operation
environment makes required verification impossible
```

一般类名、文件名、小型内部实现细节应自主决定。

---

# 31. Definition of Done

任何任务：

```text
Requirement satisfied
Architecture respected
Implementation complete
Unit tests
Integration tests where applicable
Lint/type checks
Failure paths
Lifecycle/cleanup paths
Documentation
No hidden failures
No unsupported claims
```

性能任务还要求：

```text
Benchmark
Result recorded
```

---

# 32. Immediate Next Action

V0.1.1 独立验收返回 **Conditional PASS**，已批准进入 V0.2。

V0.2-01 Project Foundation 已完成实现、独立验收（Conditional PASS）、定向修复与
最终验收（见 §18 Step V0.2-01）：**V0.2-01 Final Acceptance: PASS**。

V0.2-02 Data Session & Parquet Segment Persistence 已完成实现、本机验证、提交推送、
独立验收（Conditional PASS）与定向修复（见 §18 Step V0.2-02）：
**V0.2-02 Final Acceptance: PASS**。

V0.2-03 DuckDB Query Foundation & Bounded Historical Query Service 已完成实现、本机验证、
独立验收（Conditional PASS，一项 P1 查询正确性）与定向修复 V0.2-03-FINAL
（见 §18 Step V0.2-03）：**V0.2-03 Final Acceptance: PASS**。

V0.2-04 Project-Backed Capture Persistence Integration 已完成实现与本机验证，
独立验收结论 **Conditional PASS**（唯一阻塞项：P1 recorder cleanup timeout
lifecycle race）。定向修复 V0.2-04-FINAL 已完成；该轮最终独立验收为
**V0.2-04 Final Acceptance: NOT PASS**，剩余唯一阻塞项是
**P1 — terminal SQLite commit may already be in-flight**，已由 V0.2-04-FINAL-2
定向修复（见 §18 Step V0.2-04-FINAL-2）：**V0.2-04-FINAL-2 core remediation: PASS**。
该轮最终独立验收仍为 **V0.2-04 Final Acceptance: NOT PASS**，剩余唯一阻塞项是
stop/finalization 期间短暂发布 `capture_state=failed` 且 `failure=null` 的错误状态；
已由 V0.2-04-FINAL-3 定向修复（见 §18 Step V0.2-04-FINAL-3）：
**V0.2-04-FINAL-3 remediation complete**。该轮独立验收已通过：
**V0.2-04 Final Acceptance: PASS**。
本阶段把实时 Capture 正式接入 Project → DataSession → Parquet 持久化链路，并让
runtime 的可观察终态始终与 SQLite 的真实持久化终态一致：cleanup timeout 只在
durable verdict 确实落定时才被声明；终态写入已在执行中时 runtime 进入 finalizing
并等待 worker 的真实结果，而不是猜测结论。packaged `canx-runtime.exe` 真正执行了
Parquet 写入路径（**Packaged Parquet execution path: VERIFIED**）。该轮
Packaged DuckDB query path 仍为 NOT VERIFIED —— 已由 V0.3-01 关闭
（见 §18 Step V0.3-01：**Packaged DuckDB query path: VERIFIED**）。

V0.3-01 Trace Query & Filtering Foundation 已完成实现、本机验证、提交推送与独立验收：
**V0.3-01 Final Acceptance: PASS**。其最终缺口 V0.3-01-FINAL HTTP Request Validation
Envelope Closure 的独立验收结论同样为 **PASS**（见 §18 Step V0.3-01 /
V0.3-01-FINAL）。V0.3-01 阶段自此关闭。

V0.3-02 DBC Domain Foundation 已获项目负责人独立批准并完成实现与本机验证
（见 §18 Step V0.3-02）：新增 `cantools==44.0.0` 依赖、`runtime/canx/dbc/`
五个模块、9 个 fixture 与 176 条测试；full pytest 1011 passed、
`ruff check runtime tests tools` / `mypy runtime` / `scripts\package-windows.cmd`
全部 exit 0。本阶段是只读 domain foundation，**不自行宣布 acceptance PASS**。

状态：

```text
V0.1.1 implementation                ✅ done
V0.1.1 final cleanup                 ✅ done
push GitHub                          ✅ done
independent acceptance               ✅ Conditional PASS
↓
V0.2 — Runtime & Data Foundation
├── V0.2-01 implementation           ✅ done
├── V0.2-01 independent acceptance   ✅ Conditional PASS (2 P1)
├── V0.2-01 final remediation        ✅ done
├── V0.2-01 final acceptance         ✅ PASS
├── V0.2-02 implementation           ✅ done
├── V0.2-02 local verification       ✅ done
├── V0.2-02 independent acceptance   ✅ Conditional PASS
├── V0.2-02 final remediation        ✅ done
├── V0.2-02 final acceptance         ✅ PASS
├── V0.2-03 implementation           ✅ done
├── V0.2-03 local verification       ✅ done
├── V0.2-03 independent acceptance   ✅ Conditional PASS (1 P1)
├── V0.2-03 final remediation        ✅ done
├── V0.2-03 final acceptance         ✅ PASS
├── V0.2-04 implementation           ✅ done
├── V0.2-04 local verification       ✅ done
├── V0.2-04 independent acceptance   ✅ Conditional PASS (1 P1)
├── V0.2-04-FINAL remediation        ✅ done
├── V0.2-04 final acceptance         ❌ NOT PASS (1 P1)
├── V0.2-04-FINAL-2 remediation      ✅ done
├── V0.2-04 final acceptance (2nd)   ❌ NOT PASS (1 P1)
├── V0.2-04-FINAL-3 remediation      ✅ done
├── V0.2-04 final acceptance (3rd)   ✅ PASS
↓
V0.3 — Professional Trace & DBC Foundation
├── V0.3-01 implementation           ✅ done
├── V0.3-01 local verification       ✅ done
├── V0.3-01 independent acceptance   ✅ PASS
├── V0.3-01-FINAL implementation     ✅ done
├── V0.3-01-FINAL independent accept ✅ PASS
├── V0.3-02 implementation           ✅ done
├── V0.3-02 local verification       ✅ done
└── V0.3-02 independent acceptance   ⏳ awaiting
```

---

# 33. North Star

任何开发决策都必须回答：

> 它是否让 CAN-X 更接近一个专业 CAN 工程师和 AI Agent 可以共享数据、工具、工程上下文和证据的工程 Runtime？

如果一个方案只是：

- 快速堆 UI；
- 增加菜单数量；
- 把业务耦合回 UI；
- 绕过安全；
- 造成未来必须再次推倒重来；

则不应采用。