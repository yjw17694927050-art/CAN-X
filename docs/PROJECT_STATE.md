# CAN-X — Project State & Long-Term Development Context

> **Document**: `docs/PROJECT_STATE.md`  
> **Purpose**: Cross-session / cross-agent project handoff  
> **Updated**: 2026-09-16  
> **Current Phase**: V0.3 — Professional Trace & DBC Foundation · Step V0.3-05-FINAL HTTP Frame Input Strictness Remediation
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

架构（数据流）：

```text
external .dbc
   ↓  ProjectService.open(root)                 项目身份校验（project.json ↔ project.db 一致）
   ↓  DbcImportService.import_file(source)      V0.3-02 唯一 filesystem 读取边界（READ ONLY）
   ↓  validate → bytes → decode → cantools → canonical model + sha256 / size / encoding
   ↓  re-read source, compare sha256 + size     TOCTOU 防护 → 不一致则 dbc.source_changed
   ↓  staging write  dbc/.tmp-<asset_id>.dbc    write + flush + fsync
   ↓  atomic promote dbc/<asset_id>.dbc         os.replace
   ↓  INSERT dbc_assets row                     SQLite 短连接 + 显式事务
   ↓
DbcAsset(asset_id, project_id, source_name, relative_path, sha256, size_bytes,
         encoding, imported_at)
   ↓  close project → reopen project
   ↓  list_assets / get_asset / load_asset(asset_id)
   ↓
canonical DbcDocument
```

模块边界（`import cantools` 仍然只允许出现在一个模块里）：

```text
runtime/canx/dbc/
├── __init__.py        包导出（canonical model + asset model + typed errors），不导入 cantools
├── model.py           CAN-X canonical DBC 内容模型（V0.3-02，本轮未改动）
├── asset.py           项目资产身份 + 路径契约 + path containment（不依赖 SQLite / cantools）
├── errors.py          typed 错误（V0.3-02 的 6 类 + 本轮新增 6 类）
├── parser.py          唯一 cantools 边界（V0.3-02，本轮未改动）
├── service.py         只读 import（V0.3-02）+ load_bytes（同一 codec / parser 路径，去掉文件系统步骤）
├── repository.py      SQLite rows ↕ DbcAsset（短连接；不 parse DBC；不碰文件系统）
└── project_service.py ProjectDbcService：文件 + registry 编排
```

职责划分与既有域一致：`canx.project.storage` 拥有表存在性与版本戳，`canx.dbc.repository`
只拥有行——和 `canx.data` 对 `data_sessions` / `data_segments` 的分工相同。两个仓储互不依赖，
各自持有自己的短连接与事务 helper。

SQLite schema V3：

```text
DATABASE_SCHEMA_VERSION         2 → 3
LEGACY_DATABASE_SCHEMA_VERSION  1（仍支持原地升级的最老版本）

CREATE TABLE dbc_assets (
    asset_id      TEXT PRIMARY KEY CHECK (length(asset_id) = 36),
    project_id    TEXT NOT NULL CHECK (length(project_id) = 36),
    source_name   TEXT NOT NULL CHECK (source_name <> ''),
    relative_path TEXT NOT NULL UNIQUE CHECK (relative_path <> ''),
    sha256        TEXT NOT NULL CHECK (length(sha256) = 64),
    size_bytes    INTEGER NOT NULL CHECK (size_bytes >= 0),
    encoding      TEXT NOT NULL CHECK (encoding <> ''),
    imported_at   TEXT NOT NULL
)
```

不建 dbc_messages / dbc_signals / dbc_choices / dbc_nodes / active_dbc / channel_binding：
canonical DBC 内容继续只有一个事实来源（`.dbc` 文件），SQLite 只保存 asset registry /
provenance / integrity metadata。把 message 与 signal 再拆一遍会立刻产生第二个 DBC 事实来源。

迁移链（每次 open 在同一事务内完成）：

```text
source 1 → 应用 V2 语句 + V3 语句 → PRAGMA user_version = 3 → COMMIT
source 2 → 应用 V3 语句            → PRAGMA user_version = 3 → COMMIT
source 3 → 正常打开
source 4+ → project.unsupported_schema_version（拒绝，不部分读取）
source 0（无版本戳的外来库）→ project.database_schema_invalid
任一步失败 → ROLLBACK：V1 项目仍是完整 V1，V2 项目仍是完整 V2
```

迁移步骤按"从哪个版本升级"索引，并通过函数晚绑定解析，因此失败注入测试可以只破坏最后一步
而不触碰真实 schema。新建数据库使用所有步骤语句的并集，所以"新建的 V3"与"从 V1 升级来的 V3"
不会漂移。

schema 验证（`PRAGMA user_version` 只是声明，不是证据）：

```text
project_metadata · data_sessions · data_segments · dbc_assets
+ 每张表的必需列（逐列检查）
缺失 / 列不全 → project.database_schema_invalid（open 时拒绝，不留到第一次数据或 DBC 操作才崩）
```

DbcAsset 契约：

```text
asset_id       canonical UUID（str(UUID(v)) 归一化）
project_id     canonical UUID；必须与 project_metadata.project_id 一致
source_name    只保存 basename；含路径分隔符 / "." / ".." 一律拒绝
               外部绝对路径（C:\Users\...、D:\Customer\...、/home/...）不落库
relative_path  project-relative POSIX 路径；必须形如 dbc/<name>.dbc
               拒绝绝对路径、反斜杠、.. 穿越、其它目录、嵌套目录
sha256         小写 64 位十六进制
size_bytes     >= 0
encoding       非空白 codec 名（真实 import 使用的 codec）
imported_at    timezone-aware（UTC 存储）
```

`relative_path` 由 `asset_relative_path(asset_id)` 生成，因此 project-owned 文件名永远是
`<asset_id>.dbc`，而不是用户导入的文件名：两个都叫 `network.dbc` 的源文件可以同时存在于
一个项目，且用户文件名永远不会成为路径片段。

文件系统提交顺序（跨存储一致性，诚实记录）：

```text
validate project → validate + hash source → re-read + 比对 → staging → 原子提升 → registry INSERT
```

因此不会出现"registry 行指向一个从未落盘的文件"。反向（文件已落盘、行未提交）在两步之间
崩溃时可能发生，见 Known limitations。registry INSERT 失败时，新建的资产文件会被
best-effort 删除，然后抛出 typed registry 错误。

load 的完整性顺序（先证明完整性，再解析）：

```text
get registry row → project_id 一致性 → relative_path 归属 dbc/（resolve 后判断，
含 dbc/ 本身是外链的情况）→ 文件存在 → size → SHA-256 → 用登记的 encoding 解码 → parse
```

因此篡改或丢失不会被误报成普通解析失败；而 hash 已通过时的解码/解析失败，会被报告为
"registry 行与文件不再自洽"，而不是"文档损坏"。

typed 错误（`source = "dbc"`，五字段契约与既有域一致）：

```text
dbc.invalid_asset          资产记录无法描述合法资产                recoverable=false
dbc.asset_not_found        该 asset_id 在本项目未注册              recoverable=false
dbc.asset_storage_failed   project-owned 副本写入 / 提升失败       recoverable=true
dbc.asset_registry_failed  registry 行提交 / 读取失败              recoverable=true
dbc.asset_integrity_failed 已登记资产与其 registry 行矛盾          recoverable=false
dbc.source_changed         源文件在验证与持久化之间改变            recoverable=true
```

项目本身无效时保持 `ProjectError`（`source = "project"`），不包装成 `dbc.*`：事实是项目不可用，
不是 DBC 操作出错。

Tests added（实测收集数）：

```text
tests/unit/project/test_storage_v3.py             13  新库 V3 / V1→V3 / V2→V3 / 失败回滚
                                                       （V1 保持 V1、V2 保持 V2）/ 残缺 V3 拒绝 /
                                                       registry CHECK 约束
tests/unit/dbc/test_dbc_asset.py                  54  DbcAsset 全部不变式、UUID 归一化、
                                                      source_name 非路径、relative_path 防御、
                                                      digest / size / encoding / aware 时间戳、
                                                      resolve_asset_path 逃逸拒绝
tests/unit/dbc/test_dbc_asset_registry.py         16  insert / get / list / 未知 / 多行 /
                                                      稳定顺序 / 字段往返 / 连接关闭 /
                                                      缺库缺表 / 重复 id / 损坏行
tests/unit/dbc/test_dbc_project_service.py        30  import 全流程、精确字节、同名不冲突、
                                                      无 staging 残留、源文件不被修改、TOCTOU、
                                                      staging 失败、提升失败、registry 失败清理、
                                                      无效项目先失败、相同字节两次导入产生两个资产
tests/unit/dbc/test_dbc_service.py                30  （+7）load_bytes 与 _with_source 合并方向
tests/unit/dbc/test_dbc_errors.py                 19  （+6）新错误类进入五字段契约与 code 唯一性
tests/integration/test_dbc_project_assets.py      15  reopen / 项目移动 / 多资产内容互不串 /
                                                      同名文件 / 篡改 / 截断 / 删除 / 恶意路径 /
                                                      换 digest / 跨项目行 / 手工投放文件不被注册
tests/integration/test_project_schema_upgrade.py   8  （+1）V1→V3 与 V2→V3，含真实 DataSession
                                                      与 Parquet segment 在升级后逐字段可读
tests/unit/project/test_storage_migration.py      16  已更新：版本断言与测试名对齐本轮语义
tests/integration/test_data_session_persistence.py     已更新：重开项目的 schema 断言为 V3
```

本机验证（2026-09-16，V0.3-03 实现完成时，全部为实际执行结果）：

```text
full pytest                          1153 passed, 1 skipped（本增量前 1011 passed）
  · tests/unit/project                65 passed
  · tests/unit/dbc                   269 passed, 1 skipped
  · tests/integration（DBC）          35 passed（test_dbc_import + test_dbc_project_assets）
  · tests/integration（Project/Data） 29 passed（schema upgrade + lifecycle + data persistence）
ruff check runtime tests             exit 0（All checks passed!）
mypy runtime                         exit 0（60 source files，strict）
scripts\package-windows.cmd          exit 0
  · runtime build + staged sidecar    PASS
  · packaged-runtime smoke            3 passed in 13.08s
  · Tauri MSI build + artifact check  PASS（CAN-X_0.1.0_x64_en-US.msi）
build\runtime-dist\canx-runtime.exe  59,554,835 bytes（V0.3-02: 59,551,374）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi  62,377,984 bytes（V0.3-02: 62,373,888）
```

packaged runtime scope（诚实说明）：本阶段**不主张** packaged runtime 提供 DBC 资产能力。
`canx.dbc` 仍未出现在 runtime 入口的 import graph 中——exe 的模块目录里 `cantools` /
`textparser` / `bitstruct` 出现次数均为 0，`duckdb` 仍为 13。这与本阶段没有 HTTP API、
没有组合根接线的 scope 一致。打包回归的通过只证明一件事：schema 迁移与 DBC 持久化代码
**没有破坏**既有 runtime build / packaged smoke / Tauri package。

一次必须记录的打包前观察：本轮第一次全量回归时，`tests/integration/test_packaged_runtime_smoke.py`
的 2 个用例失败，原因是它们用当前源码创建 V3 项目、再交给 **V0.3-02 构建的旧 exe** 打开，
旧 exe 按设计返回 `project.unsupported_schema_version`。这不是缺陷，而是 schema 升级真实生效的
证据：新项目 + 旧运行时 = fail-closed 拒绝。按本阶段要求运行 `scripts\package-windows.cmd`
重建 exe 后，全量回归 1153 passed。

Schema / 依赖变化：

```text
SQLite project schema   V2 → V3（新增 dbc_assets）
project.json schema     未改（manifest 仍只有 format / schema_version / project_id）
Parquet schema          未改（FRAME_PARQUET_SCHEMA_VERSION 仍为 1）
Frame schema            未改
HTTP schema             未改（本阶段未新增任何 endpoint，未改 canx.api.*）
WebSocket schema        未改
DBC canonical schema    未改（V0.3-02 的 DbcDatabase / DbcMessage / DbcSignal / DbcChoice /
                             multiplexing / encoding 契约原样保留）
新增依赖                none（仅标准库：sqlite3 · hashlib · os.replace · uuid · pathlib）
```

Known limitations（诚实记录）：

```text
1  filesystem 与 SQLite 无法形成真正的单事务。当前顺序保证"不会有 registry 行指向从未落盘
   的文件"，但进程若恰好崩在"资产文件已落盘、registry 行尚未提交"之间，理论上会留下
   unregistered orphan file。registry 状态是权威事实；project/dbc 下未注册的文件不被信任。
2  本阶段不实现 orphan 自动清理，也不扫描 dbc/ 自动注册——那会绕过 validation 与 provenance。
   若将来需要，应作为显式的 repair / audit 功能设计。
3  不实现 delete。SQLite 行删除与文件删除同样存在跨存储一致性语义，留待资产管理阶段。
4  不实现 rename / replace / update / revision。asset_id 当前代表"一次确定的不可变导入资产"；
   同一字节的重复导入会产生新的 asset_id（本轮刻意不做 content deduplication，也不引入
   UNIQUE(sha256)，以免锁死未来的 alias / revision 语义）。
5  不定义全局 active DBC（active_dbc_id / current_dbc / selected_dbc）。一个 Project 可以拥有
   0..N 个 DBC 资产；未来 decode 必须显式决定使用哪个 asset_id / asset_set / binding。
6  不实现 decode、不实现 HTTP API、不实现前端、不实现 Agent tool。
7  dbc/ 本身为外链（symlink）时的拒绝用例在本机被 skip：Windows 需要额外权限才能创建目录
   链接（WinError 1314）。路径逃逸的确定性证据来自恶意 relative_path 用例，而非链接用例。
8  source_name 只做"是文件名而不是路径"的校验；带空格的合法文件名会被接受。
```

Deferred（明确留给后续 coherent increment，本阶段一行都没做）：

```text
DBC decode（Frame → Signal，physical values，batch / live / historical decode）
DBC HTTP API（POST /dbc/import · GET /dbc/assets · GET /dbc/{id} · POST /dbc/{id}/decode）
DBC delete / rename / replace / revision
DBC Editor UI / React / Dockview / Monaco
Trace decoded columns / Plot signal binding
Agent dbc.* tools
asset deduplication / alias / 显式 channel ↔ asset 绑定
orphan 审计与显式修复
```

状态：

```text
V0.3-03 DBC Project Registry & Persistence Foundation
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做了实现 + 本机自验证。**不自行宣布 V0.3-03 Acceptance PASS**；
最终验收由项目负责人独立执行。若通过，下一推荐 coherent increment 是
**V0.3-04 — DBC Decode Foundation**（本轮不得开始）。

**独立验收结果（项目负责人，已执行）**：

```text
V0.3-03 — DBC Project Registry & Persistence Foundation
Final Acceptance: NOT PASS

Blocking:
P1 — DBC asset path identity / project containment is not fully enforced.
```

---

### Step V0.3-03-FINAL — DBC Asset Path Identity & Containment Closure

Objective：

只修复上面两个 P1 blocker，建立两条硬不变式：

```text
Invariant A  Asset Identity Binding
             asset_id = X  ⟺  relative_path == "dbc/<canonical-X>.dbc"

Invariant B  Project Containment
             resolved dbc directory  ∈ resolved project root
             AND
             resolved asset          ∈ resolved dbc directory
```

import 与 load 必须共享同一套 Project-owned path security invariant。本阶段不是
新功能阶段：不进入 V0.3-04、不实现 decode / API / 前端 / Agent tool、不做
delete / rename / replace / revision / orphan repair、SQLite schema 保持 V3、
不新增依赖。

#### Root cause

```text
P1-A  containment 只做了第二步。原实现：

         root       = project_root.resolve()
         asset_root = (root / "dbc").resolve()      ← 自身从未被验证
         candidate  = (root / relative_path).resolve()
         if not candidate.is_relative_to(asset_root): reject

      当 <project>/dbc 是指向项目外的 junction / symlink / reparse point 时，
      asset_root 本身就是项目外目录，candidate 也随之落在项目外 —— 但
      candidate.is_relative_to(asset_root) 仍然成立，检查放行。

      更严重的是 import：写入目标由

         target = self._root / PurePosixPath(relative_path)

      直接拼接，完全绕过 containment；而 _stage_asset() 里的
      target.parent.mkdir(parents=True, exist_ok=True) 还会在目录缺失或被替换时
      静默重建。

      实测（本机 junction，无需管理员权限；输出原样记录）：

         P1-B RED: accepted asset_id=3b0f9a5c-… path=dbc/11111111-….dbc
         load-side RED: resolve_asset_path allowed …\outside\3b0f9a5c-….dbc
         import-side RED: wrote …\outside\3a11516d-….dbc exists=True

      即：import 真的把 project-owned asset 写到了 Project 之外。

P1-B  DbcAsset.relative_path 只校验形状（"dbc/<name>.dbc"），不校验 <name> 是否
      等于 asset_id。registry 因此可以把 asset A 指向 dbc/B.dbc；当两个文件
      bytes 相同时，size 与 SHA-256 校验也会一致通过 —— identity ↔ owned path
      的一一对应被破坏，而现有每一道防线都无法察觉。
```

#### Fix

```text
A. asset_id ↔ relative_path 绑定（Invariant A）

   DbcAsset.__post_init__() 在 asset_id 归一化之后调用
   _require_bound_relative_path(asset_id, relative_path)：

       expected = f"dbc/{asset_id}.dbc"
       relative_path != expected  →  dbc.invalid_asset

   检查发生在归一化之后，因此大写 UUID 输入仍然合法（归一化成小写后比对），而
   "dbc/planted.dbc"、"dbc/<另一个-UUID>.dbc" 以及大写形式的路径一律拒绝。

   repository 的 _asset_from_row() 重建记录时碰到非法行，继续翻译为
   dbc.asset_integrity_failed（details.cause = dbc.invalid_asset）。语义分层保持：

       caller 构造非法资产   →  dbc.invalid_asset
       registry 行被篡改     →  dbc.asset_integrity_failed

B. containment 提升为两步（Invariant B）

   新增 resolve_asset_directory(project_root) -> Path：

       root      = project_root.resolve()
       directory = (root / "dbc").resolve()
       ① directory 必须 ∈ root        → 否则 dbc.asset_integrity_failed
       ② directory 必须是现存目录      → 否则 dbc.asset_integrity_failed
       return directory

   resolve_asset_path(project_root, asset) 改为接收资产对象，依次证明：

       ① relative_path == asset_relative_path(asset.asset_id)
          （纵深防御：即使传入被篡改的记录对象，也会被再次拒绝）
       ② resolve_asset_directory(project_root) 通过
       ③ candidate（已 resolve）∈ directory

C. import 与 load 共用同一 gate

       asset  = DbcAsset(...)                    ← identity 在此强制
       target = resolve_asset_path(root, asset)   ← containment 在此强制
       _stage_asset(target, raw)                  ← staging 由已验证 target 派生
       INSERT dbc_assets                          ← 顺序不变

   写入目标不再由 project_root / relative_path 拼接，"load 很安全、import 直接拼
   路径" 的缺口被消除。commit 顺序（validate → safe target → staging → fsync →
   atomic promote → registry INSERT）与 TOCTOU 重读比对逻辑均未改动。

D. fail-closed 目录处理

   _stage_asset() 不再 mkdir。"dbc 缺失 / dbc 是文件 / dbc 解析到项目外" 一律在
   resolve_asset_directory() 阶段以 dbc.asset_integrity_failed 拒绝：不静默重建、
   不自动改 SQLite、不自动复制文件、不自动更新 hash。

E. typed error 复用

   全部沿用 dbc.asset_integrity_failed，未新增 error code，未扩展 error taxonomy。
```

#### Tests added

```text
asset / path binding
  tests/unit/dbc/test_dbc_asset.py
    test_a_path_that_is_not_the_one_the_identity_owns_cannot_be_expressed
      [parent | dotted-escape | dotted-inside | other-asset-id | unnamed-file]
  tests/unit/dbc/test_dbc_asset_containment.py
    test_an_upper_case_identity_resolves_to_the_canonical_path

same-bytes swapped path
  tests/integration/test_dbc_project_assets.py
    test_a_registry_path_pointing_at_a_byte_identical_file_is_refused
      planted 文件与注册资产 bytes 完全相同（size 与 SHA-256 都与调换一致），
      失败发生在记录重建阶段，details.cause = dbc.invalid_asset ——
      即由 identity 拦截，而不是靠 digest。
  tests/unit/dbc/test_dbc_asset_registry.py
    test_a_row_whose_path_is_not_bound_to_its_id_is_an_integrity_failure
    test_a_row_whose_path_names_an_unrelated_file_is_an_integrity_failure

deterministic escaped dbc root（伪造 resolve，不依赖 symlink 权限，不 skip）
  tests/unit/dbc/test_dbc_asset_containment.py
    test_a_dbc_directory_that_resolves_outside_the_project_is_refused
    test_an_asset_cannot_resolve_through_a_dbc_directory_that_left_the_project
    test_a_missing_dbc_directory_is_never_recreated
    test_a_dbc_directory_replaced_by_a_file_is_refused
    test_resolving_rechecks_identity_even_for_a_mutated_record
    test_a_project_reached_through_a_link_is_not_mistaken_for_an_escape

import escaped-root
  tests/unit/dbc/test_dbc_project_service.py
    test_import_refuses_a_dbc_directory_that_resolved_outside_the_project
    test_import_refuses_a_project_whose_dbc_directory_is_missing
    test_import_does_not_recreate_a_dbc_path_that_is_a_file

load escaped-root
  tests/unit/dbc/test_dbc_project_service.py
    test_load_refuses_a_dbc_directory_that_resolved_outside_the_project

real link integration
  tests/integration/test_dbc_project_assets.py
    test_import_refuses_a_dbc_directory_linked_outside_the_project
    test_load_refuses_a_project_copy_reached_through_a_linked_dbc_directory
  tests/unit/dbc/test_dbc_asset.py
    test_a_dbc_directory_that_is_itself_a_link_out_of_the_project_is_refused
      （symlink 版本：本机无 SeCreateSymbolicLinkPrivilege → SKIPPED）
```

真实链接的实际情况（诚实记录）：本机 `os.symlink` 需要权限，会以
`WinError 1314` SKIP；但 **NTFS junction（`mklink /J`）不需要权限**，因此上面两个
集成用例创建了真实 junction 并**没有 skip**，它们直接证明"边界真的移动了、而
CAN-X 拒绝"。确定性用例（伪造 `Path.resolve`）覆盖同一代码路径，所以本机
symlink 权限缺失不影响 containment 证据的完整性。

#### Fix effectiveness（回滚验证）

新测试是否真的能捕获这两个缺陷，用"临时移除修复"反向验证：

```text
移除 resolve_asset_directory 里的 directory ∈ root 检查
  → 6 failed（4 个确定性 + 2 个真实 junction）      ← 捕获 P1-A
移除 __post_init__ 里的 _require_bound_relative_path 调用
  → 5 failed（含 byte-identical swap 用例）         ← 捕获 P1-B
恢复修复
  → 384 passed, 1 skipped
```

#### 本机验证（2026-09-16，V0.3-03-FINAL 实现完成时，全部为实际执行结果）

```text
python -m pytest -q
  1172 passed, 1 skipped in 102.18s
  （V0.3-03 基线：1153 passed, 1 skipped；本轮净增 19 个用例）

focused
  tests/unit/dbc + tests/integration/test_dbc_project_assets.py
    303 passed, 1 skipped
  tests/unit/dbc + test_dbc_project_assets + tests/unit/project +
  test_project_schema_upgrade + test_data_session_persistence
    384 passed, 1 skipped

Project migration regression
  tests/unit/project/test_storage_v3.py              13 passed
  tests/integration/test_project_schema_upgrade.py    8 passed

ruff check runtime tests     exit 0（All checks passed!）
mypy runtime                 exit 0（60 source files, strict）
```

packaging（`scripts\package-windows.cmd`，真实执行）：

```text
[1/6] runtime build + staged sidecar        PASS
[2/6] staged sidecar verified               ok: canx-runtime-x86_64-pc-windows-msvc.exe
[3/6] packaged-runtime smoke test           PASS
[4/6] Tauri MSI build                       Finished 1 bundle
[5/6] MSI artifact check                    ok: CAN-X_0.1.0_x64_en-US.msi
[6/6] packaging complete                    exit 0

独立复核（对新构建的 exe 重跑 smoke）
  CANX_TEST_RUNTIME_EXE=<repo>\build\runtime-dist\canx-runtime.exe \
    python -m pytest tests\integration\test_packaged_runtime_smoke.py -q
  → 3 passed in 13.86s

build\runtime-dist\canx-runtime.exe          59,553,459 bytes（V0.3-03: 59,554,835）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi  62,373,888 bytes（V0.3-03: 62,377,984）
```

packaged runtime scope 说明：本轮只改 domain 层的路径校验与模型校验，`canx.dbc`
仍未接入 runtime 入口的 import graph（无 HTTP API、无组合根接线，均为既有 scope）。
打包回归因此证明的是"未破坏既有 runtime build / packaged smoke / Tauri package"，
不主张 packaged runtime 提供 DBC 资产能力。exe 与 MSI 的字节数与 V0.3-03 有约 1 KB /
4 KB 的差异（PyInstaller 归档的构建期差异），本轮不对此做因果解释。

#### SQLite schema / 依赖

```text
SQLite project schema  未改（仍为 V3；本轮未触碰 migration 代码）
project.json           未改
Parquet / Frame        未改
HTTP / WebSocket       未改
DBC canonical schema   未改（V0.3-02 契约原样）
新增依赖               none
```

#### Known limitations

```text
1  resolve → check → write 之间仍存在文件系统 namespace race：另一个进程理论上可以
   在检查之后把 dbc 目录替换成 junction。本轮不做 handle-relative 的 OS-specific
   文件系统层来彻底消除它（成本远高于收益）。本轮目标被明确定义为"正常运行 +
   持久化篡改 + 已存在的链接逃逸"三者 fail-closed，不声称"所有 race 不可能"。
2  symlink 版本的用例在本机 SKIPPED（WinError 1314）。真实链接证据由 junction
   用例提供，确定性用例覆盖同一代码路径。
3  dbc 目录缺失一律拒绝且不重建：用户若手工删除 dbc/，需自行恢复目录后再导入。
   这是刻意的 fail-closed 选择，repair / audit 属于未来独立功能。
4  其余限制与 V0.3-03 相同（不实现 delete / replace / active DBC / decode / API /
   前端 / Agent tool；unregistered orphan file 不被信任也不被自动清理）。
```

状态：

```text
V0.3-03-FINAL DBC Asset Path Identity & Containment Closure
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做修复 + 本机自验证。**不自行宣布 V0.3-03 Acceptance PASS**；等待项目负责人
重新独立验收 V0.3-03。只有在 V0.3-03 获得 `Final Acceptance: PASS` 之后，才允许
进入 **V0.3-04 — DBC Decode Foundation**。

#### 独立验收结果（项目负责人，已执行）

```text
V0.3-03 — DBC Project Registry & Persistence Foundation
Final Acceptance: PASS
Status: CLOSED

V0.3-03-FINAL — DBC Asset Path Identity & Containment Closure
Independent Acceptance: PASS
```

以上为项目负责人给出的独立验收结论，由本轮（V0.3-04 开工前）原样记录。V0.3-03
（含 FINAL）自此关闭，项目负责人已批准进入 **V0.3-04 — DBC Decode Foundation**。

---

### Step V0.3-04 — DBC Decode Foundation

Objective：

建立 CAN-X 自有、headless、UI-independent、typed、deterministic 的
**Frame → DBC Signal Decode Foundation**：

```text
canonical Frame  +  canonical DbcDatabase
        ↓
     DbcDecoder
        ↓
typed canonical decoded result（DecodedFrame / DecodedFrameBatch）
```

本阶段只做 Runtime / headless decode domain + decode engine + batch semantics + tests。
不做 HTTP API、Trace decoded columns、前端、WebSocket decode、Plot、Agent tool、
DBC Editor、active DBC、channel ↔ DBC binding、Parquet/DuckDB signal dataset。

#### Architecture

```text
DBC text
  ↓  cantools（仅 parser.py 可见）
CAN-X DbcDatabase
  ↓  DbcDecoder(database)      ← compile once
compiled message index  +  per-signal extraction plan
  ↓  decode_frame(frame)
CAN-X DecodedFrame
```

- decoder 输入是 **canonical `DbcDatabase`**，不是 cantools database。同一个 decoder
  因此未来可服务 transient imported DBC / project-owned asset / historical decode /
  live decode / Agent / tests，全部走同一 contract；
- `DbcDecoder.__init__` 一次性建立 `(frame_id, is_extended) → compiled message` 索引与
  每个 signal 的 extraction plan。decode 期间**不做 database scan、不重算 bit topology**，
  编译后 effectively read-only（只持有 tuple 与只读 Mapping）；
- `runtime/canx/dbc/decode.py` 与 `decode_model.py` **不 import cantools / bitstruct /
  textparser**。实测：`import canx.dbc` 与 `import canx.dbc.decode` 后
  `'cantools' in sys.modules` 为 `False`，`bitstruct` 亦为 `False`；
- 本阶段未修改 Frame / FrameBatch。

#### Decoded domain models

```python
@dataclass(frozen=True, slots=True)
class DecodedSignal:        name · raw_value(int) · physical_value(float)
                            choice_label(str|None) · unit(str|None)
class DecodedFrame:         frame(Frame) · message_name · signals(tuple[DecodedSignal, ...])
class DbcDecodeFailure:     code · message · recoverable · source · details(read-only Mapping)
class DecodedFrameOutcome:  frame · decoded(DecodedFrame|None) · failure(DbcDecodeFailure|None)
class DecodedFrameBatch:    schema_version(=1) · stream_id · first/last_sequence ·
                            frame_count · outcomes(tuple[DecodedFrameOutcome, ...])
```

三条刻意设计：

1. **raw 数值永远保留**。choice label 是附加语义而非替换 —— `raw=2` 与
   `physical=2.0` 与 `choice_label="Error"` 同时存在，未来 Trace / Plot / Agent /
   raw-decoded 对照都不会丢失数值语义；
2. **provenance 不被摘要掉**。`DecodedFrame` 直接持有原 canonical `Frame`，
   `sequence` / `channel_id` / `direction` / `hardware_timestamp` / `host_timestamp` /
   `normalized_timestamp` / `timestamp_quality` / `clock_domain` / `is_fd` /
   `bitrate_switch` / `error_state_indicator` / `flags` 全部保留；
3. **outcome 严格二选一**。`decoded XOR failure`，两者同缺或同在均被模型拒绝；
   `DecodedFrameBatch` 为独立 schema（`CURRENT_SCHEMA_VERSION = 1`），不冒充 raw
   FrameBatch 的 `schema_version`。

#### Message identity

```text
lookup key   (frame.arbitration_id, frame.is_extended)     ← 绝不只用 arbitration_id
is_fd        严格相等：message.is_fd == frame.is_fd
             不等 → dbc.frame_type_mismatch（双向都拒绝）
```

同数值 ID 的 standard 与 extended 是两个不同 message，实测两者互不串。
DbcDatabase 已保证 `(frame_id, is_extended)` 唯一，decoder 不做二次去重。

#### Payload policy

```text
len(frame.data) <  message.length   → dbc.payload_too_short（不做 partial decode）
len(frame.data) == message.length   → decode
len(frame.data) >  message.length   → decode，只有 message.length 定义的 bits 参与
                                       extra bytes 完全不参与任何 signal
```

长 payload 合法，因为 CAN FD 实际 payload bucket 可能大于工程定义长度；实测
`message.length = 12` + 16/64 字节 FD frame 均可解码，且改变尾部 extra bytes
不改变任何 signal 值。本轮明确不支持 partial / truncated decode。

#### Bit extraction

```text
Intel(little endian)   字节窗口按 little 读入 → >> (start % 8) → & mask
Motorola(big endian)   start_bit 是最 MSB（sawtooth：每字节从自己的 bit 7 递减）
                       网络位 network_start = 8*(start//8) + (7 - start%8)
                       字节窗口按 big 读入 → >> shift → & mask
signed                 在 length 位上做 two's complement；8-bit 0xFF → -1（不是 255）
```

两种 byte order 都归约为「一个字节窗口 + 一次右移 + 一次 mask」，因此 decode 期间
只做一次 `int.from_bytes` 与两次整数运算。覆盖：1/8/12/16/32/64 bit、跨 byte boundary、
非 byte-aligned、非零 start bit、signed 跨边界。

#### Physical conversion

```text
physical = raw * factor + offset        （raw 为符号解释后的 integer）
choice_label = choices[raw]             （以 raw 数值查表，绝不以 scaled 值或 label 匹配）
unit                                    （原样保留，缺省 None）
```

- 未声明的 raw 值 **不抛错**，`choice_label = None`，数值原样保留；
- `minimum` / `maximum` **不是 clamp**。超出范围的解码结果原样返回，raw 亦不受影响；
- 计算结果若为 NaN / ±inf → `dbc.signal_decode_failed`（typed），绝不静默返回。

#### Multiplexing

支持（基础单层）：

```text
multiplexer 开关本身 decode 并出现在结果中（不被消耗掉）
multiplexer_signal is None 的普通 signal 视为 common / always active
multiplexer_ids 含当前 raw 开关值的 signal 才 decode，其余省略
选择依据是 raw 值：不是 scaled 值，也不是 VAL_ label
decoded signals 顺序 = DBC 文档声明顺序（inactive 省略后仍保持原序）
开关值未被任何 branch 声明时：返回 common + 开关本身，不猜任何一个 branch
```

明确 Deferred（不在本轮支持，且**不**声称支持）：

```text
nested / extended multiplexing（多层 mux 依赖）
```

该 topology 在当前 canonical model 中**无法无歧义表达**：V0.3-02 的
`_require_optionally_multiplexed` 拒绝「既是开关又有 parent」的半关系，因此 decoder
永远看不到真正的嵌套结构；但一个 extended-mux 源文件经 cantools 44.0.0 解析后会
**塌缩成「同一 message 里有两个开关、其余信号看起来都是 common」**——把它当作
「所有 signal 都 active」解码会静默返回错误结果。因此 `_resolve_multiplexer()`
在 compile 阶段拒绝两种拓扑并记录 `ambiguous_multiplexing`：

```text
message 中出现 >1 个 is_multiplexer=True 的 signal
某 signal 的 multiplexer_signal 在 message 内无法解析（不存在 / 不是开关）
```

#### Float signal audit（§28–30）

真实 probe（cantools 44.0.0，本机 `.venv`）：

```text
输入：SIG_VALTYPE_ 291 FloatSig : 1;   （合法 DBC 32-bit float 声明）
cantools：接受，signal.is_float == True
CAN-X（修复前）：DbcSignal 无 is_float 字段 → 元数据静默丢失
                 probe 输出：has is_float attr? False
```

结论：这是 **correctness blocker**，按 §29 做最小修复。

```text
canonical change        DbcSignal + is_float: bool（唯一允许的 canonical schema 变化）
parser                  显式 signal.is_float → DbcSignal.is_float（仍然 eager 转换，
                        cantools object 不越过 parser boundary）
RED evidence            tests/unit/dbc/test_dbc_float_metadata.py
                        修复前 7 failed / 1 passed，失败原因为
                        AttributeError: 'DbcSignal' object has no attribute 'is_float'
GREEN                   修复后 8 passed
```

decode 行为（§30 选择更安全的方案）：

```text
本阶段不实现 float signal decode
message 中任一 signal is_float=True → 整个 message 报 dbc.decode_unsupported
                                      （details.reason = "float_signal_payload"）
绝不把 float raw bits 当普通 integer 解码，也绝不返回半正确结果
```

#### Batch semantics

```text
decode_frame(frame)  → DecodedFrame | raise typed DbcError      （strict API）
decode_batch(batch)  → DecodedFrameBatch                        （one outcome per frame）
```

- 每个 input Frame 都有且只有一个 outcome，顺序与输入一致，`frame_count` 与输入相等；
- per-frame failure（message_not_found / payload_too_short / unsupported…）被捕获为
  `DbcDecodeFailure` immutable snapshot，**不中断整批**；
- 只有 programming error（参数类型错误 / 内部不变量破坏）才让整批失败；
- 输入复用既有 `FrameBatch`（caller-bounded），不引入第二套 raw batch，也不做
  whole-dataset API；性能 smoke 逐 batch / 循环处理，不 materialize 整个数据集；
- decoder 确定性：相同 `DbcDatabase` + 相同 `Frame` 重复调用结果相等；实测 8 线程
  并发读取同一 decoder 64 次，结果与单线程完全一致。

#### Typed errors

```text
dbc.message_not_found      (frame_id, is_extended) 在本 database 无定义        recoverable=false
dbc.frame_type_mismatch    frame 与 message 的 is_fd 不一致                    recoverable=false
dbc.payload_too_short      payload 短于 message.length                        recoverable=false
dbc.decode_unsupported     定义无法无歧义解码（越界 / float / mux 拓扑）        recoverable=false
dbc.signal_decode_failed   单个 signal 值无法成立（如 scaling 溢出为非有限数）   recoverable=false
```

- 全部 `source = "dbc"`，五字段契约与既有域一致；全部 `recoverable = false`，
  因为同一 Frame + 同一 database 重复执行结果不变，重试不可能改变结果；
- **不修改**既有 `DbcDecodeError`：`dbc.decode_failed` 继续只表示「DBC 文件 bytes
  不是声明 encoding 下的文本」，不得被 frame decoding 复用；
- error details 只含安全字段（sequence / arbitration_id / is_extended / is_fd /
  message_name / signal_name / expected_length / actual_length / reason）；
  不含 traceback、cantools repr、完整 payload dump；
- `KeyError` / `IndexError` / `OverflowError` / `struct.error` / 裸 `ValueError` /
  `cantools.DecodeError` 均不穿越 decoder public boundary：越界定义在 compile 阶段
  被拒绝，scaling 失败被翻译为 `dbc.signal_decode_failed`。

#### Differential verification（本阶段 acceptance 硬要求）

```text
oracle                  cantools == 44.0.0（pyproject pinned；仅 tests 使用）
oracle 使用面            仅 public API：load_string / Message.decode /
                        Database.decode_message / NamedSignalValue
                        （不 import _codec 等私有内部结构）
比较方式                 raw int 精确相等（==）
                        physical float 用 math.isclose
向量                    6672 个单 signal 定义 × 3 个 seeded payload
                        （random.Random(20260628)，无 Hypothesis 等新依赖）
                        = 20,016 次 raw + scaled 比较，全部一致
覆盖                    Intel / Motorola · signed / unsigned · 1..64 bit ·
                        8 字节内所有可行 start bit · factor / offset ·
                        multi-signal message · choices · multiplexing · CAN FD
结果                    全部一致（0 mismatch），tests/unit/dbc/test_dbc_decode_differential.py
```

一处**刻意分歧**（已写进测试而非抹平）：

```text
开关值未被任何 branch 声明时
  cantools  → raises DecodeError("expected multiplexer id 0, 1 or 2, but got 7")
  CAN-X     → 返回开关本身与所有 always-active signal（不猜 branch）
理由：该 Frame 仍然携带有效数据；Trace 应当能显示这个异常开关值，
      而抛异常的 API 显示不出来。两端行为都被测试钉住，不会各自漂移。
```

#### Project asset integration

`tests/integration/test_dbc_decode_project_asset.py`（6 例）实测通过：

```text
create project → import DBC asset → close → reopen
→ ProjectDbcService.load_asset(asset_id) → document.database
→ DbcDecoder(document.database) → canonical Frame → decode
```

验证：message 名与 signal 值正确（EngineSpeed 3000 → 750.0 rpm、CoolantTemp 80 → 40.0 degC、
ThrottlePosition 128 → 50.196078 %）、`decoded.frame is <原 Frame>`、
provenance（sequence / channel_id / direction / hardware_timestamp / normalized_timestamp /
timestamp_quality）完整、multiplexed asset reopen 后只返回被选中的 branch、
batch 路径 per-frame failure 正确、同项目内两个 asset 各自独立互不串。

两个层次只通过 canonical domain 耦合：V0.3-03 交付 `DbcDocument`，V0.3-04 消费
`DbcDatabase`，互不依赖。

#### Performance baseline

本机实测（`tests/performance/test_dbc_decode_benchmark.py`，100,000 frames，`-q -s`）：

```text
simple（basic_standard.dbc，3 signals）     100,000 frames in 0.721s  → 138,688 frames/sec
multiplexed（multiplexed.dbc）             100,000 frames in 0.611s  → 163,791 frames/sec
batched（1000-frame FrameBatch 流）         100,000 frames in 1.341s  →  74,596 frames/sec
```

**不设 PASS throughput threshold，也不声称 real-time ready**：本轮只记录真实测量值
与「不崩溃、工作集有界（frame 按需生成、逐 batch 消费，不 materialize 整个数据集）」。
benchmark 断言的是测量确实发生（每个 input frame 都产出 outcome、measured span > 0），
而不是某个 fps 门槛。

#### Tests added（逐文件实测收集数）

```text
tests/unit/dbc/test_dbc_float_metadata.py              8   is_float 模型契约 + parser 保留元数据（RED→GREEN）
tests/unit/dbc/test_dbc_decode_model.py               56   DecodedSignal/Frame/Failure/Outcome/Batch 全部不变式
                                                           与 immutability、decoded XOR failure、details 只读
tests/unit/dbc/test_dbc_decoder_lookup.py             19   (frame_id, is_extended) 身份、同号 standard/extended 隔离、
                                                           FD 双向 mismatch、unknown、参数类型、provenance 保留
tests/unit/dbc/test_dbc_decoder_bits.py               33   Intel / Motorola 手算向量、non-aligned、跨 byte、
                                                           two's complement 多宽度
tests/unit/dbc/test_dbc_decoder_values.py             24   factor / offset（含负值）、choices 语义、
                                                           未声明 choice、range 不 clamp、非有限数 typed failure
tests/unit/dbc/test_dbc_decoder_payload.py            18   exact / excess / truncated、zero-length、CAN FD、
                                                           越界 / float / 歧义 mux 定义被拒绝
tests/unit/dbc/test_dbc_decoder_multiplex.py          17   基础单层 mux、common signal、inactive 省略、返回开关、
                                                           raw 而非 scaled/label 选择、顺序、extended mux 拒绝
tests/unit/dbc/test_dbc_decoder_batch.py              20   one outcome per frame、顺序、无丢帧、failure snapshot、
                                                           determinism、8 线程并发读一致
tests/unit/dbc/test_dbc_decode_differential.py        14   cantools 差分（20,016 次比较）+ 分歧钉死 + 边界回归
tests/integration/test_dbc_decode_project_asset.py     6   项目资产 reopen → decode 端到端
tests/performance/test_dbc_decode_benchmark.py         3   100k frames × 3 条路径的实测基线
tests/unit/dbc/test_dbc_errors.py                     +7   5 个新 typed error 进入五字段契约与 code 唯一性
tests/unit/dbc/test_dbc_parser.py                     +1   float_signal.dbc 进入 canonical-graph 边界参数化
                                                           本轮新增 226 例（逐文件实测）
```

新增 fixture（最小、可审、license-clean，全部手写）：

```text
tests/fixtures/dbc/float_signal.dbc       合法 DBC float signal（SIG_VALTYPE_ 声明）
tests/fixtures/dbc/fd_extra_payload.dbc   FD message.length(12) < FD frame payload 的 excess 用例
```

#### Full regression（2026-09-16，本阶段实现完成时，全部为实际执行结果）

```text
full pytest                          1398 passed, 1 skipped（V0.3-03 记录为 1153 passed, 1 skipped）
  · tests/unit/dbc                    503 passed, 1 skipped
  · tests/integration（DBC 三个文件）    43 passed
  · tests/unit/project + schema upgrade 73 passed
ruff check runtime tests             exit 0（All checks passed!）
mypy runtime                         exit 0（62 source files，strict）
```

1 skipped 为既有既知项：本机无权限创建目录链接（WinError 1314），
V0.3-03 已记录同一 skip，非本轮引入。

#### Packaging

```text
scripts\package-windows.cmd          exit 0
  · [1/6] runtime build + staged sidecar   PASS
  · [2/6] staged sidecar verified           ok
  · [3/6] packaged-runtime smoke test       PASS
  · [4/6] Tauri MSI build                   Finished 1 bundle
  · [5/6] MSI artifact check                ok: CAN-X_0.1.0_x64_en-US.msi
  · [6/6] packaging complete                 exit 0

独立复核（对新构建的 exe 重跑 smoke）
  CANX_TEST_RUNTIME_EXE=<repo>\build\runtime-dist\canx-runtime.exe \
    python -m pytest tests\integration\test_packaged_runtime_smoke.py -q
  → 3 passed in 13.43s

build\runtime-dist\canx-runtime.exe             59,553,571 bytes（V0.3-03-FINAL: 59,553,459）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi   62,373,888 bytes（与 V0.3-03-FINAL 相同）
exe 内字符串出现次数：cantools 0 · bitstruct 0 · textparser 0 · duckdb 13（均与 V0.3-03-FINAL 一致）
```

packaged runtime scope（诚实说明）：本轮**不主张** packaged runtime 提供 DBC decode 能力。
`canx.dbc` 仍不在 runtime 入口的 import graph 中——grep 全 `runtime/canx`（`dbc/` 之外）
没有任何模块 import 它，本阶段也没有 HTTP API、没有组合根接线。exe 字节数相比
V0.3-03-FINAL 只有 112 字节差异，本轮**不对其做因果解释**（PyInstaller 归档的构建期差异）。
打包回归证明的是：**没有破坏**既有 runtime build / packaged smoke / Tauri package；
它**不**证明 `canx.dbc.decode` 已进入 shipped runtime。

#### Schema / 依赖变化

```text
SQLite project schema  未改（仍为 V3；未新增任何表、未触碰 migration）
project.json           未改
Parquet schema         未改
Frame / FrameBatch     未改
HTTP / WebSocket       未改（未新增 endpoint，未改 canx.api.*）
DBC canonical schema   最小变化：DbcSignal + is_float: bool（§29 correctness blocker 修复）
新增依赖               none（仅标准库：int.from_bytes · math.isfinite · MappingProxyType · frozenset）
```

#### Known limitations（诚实记录）

```text
 1  nested / extended multiplexing 不完整支持。真正的多级 mux 拓扑在 canonical model 中
    无法表达，且 cantools 44.0.0 对 `m0M` / `m0m0` 源文件会静默塌缩成「两个开关 + 其余
    看似 common」。decoder 只能检测到塌缩后的形态（>1 个开关）并拒绝整个 message，
    不能还原原始拓扑。基础单层 mux 已支持。
 2  float signal（SIG_VALTYPE_）只保留元数据，不实现解码；遇到即 dbc.decode_unsupported。
    32-bit / 64-bit IEEE-754、byte order、scaling 均未实现，也未验证。
 3  不支持 partial / truncated decode：payload 短于 message.length 一律 typed failure。
 4  container message / J1939 特定语义未实现，也未纳入本轮范围。
 5  没有 live subscription、没有 historical bulk pipeline、没有 channel ↔ DBC binding、
    没有 active DBC、没有 API / UI / Agent tool。
 6  `decoded signals 顺序 = DBC 文档声明顺序` 是 CAN-X 自己的 contract；cantools 的顺序是
    「root codec 后接 branch codec」，两者在 child 声明于 switch 之前的文档上不同。
    差分测试按 signal 名逐个比较数值，不比较顺序。
 7  性能数字是单机单次实测，未做统计重复、未做 CPU 亲和性/频率锁定；不同机器/负载下会不同。
 8  decoder 不做任何缓存：同一 Frame 重复解码会重复计算（当前无 cache 需求，避免先引入状态）。
```

#### Deferred（明确留给后续 coherent increment，本阶段一行都没做）

```text
DBC HTTP API（POST /dbc/{id}/decode · GET /dbc/messages …）
Trace decoded columns / trace.query decoded 字段 / trace.summary 改动
live WebSocket decoded-signal stream（backpressure / CPU budget / subscription）
Plot signal binding 与统一 timeline
Agent `dbc.decode` tool
DBC Editor / active DBC selection UI / channel ↔ DBC binding
float / IEEE-754 decode（含 differential tests）
nested / extended multiplexing 拓扑表达与解码
derived Parquet signal dataset / DuckDB decoded-signal query
decode cache persistence
```

#### 状态

```text
V0.3-04 DBC Decode Foundation
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做实现 + 本机自验证。**不自行宣布 V0.3-04 Final Acceptance: PASS**；
最终验收由项目负责人独立执行。若通过，下一推荐 coherent increment 由项目负责人决定
（不得由本轮自行开始）。

---

### Step V0.3-05 — DBC Runtime Read & Decode API Foundation

V0.3-04 由项目负责人独立验收：**Final Acceptance: PASS**（见本轮开工任务书）。V0.3-04 的
decode semantics 在本轮**未被修改**。

#### Objective

把 V0.3-03 的 project-owned DBC asset 与 V0.3-04 的 canonical decode engine，接到
Runtime 的 typed HTTP control plane 上：

```text
Project-owned DBC Asset
        ↓
ProjectDbcService
        ↓
canonical DbcDatabase
        ↓
DbcDecoder
        ↓
typed Runtime HTTP API
```

本轮只做：DBC asset 只读查询 + canonical DBC definition 查询 + single/batch frame
decode HTTP API。**不**扩展到前端、Trace decoded columns、WebSocket decode、Plot 或 Agent。

#### Architecture

```text
HTTP Adapter (runtime/canx/api/dbc.py)
   request validation / wire ↔ canonical conversion / result projection
        ↓
ProjectDbcService            asset source of truth (V0.3-03)
        ↓
DbcImportService / CantoolsDbcParser     唯一 cantools 边界 (V0.3-02)
        ↓
canonical DbcDatabase (V0.3-02)
        ↓
DbcDecoder                   decode source of truth (V0.3-04)
```

HTTP 层**不做**：bit extraction、Intel / Motorola decode、scaling、multiplexing、
project validation、asset integrity validation、直接调用 cantools decode API、保存
active / current DBC 全局状态、创建第二套 DBC engine。每条请求独立 `load_asset` + 独立
编译 `DbcDecoder`，进程内没有任何 active DBC 状态或 decode cache。

#### Endpoints

```text
GET  /dbc/assets?project_path=<path>
GET  /dbc/assets/{asset_id}?project_path=<path>
GET  /dbc/assets/{asset_id}/database?project_path=<path>
POST /dbc/assets/{asset_id}/decode
POST /dbc/assets/{asset_id}/decode-batch
```

新增独立模块 `runtime/canx/api/dbc.py` 与独立 `create_dbc_router()`，在 `create_app()`
中注册。`GET /dbc/assets` 只调用 `ProjectDbcService.list_assets()`，**不**扫描
`project/dbc/` 目录，也**不**自动采用未注册文件（测试用一个手工放入的 `.dbc` 验证它不被
列出）。

**未**实现 `POST /dbc/import`：现有 `ProjectDbcService.import_asset(source_path)` 会读取
Project 外部文件路径，V0.3-05 不新增一个可通过 HTTP 任意指定 local source path 的文件
读取入口。DBC import 的 desktop / Tauri filesystem boundary 后续单独设计。

#### Wire contract

- asset 投影：`asset_id / source_name / sha256 / size_bytes / encoding / imported_at`。
  不含 `relative_path`（内部布局）。顺序保持 domain 已提供的 deterministic order。
- database 投影：`version / messages[] / nodes[]`；message = `frame_id / name / length /
  is_extended / is_fd / senders / comment / cycle_time / signals[]`；signal = `name /
  start_bit / length / byte_order / is_signed / is_float / factor / offset / minimum /
  maximum / unit / receivers / choices / is_multiplexer / multiplexer_signal /
  multiplexer_ids / comment`；choice = `value / label`；node = `name / comment`。不返回任何
  cantools object / repr，也不返回 filesystem path / traceback / parser internals。
- single decode 响应：`frame / message_name / signals[]`；每个 signal = `name / raw_value /
  physical_value / choice_label / unit`。`frame` 是完整 16 字段 provenance，不是摘要。
- `data` 接受任意大小写 hex 输入，输出统一 **UPPERCASE HEX**。
- batch 响应：`schema_version / stream_id / first_sequence / last_sequence / frame_count /
  outcomes[]`；outcome = `frame / decoded | null / failure | null`，其中 `decoded` 与
  `failure` **恰好一个存在**；`failure` 保留 domain snapshot 的
  `code / message / details / recoverable / source`。

#### Shared frame wire model

Trace 已有的 16-field canonical frame response 被提取为共享模块
`runtime/canx/api/frame.py`（`FrameWire` / `frame_to_wire` / `wire_to_frame`），Trace 与
DBC 共同使用，未改变 `/trace/query` 的 wire contract、字段名、`data = uppercase hex` 与
Trace 行为。

`wire_to_frame` **不复制** Frame 的业务不变式：它只解码 hex payload，然后构造 canonical
`Frame`，由 domain 对象决定 DLC legality / arbitration id range / timestamp policy /
flags / classic-FD flag 组合是否合法。

#### Batch bound

HTTP request guard：`1 <= frame_count <= 1000`（`MAX_BATCH_FRAMES`）。这是 HTTP 端点的
边界，**不是** `FrameBatch` domain 的永久能力——domain 未被改动。frames 的 `sequence`
必须连续，因为 `FrameBatch` 定义即连续；不连续的 frames 不是 batch。

#### Error mapping

`api/errors.py` 的 `DomainError` 扩展为 `ProjectError | QueryError | DbcError`，
`status_for` 新增映射，`create_app()` 注册统一 `DbcError` exception handler：

```text
ProjectError                     → 400
DbcAssetValidationError          → 400   dbc.invalid_asset
DbcAssetNotFoundError            → 404   dbc.asset_not_found
DbcAssetIntegrityError           → 409   dbc.asset_integrity_failed
DbcAssetRegistryError            → 503   dbc.asset_registry_failed
DbcMessageNotFoundError          → 422   dbc.message_not_found
DbcFrameTypeMismatchError        → 422   dbc.frame_type_mismatch
DbcPayloadTooShortError          → 422   dbc.payload_too_short
DbcDecodeUnsupportedError        → 422   dbc.decode_unsupported
DbcSignalDecodeError             → 422   dbc.signal_decode_failed
其它无专门映射的 typed DbcError   → 500
```

`api/dbc.py` 内**没有**第二套 ErrorResponse。请求体形状与语义失败继续走全局
`api.request_validation_failed`（422 / source = api），不恢复 FastAPI 原生
`{"detail": ...}`，也不回显 caller payload。

**某个 frame decode 失败不代表 HTTP request 失败**：mixed batch 返回 200 与每帧一个
outcome；只有 request 或 asset 本身失败才是 error response。

#### Thread / offload boundary

asset load（filesystem + SQLite + hash + text decode + cantools parse）、decoder 构造、
single / batch decode 全部在 `asyncio.to_thread` 中执行，沿用 Trace API 已有模式，未引入
新 executor framework、未新增 dependency。

#### Tests added

```text
tests/unit/api/test_dbc_api.py                        63   端点 / 投影 / single decode / decode failure / request validation / batch bound
tests/integration/test_dbc_api_integration.py          8   真实链路 reopen / domain-vs-API 对照 / 双 asset 不串 / tamper 409 / 404 / 400
tests/integration/test_packaged_runtime_smoke.py      +1   packaged exe 真跑 DBC read + decode + batch
```

新增 fixture：无（复用既有 `tests/fixtures/dbc/`，全部为既有手写、license-clean fixture）。

#### Full regression（2026-09-16，本轮实现完成时，全部为实际执行结果）

```text
python -m pytest tests/unit/api -q          164 passed
python -m pytest tests/unit/dbc -q          503 passed, 1 skipped
python -m pytest tests/integration -q       238 passed（重建 exe 后）
python -m pytest -q                         1470 passed, 1 skipped
ruff check runtime tests tools              exit 0（All checks passed!）
mypy runtime                                exit 0（64 source files，strict）
```

全量用例数 1398 → 1470，净增 72（= 63 + 8 + 1，逐文件实测）。1 skipped 为既有既知项
（本机无权限创建目录链接，WinError 1314），V0.3-03 已记录同一 skip，非本轮引入。

#### Packaged runtime proof

V0.3-04 记录 "packaged runtime did NOT yet provide DBC decode / `canx.dbc` was not in the
runtime entry import graph"。本轮接入 DBC router 后该状态改变，并用真实 exe 证明：

```text
scripts\package-windows.cmd               exit 0
  · [1/6] runtime build + staged sidecar   PASS
  · [2/6] staged sidecar verified           ok
  · [3/6] packaged-runtime smoke test       4 passed in 16.95s
  · [4/6] Tauri MSI build                   Finished 1 bundle
  · [5/6] MSI artifact check                ok: CAN-X_0.1.0_x64_en-US.msi
  · [6/6] packaging complete                exit 0

独立复核（对新构建的 exe 重跑 smoke）
  CANX_TEST_RUNTIME_EXE=<repo>\build\runtime-dist\canx-runtime.exe \
    python -m pytest tests\integration\test_packaged_runtime_smoke.py -q
  → 4 passed in 18.31s
```

packaged DBC test 的真实链路：source-side 用既有 domain 创建 project 并 import 一个已知
DBC fixture → 启动新建 `canx-runtime.exe` → 对该 exe 调 `GET /dbc/assets`、
`GET /dbc/assets/{id}/database`、`POST /dbc/assets/{id}/decode`、
`POST /dbc/assets/{id}/decode-batch` → 断言 message 与 signal 数值（EngineSpeed raw 3000
→ 750 rpm、CoolantTemp 80 → 40 degC）→ 对同一运行中的进程篡改 project-owned 文件后断言
409 `dbc.asset_integrity_failed`。**不是** grep exe strings、不是 source-side import
test、不是 unit test。

```text
build\runtime-dist\canx-runtime.exe            60,116,283 bytes（V0.3-04: 59,553,571）
apps\...\binaries\canx-runtime-...-msvc.exe    60,116,283 bytes（与上一致）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi  62,939,136 bytes（V0.3-04: 62,373,888）
exe 内字符串出现次数：cantools 0 → 55 · bitstruct 2 · textparser 2 · duckdb 13（未变）
                      "dbc/assets" 0（纯 Python 模块进 PYZ 归档，不以明文出现在 exe）
```

打包前旧 exe（V0.3-04 构建）内 `dbc/assets` 与 `cantools` 均出现 0 次，且
`GET /dbc/assets` 返回 404 `{"detail":"Not Found"}`——这确认首次 smoke failure 来自
**旧 binary**，重建后同一测试通过。

`scripts\package-windows.cmd` 未修改；`packaging\canx-runtime.spec` 未修改：PyInstaller
静态分析已能从 `canx.api.app → canx.api.dbc → canx.dbc.* → cantools` 跟踪到依赖，无需
新增 hiddenimports。

#### Schema / dependency changes

```text
SQLite project schema     未改（仍为 V3；未新增表、未触碰 migration）
project.json              未改
Parquet schema            未改
Frame / FrameBatch        未改
DBC canonical schema      未改
DecodedFrame schema       未改
WebSocket schema          未改
frontend schema           未改
新增依赖                  none
新增 HTTP schema          是（/dbc/* 五个端点的 request / response model）
```

#### Known limitations（诚实记录）

```text
 1 batch 上限 1000 是 HTTP request guard，未做吞吐 benchmark；本轮不主张任何 throughput。
 2 frames 的 sequence 必须连续（FrameBatch 定义），非连续 frames 被 422 拒绝，不支持 gap。
 3 batch 的 outcome.decoded 只含 message_name + signals；frame 在 outcome 顶层，不重复。
 4 不提供 POST /dbc/import：HTTP 层不新增任意 local source path 的文件读取入口。
 5 无 active / current DBC 状态、无 channel ↔ DBC binding、无 decode cache；每条请求独立
   load + compile + decode，因此同一 asset 的重复请求会重复解析与编译。
 6 其它域未映射的 typed DbcError 统一 500，未逐类细分。
 7 未做并发 / 压测：blocking work 已 offload 到 worker thread，但未测量 event loop 在高
   并发 DBC 请求下的行为。
```

#### Deferred（明确留给后续 coherent increment，本阶段一行都没做）

```text
DBC import over HTTP（desktop / Tauri filesystem boundary 设计）
DBC Editor / active DBC selection UI / channel ↔ DBC binding
Trace decoded columns / trace.query decoded 字段
live WebSocket decoded-signal stream（backpressure / CPU budget / subscription）
Plot signal binding 与统一 timeline
Agent `dbc.decode` tool
float / IEEE-754 decode（含 differential tests）
nested / extended multiplexing 拓扑表达与解码
derived Parquet signal dataset / DuckDB decoded-signal query
decode cache / decode throughput benchmark
delete / rename / replace DBC asset
```

#### 状态

```text
V0.3-05 DBC Runtime Read & Decode API Foundation
Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮只做实现 + 本机自验证。**不自行宣布 V0.3-05 Final Acceptance: PASS**；最终验收由
项目负责人独立执行。不得由本轮自行开始 V0.3-06。

### Step V0.3-05-FINAL — HTTP Frame Input Strictness Remediation

V0.3-05 独立验收结论：**Conditional PASS**（P0: 0 · P1: 1 · P2: 1）。

#### Independent acceptance finding

P1：共享 HTTP frame wire model `FrameWire` 只配置了 `ConfigDict(frozen=True)`，Pydantic
默认 lax 模式会对 JSON primitive category 做静默转换。调用方发送 `"sequence": "1"`、
`"arbitration_id": false`、`"is_fd": 1`、`"host_timestamp": "100.25"` 等跨类别值时，
值先被转成合法 Python 类型，`wire_to_frame()` 与 canonical `Frame` 都看不到原始错误
类型，请求被接受。

P2：`runtime/canx/api/dbc.py` 模块 docstring 写 "Four endpoints"，实际为 5 个。

#### Root cause

不是 `wire_to_frame()` 或 canonical `Frame` 的缺陷——两者都没有被错误输入到达。缺的是
**wire 层的类型类别约束**：`FrameWire` 没有声明「JSON payload 的 primitive category 是
contract 的一部分」，于是 Pydantic 的 lax 转换在 domain 之前改写了问题本身。

#### Fix

```python
class FrameWire(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
```

单行配置变更（外加类 docstring 说明）。`DbcFramePayload(FrameWire)` 继承该配置，因此
single decode 与 batch decode 同时生效，无需在两个端点各写一遍。

选择 `strict=True` 而非字段级 `Strict*` 注解，依据是一条 30 秒探针实测（Pydantic
2.13.5）：

```text
ACCEPTED  int    <- int             REJECTED  int    <- str
ACCEPTED  float  <- float           REJECTED  int    <- bool
ACCEPTED  float  <- int   ← 保留    REJECTED  int    <- float
ACCEPTED  bool   <- bool            REJECTED  bool   <- int
ACCEPTED  str    <- str             REJECTED  bool   <- str
ACCEPTED  float|None <- None        REJECTED  float  <- str
ACCEPTED  literal <- literal        REJECTED  str    <- int
                                    REJECTED  literal <- int
```

关键点：`float` 在 strict 模式下**仍接受 int**，所以 `"host_timestamp": 1`（JSON int）
保持合法，而 `"host_timestamp": "1"`（JSON string）被拒——这恰好是「拒绝跨 JSON
primitive category coercion」，而不是「为了 strict 收窄合法值集合」。字段级
`StrictFloat` 会拒绝 int，反而破坏既有合法 contract，因此未被采用。

P2：`Four endpoints` → `Five endpoints`（仅文档文字，未借机重构）。

#### RED → GREEN 证据

修复前（源码探针，26 例覆盖全部字段类别）：

```text
ACCEPTED  sequence <- "1"              coerced-> 1
ACCEPTED  sequence <- true             coerced-> 1
ACCEPTED  arbitration_id <- "291"      coerced-> 291
ACCEPTED  arbitration_id <- false      coerced-> 0
ACCEPTED  is_fd <- 1                   coerced-> True
ACCEPTED  is_extended <- "false"       coerced-> False
ACCEPTED  host_timestamp <- "100.25"   coerced-> 100.25
…（共 15 例非法输入被静默接受）
```

修复后同一探针：15 例非法输入全部 `REJECTED`，5 例合法输入（JSON int → float 字段、
JSON float、`0`、`null` optional、baseline）保持 `ACCEPTED`。

回滚验证（在测试层确认新增测试确实捕获缺陷，而不是修复后补写的恒真断言）：

```text
临时移除 strict=True
  pytest tests/unit/api/test_dbc_api.py -q -k "wrong_json_category or coercion"
  → 19 failed, 7 passed, 72 deselected
恢复 strict=True
  同上 → 26 passed, 72 deselected
```

#### Why canonical Frame remains the source of domain invariants

`wire_to_frame()` 未改动：

```text
HTTP wire validation (FrameWire, strict)
   ↓
wire_to_frame()   ← 仍只做 hex 解码 + 构造 canonical Frame
   ↓
canonical Frame   ← 仍决定 DLC legality / arbitration id range /
                     classic-FD flag compatibility / timestamp finite &
                     non-negative / sequence bounds / flags bounds /
                     channel & clock-domain non-empty
```

DBC decode 请求路径上仍没有任何第二套 Frame validation；`runtime/canx/domain/frame.py`
本轮**一行未改**。本次修的是「进入 domain 之前的类型保真」，不是 domain 规则本身。

#### Tests added

`tests/unit/api/test_dbc_api.py` 新增 35 例：

```text
test_a_frame_field_sent_as_the_wrong_json_category_is_rejected    22
   integer 字段 ← string / boolean / float（sequence, arbitration_id, dlc, flags）
   boolean 字段 ← integer / string（is_extended, is_fd, bitrate_switch,
                                   error_state_indicator）
   string / Literal 字段 ← integer（channel_id, clock_domain, direction,
                                    timestamp_quality, data）
   timestamp 字段 ← numeric string（hardware / host / normalized）
test_a_legal_json_payload_still_decodes_with_its_values             9
   JSON int → float 字段、JSON float、JSON int 0、null optional、
   int 字段 ← JSON int、lowercase hex、uppercase hex
test_a_batch_member_with_a_wrong_json_category_rejects_the_request  3
   frames[1].sequence = "2" / is_fd = 0 / channel_id = 42
test_a_coercion_refusal_echoes_no_value_and_no_internals            1
```

`tests/integration/test_packaged_runtime_smoke.py` 的 packaged DBC 测试内增加 strict
检查（`"sequence": "1"` → 422 / `api.request_validation_failed` / `source = api` /
`recoverable = false`）。

batch 断言刻意同时验证「是 request 失败而非 per-frame failure」：响应体中 `outcomes`
与 `frame_count` 均不存在。`error_state_indicator` 用 `"false"` 而非 `"true"` 作为用例，
否则被 coercion 成 `True` 后同时触发 classic-frame 语义拒绝，无法区分类型拒绝与 domain
拒绝。

#### Full regression（2026-09-16/17，本轮实现完成时，全部为实际执行结果）

```text
python -m pytest tests/unit/api/test_dbc_api.py -q                  98 passed
python -m pytest tests/unit/api -q                                 199 passed
python -m pytest tests/unit/dbc -q                                 503 passed, 1 skipped
python -m pytest tests/integration/test_dbc_api_integration.py -q    8 passed
python -m pytest tests/integration -q                              238 passed
python -m pytest -q                                              1505 passed, 1 skipped
ruff check runtime tests tools                                     exit 0（All checks passed!）
mypy runtime                                                       exit 0（64 source files，strict）
```

用例总数 1470 → 1505（净增 35，与新增测试数一致）。

Trace 回归：`tests/unit/api/test_trace_query_api.py`（含 frame 16 字段集合断言与
`first["data"] == "00ABCD"` uppercase hex 断言）与
`tests/unit/api/test_request_validation_envelope.py` 全部通过；`FrameWire` 的 strict 化
未改变 `/trace/query` wire contract。1 skipped 为既有既知项（本机无权限创建目录链接
WinError 1314），非本轮引入。

#### Packaging

```text
scripts\package-windows.cmd                 exit 0
  · [1/6] runtime build + staged sidecar     PASS
  · [2/6] staged sidecar verified            ok
  · [3/6] packaged-runtime smoke test        4 passed in 17.15s
  · [4/6] Tauri MSI build                    Finished 1 bundle
  · [5/6] MSI artifact check                 ok: CAN-X_0.1.0_x64_en-US.msi
  · [6/6] packaging complete                 exit 0

独立复核（对新构建的 exe 重跑 smoke）
  CANX_TEST_RUNTIME_EXE=<repo>\build\runtime-dist\canx-runtime.exe \
    python -m pytest tests\integration\test_packaged_runtime_smoke.py -q
  → 4 passed in 18.25s

build\runtime-dist\canx-runtime.exe             60,116,826 bytes（V0.3-05: 60,116,283）
apps\...\binaries\canx-runtime-...-msvc.exe     60,116,826 bytes（与上一致）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi   62,939,136 bytes（未变）
```

#### Packaged strict-validation proof

修复前后对 packaged smoke 的实测对照（请求体发送 `"sequence": "1"`）：

```text
修复前（V0.3-05 构建的 exe，60,116,283 bytes）
  POST /dbc/assets/{id}/decode  {"sequence": "1", …}
  → 200 OK，响应中 "sequence": 1
  → 字符串被静默转换为整数并成功解码（P1 在 shipped runtime 中复现）

修复后（本轮构建的 exe，60,116,826 bytes）
  POST /dbc/assets/{id}/decode  {"sequence": "1", …}
  → 422，code = api.request_validation_failed，source = api，recoverable = false
```

该检查位于 `test_packaged_runtime_smoke.py` 的 packaged DBC 测试内，由新建
`canx-runtime.exe` 自身进程应答，证明 strict wire contract 确实进入 shipped runtime，
而不只存在于源码树。

#### Schema / dependency changes

```text
SQLite project schema     未改
project.json              未改
Parquet schema            未改
Frame schema              未改（runtime/canx/domain/frame.py 本轮一行未改）
FrameBatch schema         未改
DBC canonical schema      未改
DecodedFrame schema       未改
WebSocket schema          未改
frontend schema           未改
新增依赖                  none
HTTP wire contract 变更   FrameWire 收紧为 strict：拒绝跨 JSON primitive category；
                          合法值集合不变（JSON int 仍可用于 float 字段，null 仍合法）
```

#### Known limitations（诚实记录）

```text
 1 strict 只约束 JSON primitive category，不约束取值语义（范围 / 枚举仍由 canonical
   Frame 与 Literal 类型决定）。
 2 strict 收紧后，此前会被 lax 接受的畸形 payload 现在返回 422。这是修复目标本身，但对
   依赖旧 lax 行为的调用方属于有意的 breaking change。
 3 JSON 无 int / float 区分：`1.0` 进入 int 字段会被拒（实测），`1` 进入 float 字段仍被
   接受。后者与 canonical Frame 允许 int | float timestamp 一致。
 4 本轮未对 packaged exe 之外的分发形态（macOS / Linux 构建）验证 strict contract，
   仅 Windows 打包产物实测。
```

#### 状态

```text
V0.3-05-FINAL HTTP Frame Input Strictness Remediation
remediation implemented
Local verification complete
Awaiting independent final acceptance
```

本轮只做 P1 / P2 修复 + 本机自验证。**不自行宣布 V0.3-05 Final Acceptance: PASS**；
最终验收由项目负责人独立执行。不得由本轮自行开始 V0.3-06。


---

### Step V0.3-06 — Safe DBC Content Import API Foundation

V0.3-05 / V0.3-05-FINAL 已由项目负责人独立验收：**Independent Final Acceptance: PASS**
（P0: 0 · P1: 0 · P2: 0 · Status: CLOSED；验收时 HEAD
`f4e5c0bd81706986b3431d8da10734684e2d2453`）。本轮开工前核对 GitHub 远端 HEAD 与该值一致
（`git ls-remote origin HEAD`），未发现冲突；V0.3-05 的 read / decode contract 本轮**未修改**。

#### Objective

为 Runtime 建立安全、typed、bounded、content-based 的 DBC project import HTTP
control-plane：调用方把**已经持有的 DBC bytes** 导入为 project-owned asset，而 Runtime HTTP
API 永远不接受任意 external source path。

```text
trusted caller already holds DBC bytes
        ↓
bounded typed HTTP request（Base64 / source_name / encoding）
        ↓
DbcImportService.import_bytes      external-import identity boundary
        ↓
canonical parse / validation
        ↓
ProjectDbcService.import_asset_bytes
        ↓
project-owned immutable DBC asset（byte-for-byte）
        ↓
registry（既有 dbc_assets 表）
        ↓
既有 GET /dbc/assets · /database · /decode（V0.3-05 未改）
```

#### Architecture

```text
HTTP Adapter (runtime/canx/api/dbc.py)
   POST /dbc/assets → DbcAssetImportRequest（strict + extra=forbid）
   Base64 解码 / size guard / 结果投影
        ↓
ProjectDbcService.import_asset_bytes       content import（本轮新增）
ProjectDbcService.import_asset             path import（V0.3-03，语义未变）
        ↓           两条路径在此汇合
_persist_imported_document                 唯一 persistence body
   staging → flush + fsync → atomic promote → registry insert → 失败清理
        ↓
DbcImportService.import_bytes / import_file / load_bytes    唯一 cantools 边界
```

HTTP 层**不做**：读文件、解析 DBC、推导 asset path、写 registry、构造第二套 error envelope。

#### Security boundary（本轮核心）

V0.3-05 记录的限制在本轮**仍然成立并成为正式 contract**：

```text
Runtime HTTP API 不接受 external source path
不根据 caller path 打开外部文件
不做 caller 指定的 Path.read_bytes()
renderer / HTTP caller 无法指挥 Python Runtime 浏览任意 filesystem
```

外部文件系统 → bytes 的那一段属于未来 Tauri safe filesystem bridge，本轮只实现 Runtime 侧的
content import foundation。

三重防线（不是只有 adapter）：

```text
1. request model      extra="forbid"  → 未知字段（含 source_path）422，而非静默忽略
2. HTTP adapter       类型 / Base64 / empty / size guard
3. domain service     source_name invariant（import_bytes 内独立校验）
```

第 3 层存在的理由：未来 Tauri bridge 可能绕过 HTTP model 直接调用 domain/service，安全语义
不能只存在于唯一一个 adapter。

#### POST /dbc/assets contract

```text
POST /dbc/assets   → 201 Created + DbcAssetResponse（复用既有 schema，未新建重复投影）
GET  /dbc/assets   → 既有 list（未改）
```

同一 resource collection 上 GET = list、POST = create/import。**未**新增
`POST /dbc/import` / `/assets/import-path` / `/from-file`。

Request：

```json
{
  "project_path": "C:/projects/vehicle.canx",
  "source_name": "vehicle.dbc",
  "content_base64": "VkVSU0lPTiAiMS4wIg0K...",
  "encoding": "cp1252"
}
```

```text
project_path    CAN-X project path
source_name     provenance basename only（不是 filesystem path）
content_base64  原始 DBC bytes 的 Base64（无损）
encoding        optional codec；None 走既有默认 utf-8-sig
```

`ConfigDict(frozen=True, strict=True, extra="forbid")`：跨 JSON primitive category 的
coercion 被拒（V0.3-05-FINAL 的 strict 纪律延续到新的 request model）。

#### Base64 policy

DBC 文件不保证是 text：可能是 `utf-8-sig`、legacy codec，或含非 ASCII comment /
identifier。因此 durable import contract 传**原始 bytes 的无损表示**，不接受"解码后的
text"——后者会替调用方选定编码，使 byte-for-byte copy 与其 digest 不可验证。

本轮使用标准库 Base64，**未**引入 `python-multipart` 或任何新 dependency。

#### HTTP size bound

```text
MAX_DBC_IMPORT_BYTES = 16 MiB
```

这是 **HTTP control-plane request guard**，不是 DBC domain 的永久能力限制：domain 的 import
service 对另一个可信调用方交来的更大文档并无意见，project 层对大小没有观点。

```text
empty content            → 422
invalid Base64           → 422
decoded content > 16 MiB → 422
```

三者均为 request contract failure：`422` / `api.request_validation_failed` / `source = api`
/ `recoverable = false`。malformed Base64 **不会**被伪装成 DBC parser error。

实现上先用编码文本长度上界（`MAX_IMPORT_BASE64_CHARS`）拦截，再解码，所以超限请求不会先被
materialise 成 bytes。

#### Source-name invariant

```text
non-empty
无前后空白
basename only：无 "/"、"\"、":"（Windows drive separator）、无 control character
必须 .dbc 结尾（case-insensitive）
```

```text
vehicle.dbc              ACCEPT
BODY.DBC                 ACCEPT
a.b.c.dbc                ACCEPT
../vehicle.dbc           REJECT
C:\temp\vehicle.dbc      REJECT
/tmp/vehicle.dbc         REJECT
folder/vehicle.dbc       REJECT
vehicle.txt              REJECT
blank                    REJECT
```

拒绝值**不回显**进 `details`：这一层无法知道 caller 的字符串是不是路径或秘密，错误响应不能
变成被拒 payload 的呈现；details 只报"哪条规则失败"（`reason`）。

#### Domain / service changes

```text
DbcImportService.import_bytes(raw, *, source_name, encoding=None)   新增 public API
    external-import identity boundary：校验 source_name / 解析 exact bytes /
    return canonical DbcDocument，path = None

DbcImportService.load_bytes(...)      未改：继续服务 project-owned asset load
DbcImportService.import_file(...)     未改：path import 语义不变
```

#### Shared persistence design

两条 import 路径在 `ProjectDbcService._persist_imported_document(project_id, document, raw)`
汇合，只有一份：

```text
derive asset id / canonical relative path
↓ resolve_asset_path（与 load 同一 containment gate）
↓ _stage_asset（staging 写入 + flush + fsync + atomic promote）
↓ repository.insert_asset
↓ 失败时 best-effort 删除已写文件
```

`import_asset(path)` 的 TOCTOU gate（`_reread_verified`：validate → re-read → hash compare）
**只属于 path-based import**，原样保留；bytes import 已持有 immutable bytes snapshot，没有
第二次 filesystem read，也没有虚构一个。V0.3-03 的路径身份 / containment / symlink 逃逸 /
registry rollback / tamper detection 测试未修改且全部通过。

#### Error mapping

`api/errors.py` 的 `status_for` 新增（**未**在 `api/dbc.py` 建第二套 envelope）：

```text
ProjectError                     → 400
DbcUnsupportedFormatError        → 422   dbc.unsupported_format      ← 新增映射
DbcDecodeError                   → 422   dbc.decode_failed           ← 新增映射
DbcParseError                    → 422   dbc.parse_failed            ← 新增映射
DbcModelError                    → 422   dbc.invalid_model           ← 新增映射
DbcAssetStorageError             → 503   dbc.asset_storage_failed    ← 新增映射
DbcAssetRegistryError            → 503   （V0.3-05 已有）
DbcAssetValidationError          → 400
DbcAssetNotFoundError            → 404
DbcAssetIntegrityError           → 409
```

请求形状 / Base64 / size 失败继续走全局 `api.request_validation_failed`（422 / source = api），
不回显 caller payload，不泄漏 traceback / filesystem internals / SQLite internals /
cantools repr / raw DBC text。

#### Async / thread boundary

handler 沿用既有模式：`await asyncio.to_thread(_import_asset, request)`。Base64 decode、DBC
parse、filesystem write + fsync、hash、SQLite registry 全部在 thread 中执行；**未**引入
executor framework 或新 dependency。

诚实记录一处取舍：request contract 校验（Base64 严格性 / empty / size）位于 Pydantic
validator，即在事件循环内同步执行。它是"进入 domain 之前拒绝请求"的唯一位置——若挪进
handler，`invalid Base64` 就会与 domain 的 DBC 失败共用一条路径，丢失
`api.request_validation_failed` / `source = api` 语义。代价是接受路径上对**已受 16 MiB 上界
约束**的文本多解码一次；真正昂贵的部分（parse / fsync / registry）仍在 loop 外。

#### Tests added

```text
tests/unit/dbc/test_dbc_service.py              +34   import_bytes：bytes 保真 / 无 path /
                                                     encoding 策略 / decode-parse-model 失败 /
                                                     23 例非法 source_name / 3 例合法大小写
tests/unit/dbc/test_dbc_project_service.py      +33   import_asset_bytes：byte-for-byte /
                                                     BOM / reload 后可 decode / 无 dedup /
                                                     decoy 文件不参与 / 失败无残留
                                                     （无注册行 / 无最终文件 / 无 staging）
tests/unit/api/test_dbc_api.py                  +39   201 契约 / 既有 read + decode 全链路 /
                                                     source_name 拒绝 / 11 例 request shape /
                                                     超限 / 对照组（422 vs 400）/ 唯一内容源 /
                                                     无回显
tests/integration/test_dbc_api_integration.py    +6   create→close→POST→list/database/decode /
                                                     双 asset 不串 / 与 path import 共存 /
                                                     400 vs 422 / tamper 409 / 无 dedup
tests/integration/test_packaged_runtime_smoke.py +1   packaged exe 自己执行 import +
                                                     exact-byte proof
```

新增 fixture：无（复用既有 license-clean 手写 fixture）。

#### Full regression（2026-09-17，全部为实际执行结果）

```text
python -m pytest tests/unit/dbc -q                                  570 passed, 1 skipped
python -m pytest tests/unit/api/test_dbc_api.py -q                  137 passed
python -m pytest tests/unit/api -q                                  238 passed
python -m pytest tests/integration/test_dbc_api_integration.py -q    14 passed
python -m pytest tests/integration -q                               245 passed (87.41s)
python -m pytest -q                                              1618 passed, 1 skipped (129.82s)
ruff check runtime tests tools                                    exit 0（All checks passed!）
mypy runtime                                                      exit 0（64 source files，strict）
```

用例总数 1505 → 1618（净增 113 = unit 106 + integration 6 + packaged 1）。1 skipped 为既有已知
项（本机无权限创建目录链接 WinError 1314），非本轮引入。

#### Packaged-runtime import proof（本阶段关键验收项）

V0.3-05 的 packaged proof 是「source side 建 project + source side import asset → packaged
exe 读 / decode」。V0.3-06 把 import 本身移进 exe：

```text
source side: 只创建 EMPTY project（断言 project/dbc 为空目录）
↓ start 新构建的 canx-runtime.exe
↓ POST /dbc/assets（project_path / source_name / content_base64）
↓ packaged exe 自己：Base64 decode → canonical parse →
                   写 project-owned asset → 写 registry 行
↓ GET /dbc/assets · GET /database · POST /decode
  EngineSpeed raw 3000 → 750 rpm；CoolantTemp raw 80 → 40 degC；
  ThrottlePosition raw 128
↓ POST /dbc/assets + source_path（path-shaped extra field）→ 422
  api.request_validation_failed / source = api / recoverable = false
  → 且 asset 数仍为 1（被拒请求未创建任何 asset）
↓ shutdown exe（exit 0）
↓ source side reopen project：asset 存在 / 可 load / list_assets 恰为 1 条
```

RED 证据（同一测试对 V0.3-05-FINAL 构建的 exe 运行）：

```text
POST /dbc/assets → 405 {"detail":"Method Not Allowed"}
```

即该 endpoint 确实不在旧 shipped runtime 中，测试能捕获其缺失。

#### Exact-byte packaged proof

packaged 进程退出后，从 source side reopen project 读取 `project/dbc/<asset_id>.dbc`：

```text
stored_bytes == submitted_bytes                        PASS
sha256(stored_bytes) == sha256(submitted_bytes)        PASS
size_bytes == len(submitted_bytes)                     PASS
registry 行 sha / size / source_name / encoding 与提交一致  PASS
load_asset(asset_id) 仍解析出 EngineData                PASS
```

这排除了「parse → 重新渲染 DBC 文本 → 保存渲染结果」的可能：packaged runtime 写下的必须是
提交的原始 bytes。

#### Packaging

```text
scripts\package-windows.cmd                 exit 0
  · [1/6] runtime build + staged sidecar     PASS
  · [2/6] staged sidecar verified            ok
  · [3/6] packaged-runtime smoke test        5 passed in 21.36s
  · [4/6] Tauri MSI build                    PASS
  · [5/6] MSI artifact check                 ok: CAN-X_0.1.0_x64_en-US.msi
  · [6/6] packaging complete                 exit 0

独立复核（对新构建的 exe 重跑 smoke，不复用旧 exe）
  CANX_TEST_RUNTIME_EXE=<repo>\build\runtime-dist\canx-runtime.exe \
    python -m pytest tests\integration\test_packaged_runtime_smoke.py -q
  → 5 passed in 21.58s

build\runtime-dist\canx-runtime.exe                               60,122,752 bytes
apps\...\binaries\canx-runtime-x86_64-pc-windows-msvc.exe         60,122,752 bytes
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi                     62,943,232 bytes
（V0.3-05-FINAL：60,116,826 / 60,116,826 / 62,939,136）
```

#### Schema / dependency changes

```text
SQLite schema             未改（未新增 migration）
project.json              未改
Parquet schema            未改
Frame / FrameBatch        未改
canonical DBC model       未改
DecodedFrame              未改
WebSocket                 未改
frontend / Tauri          未改（本轮未触碰 apps/desktop/）
新增 Python dependency    none（Base64 使用标准库）
新增 Rust / JS dependency none
```

#### Known limitations（诚实记录）

```text
 1 content import 只有 HTTP / domain 入口，没有桌面入口：Tauri filesystem bridge 尚未实现，
   用户目前无法从 UI 选择 DBC 文件。
 2 size guard（16 MiB）是 HTTP 端点边界，不是 domain 能力上限；直接调用 domain 的调用方不受
   该上界约束。
 3 request contract 校验在事件循环内对已受上界约束的文本多解码一次（见 Async / thread
   boundary）。
 4 同一份内容导入两次得到两个 asset（无 dedup）——有意保留的现状，不是缺陷；asset 的生命
   周期（rename / replace / delete）仍未定义。
 5 本轮只在 Windows 上验证打包产物；macOS / Linux 构建形态未验证。
 6 未对真实 CAN 硬件 / 真实设备做任何验证（本阶段不涉及）。
```

#### Deferred（本轮明确未做）

```text
Tauri file picker / React DBC UI / drag & drop import / filesystem capability
active DBC / channel ↔ DBC binding / Trace decoded columns / WebSocket decoded stream
Plot signal binding / Agent dbc.decode
DBC Editor / delete / rename / replace asset / asset dedup
decode cache / float decode / nested & extended multiplexing
persistent decoded signals / Parquet decoded dataset / DuckDB decoded query
V0.4 work
```

#### 状态

```text
V0.3-06 Safe DBC Content Import API Foundation

Implementation complete
Local verification complete
Awaiting independent acceptance
```

本轮**不自行宣布** V0.3-06 Final Acceptance: PASS / CLOSED；最终验收由项目负责人独立执行。
不得由本轮自行开始下一阶段。


---

### Step V0.3-06-FINAL — Offload Base64 Decode From Event Loop

V0.3-06 独立验收结论：**CONDITIONAL PASS**（P0: 0 · P1: 1 · P2: 0）。

#### Independent acceptance finding

P1：`DbcAssetImportRequest` 在 Pydantic synchronous validator 中执行**完整 Base64
decode**，使最大 16 MiB decoded / 22 MiB+ encoded 的 CPU / memory work 落在 FastAPI event
loop 上；同一 payload 随后又在 worker thread 中 decode 一次。这与 V0.3-06 自述的
「decode 只在 worker 中发生一次」不一致。

#### Root cause

不是 domain 问题，也不是 request contract 语义问题，而是 **API scheduling / boundary**
问题：

```text
HTTP request
↓
Pydantic sync validation        ← 这里做了完整 decode（event loop）
↓
route handler
↓
asyncio.to_thread(...)
↓
worker 中再 decode 一次
```

一条规则需要在两处成立（validator 与 worker 都必须知道「什么算合法内容」），于是 validator 用
「调用同一个函数再丢掉结果」来实现——代价是 caller 可控的最大工作量被放到 event loop 上，并且
支付两次。

#### RED evidence（修复前实测）

5 个新测试先在旧实现上运行：

```text
python -m pytest tests/unit/api/test_dbc_api.py -q \
  -k "decodes_the_content_once or worker_thread or without_any_decode or past_the_bound"
→ 5 failed, 137 deselected

test_a_valid_import_decodes_the_content_once_off_the_event_loop
  AssertionError: one request must decode its content exactly once
  assert 2 == 1
   +  where 2 = len([5968, 6500])          ← 两个线程各 decode 一次

test_content_that_decodes_past_the_bound_is_refused_after_the_decode
  assert 3240 != 3240                     ← decode 就发生在 event-loop 线程上

test_empty_content_is_refused_without_any_decode                        FAILED
test_encoded_text_beyond_the_static_bound_is_refused_without_any_decode  FAILED
test_malformed_base64_is_detected_on_a_worker_thread                     FAILED
```

线程 id 是围绕**真实 decode** 记录的（monkeypatch 计数 wrapper），不是从
`asyncio.to_thread` 被调用推断出来的。

#### Fix

```text
runtime/canx/api/errors.py   + ApiRequestError（typed request-contract failure）
runtime/canx/api/dbc.py      validator 只做廉价静态检查；decode 唯一入口抛 ApiRequestError
runtime/canx/api/app.py      + app-level ApiRequestError handler → 共享 request-validation envelope
```

validator 现在只做两件 O(1) 判断（空文本、编码文本长度上界），**不再调用
`base64.b64decode`**；完整 decode 只发生在 `decode_import_content`，而它只被 worker 调用。

错误语义通过一个 typed exception 保持统一，而不是第二套 envelope：worker 检测到 transport
失败时抛 `ApiRequestError`，application boundary 用同一个 `request_validation_envelope()` 与
同一个 422 应答。

#### Final request pipeline

```text
HTTP request
↓
Pydantic request-shape validation    strict primitives / required fields /
                                     extra="forbid" / empty + encoded-length guard
↓
async route
↓
asyncio.to_thread(_import_asset, …)
↓
decode_import_content                ← 唯一一次完整 Base64 decode
↓
decoded-size guard（≤ MAX_DBC_IMPORT_BYTES）
↓
DBC canonical parse
↓
hash → staging → fsync → atomic promote → registry
```

#### Why request semantics remain unchanged

```text
POST /dbc/assets · 201 · DbcAssetResponse · project_path · source_name ·
content_base64 · encoding                              未变
ConfigDict(frozen=True, strict=True, extra="forbid")   未变
source_path / content / unknown field                  仍然 422
primitive coercion                                     仍然拒绝
```

失败分层仍然严格：

```text
malformed transport encoding   → 422 api.request_validation_failed（source = api）
empty content                  → 422 api.request_validation_failed（source = api）
oversize content               → 422 api.request_validation_failed（source = api）
invalid DBC grammar            → 422 dbc.parse_failed（source = dbc）
invalid declared encoding      → 422 dbc.decode_failed（source = dbc）
```

改变的是**由谁、在哪个线程检测**，不是检测结果的类别。

#### Decode-exactly-once proof

monkeypatch 计数 wrapper 包住 `decode_import_content`，成功 import 一次请求：

```text
len(threads) == 1          （修复前：2）
```

#### Worker-thread proof

同一测试同时记录 event-loop 线程 id 与 decode 线程 id：

```text
threads[0] != event_loop_thread        （修复前：相等）
```

#### Invalid-Base64 worker proof

malformed Base64（`"!!!not-base64!!!"`）在 worker 中被检测：

```text
threads == [worker_tid]  且  worker_tid != event_loop_tid
HTTP 422 / api.request_validation_failed / source = api / recoverable = false
且响应回显检查：payload 不出现在响应中、project 中未产生任何 asset
```

#### Size guard 的两层与它的可达性（诚实记录）

```text
MAX_DBC_IMPORT_BYTES = 16 MiB             HTTP request guard（非 domain 永久限制）
MAX_IMPORT_BASE64_CHARS = 4*ceil(MAX/3)   cheap 编码长度预检（validator 中执行，O(1)）
```

`MAX_IMPORT_BASE64_CHARS` 是「**能**解码到界内」的最长文本，不是「任意解码都界内」的长度：
4 个 Base64 字符携带 3 字节，因此长度恰为该值的文本在区间顶端会解码出略多于上界的字节数。
post-decode 的最终尺寸检查因此是**可达**的，不是防御性代码：

```text
len("A" * MAX_IMPORT_BASE64_CHARS) == MAX_IMPORT_BASE64_CHARS
len(b64decode(该文本, validate=True)) == 16,777,218 > 16,777,216
→ 422 api.request_validation_failed（由 worker 的最终尺寸检查拒绝）
```

该构造在测试中被断言，而不是被假设。

#### Privacy regression（未变）

```text
invalid Base64 / oversize / empty / invalid DBC / bad encoding
  → 响应不含 content_base64、raw DBC bytes/text、caller path、source_path 值、
    traceback、pydantic input/ctx、cantools、sqlite internals
  → worker 侧诊断只报「字段位置 + 规则」，不回显值
```

#### Security boundary（未变，来自 V0.3-06 验收）

```text
Runtime HTTP 只接受 content，不接受 external source path      未变
source_name basename-only .dbc / domain 自行校验规则           未变
domain 不读外部文件 / 持久化 exact bytes                       未变
共享 persistence body / 旧 path import TOCTOU / containment /
asset identity binding                                        未变
```

本轮**未修改** `runtime/canx/dbc/service.py` 与
`runtime/canx/dbc/project_service.py`。

#### Tests added

```text
tests/unit/api/test_dbc_api.py   +5
  成功 import 只 decode 一次，且不在 event loop
  malformed Base64 由 worker 检测，仍是 api.request_validation_failed
  空内容不产生任何 decode（cheap 预检）
  编码文本超上界不产生任何 decode（cheap 预检）
  解码后才超界的 payload 由 worker 最终尺寸检查拒绝（并断言该构造可达）
```

#### Full regression（2026-09-17，全部为实际执行结果）

```text
python -m pytest tests/unit/api/test_dbc_api.py -q              142 passed
python -m pytest tests/unit/api -q                              243 passed
python -m pytest tests/unit/dbc -q                              570 passed, 1 skipped
python -m pytest tests/unit/api tests/unit/dbc -q               813 passed, 1 skipped
python -m pytest tests/integration/test_dbc_api_integration.py -q   14 passed
python -m pytest tests/integration -q                           245 passed (85.41s)
python -m pytest -q                                            1623 passed, 1 skipped (144.42s)
ruff check runtime tests tools                                  exit 0（All checks passed!）
mypy runtime                                                    exit 0（64 source files，strict）
```

用例总数 1618 → 1623（净增 5，与新增测试数一致）。1 skipped 为既有已知项（本机无权限创建
目录链接 WinError 1314），非本轮引入。

#### Packaging

```text
scripts\package-windows.cmd                exit 0
  · [1/6] runtime build + staged sidecar     PASS
  · [2/6] staged sidecar verified            ok
  · [3/6] packaged-runtime smoke test        5 passed in 24.99s
  · [4/6] Tauri MSI build                    PASS
  · [5/6] MSI artifact check                 ok: CAN-X_0.1.0_x64_en-US.msi
  · [6/6] packaging complete                 exit 0

独立复核（对新构建的 exe 重跑 smoke，不复用旧 exe）
  CANX_TEST_RUNTIME_EXE=<repo>\build\runtime-dist\canx-runtime.exe \
    python -m pytest tests\integration\test_packaged_runtime_smoke.py -q
  → 5 passed in 34.06s

build\runtime-dist\canx-runtime.exe              60,123,803 bytes（V0.3-06: 60,122,752）
apps\...\binaries\canx-runtime-...-msvc.exe      60,123,803 bytes（与上一致）
apps\...\bundle\msi\CAN-X_0.1.0_x64_en-US.msi    62,947,328 bytes（V0.3-06: 62,943,232）
```

packaged 断言仍然覆盖 `source_path` extra field → 422
`api.request_validation_failed`；具体 thread id 不在 packaged binary 中检测（线程边界由
source test 证明）。

#### Schema / dependency changes

```text
SQLite schema / project.json / Parquet / Frame / FrameBatch /
canonical DBC model / DecodedFrame / WebSocket / frontend / Tauri   未改
新增依赖                                                            none（仅标准库）
```

#### Known limitations（诚实记录）

```text
 1 cheap guard 只约束编码文本长度与空文本，不能证明解码结果在界内；decode 后的尺寸检查是
   必要的第二道，且本轮在测试中构造出了它可达的用例。
 2 request-shape 校验本身（Pydantic 解析 22 MiB 级字符串）仍在 event loop 上执行——这是
   FastAPI 的同步验证阶段。本轮消除的是其中 caller 可控的**解码**工作，而不是框架验证本身。
 3 线程边界证明基于 source tree；packaged binary 未检测 thread id，其 packaged proof 覆盖
   端点行为与 exact-byte 持久化。
 4 仅 Windows 打包产物实测；macOS / Linux 构建形态未验证。
```

#### 状态

```text
V0.3-06-FINAL Offload Base64 Decode From Event Loop

remediation implemented
Local verification complete
Awaiting independent final acceptance
```

本轮只做 P1 修复 + 本机自验证。**不自行宣布** V0.3-06-FINAL Final Acceptance: PASS /
CLOSED；最终验收由项目负责人独立执行。不得由本轮自行开始下一阶段。


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