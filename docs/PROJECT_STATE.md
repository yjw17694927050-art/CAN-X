# CAN-X — Project State & Long-Term Development Context

> **Document**: `docs/PROJECT_STATE.md`  
> **Purpose**: Cross-session / cross-agent project handoff  
> **Updated**: 2026-09-16  
> **Current Phase**: V0.2 — Runtime & Data Foundation · Step V0.2-02 Data Session & Parquet Segment Persistence
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

本机验证（2026-09-16，修复后重新执行）：`pytest` **157 passed**、
`ruff check runtime tests tools` exit 0、`mypy runtime` exit 0（35 source files）。

当前状态：

```text
V0.2-01 Project Foundation
Implementation complete
Independent acceptance: Conditional PASS
Final remediation completed
Awaiting final acceptance
```

未实现（刻意留在 V0.2 后续增量）：DuckDB、Query Service、Data Session、UI。

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

本机验证（2026-09-16，Windows）：

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

状态：

```text
V0.2-02 Data Session & Parquet Segment Persistence
Implementation complete
Awaiting independent acceptance
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

V0.2-01 Project Foundation 已完成实现、独立验收（Conditional PASS）与定向修复
（见 §18 Step V0.2-01），当前等待 **V0.2-01 Final Acceptance**。

V0.2-02 Data Session & Parquet Segment Persistence 已完成实现与本机验证
（见 §18 Step V0.2-02），提交并推送 GitHub，当前等待 **V0.2-02 Independent Acceptance**。
尚未开始 V0.2-03。

状态：

```text
V0.1.1 implementation                ✅ done
V0.1.1 final cleanup                 ✅ done
push GitHub                          ✅ done
independent acceptance               ✅ Conditional PASS
↓
V0.2 — Runtime & Data Foundation
├── V0.2-01 implementation           ✅ done
├── V0.2-01 independent acceptance   ◑ Conditional PASS (2 P1)
├── V0.2-01 final remediation        ✅ done
├── V0.2-01 final acceptance         ⏳ awaiting
├── V0.2-02 implementation           ✅ done
├── V0.2-02 local verification       ✅ done
├── V0.2-02 independent acceptance   ⏳ awaiting
└── V0.2-03 (next coherent increment) not started
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