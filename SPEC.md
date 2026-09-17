# CAN-X — Technical Specification

> **Document**: `SPEC.md`  
> **Product**: CAN-X  
> **Version**: 0.1-draft  
> **Date**: 2026-09-15  
> **Status**: Architecture Baseline  
> **Development Model**: Document-Driven Development
>
> 本文档定义 CAN-X 的技术架构和强制技术边界。
>
> 产品需求见 `PRD.md`。  
> Codex / AI 开发规则见 `AGENTS.md`。

---

# 1. Architecture Decision Summary

CAN-X 技术栈初始冻结为：

```text
Desktop:
Tauri 2.x

Frontend:
React 19.x
TypeScript
Vite

Workspace:
Dockview

Frontend State:
Zustand

Async State:
TanStack Query

Virtualization:
TanStack Virtual

Editor:
Monaco Editor

i18n:
i18next

Runtime:
Python 3.13.x target

API:
FastAPI

Control Plane:
HTTP / JSON

Realtime Data Plane:
WebSocket
Binary MessagePack batches

CAN:
python-can
custom Adapter Layer

DBC:
cantools

Project Metadata:
SQLite

Large Data:
Parquet

Query:
DuckDB

Analysis:
NumPy / pandas
Polars may be introduced after benchmark

Desktop System Layer:
Rust through Tauri

Agent:
custom Agent Runtime
```

具体 patch 版本由 lockfile 固定。

禁止在源码中依赖“latest”。

---

# 2. Architecture Rule

CAN-X 必须保持以下核心隔离：

```text
UI
≠
CAN Runtime
≠
Agent Runtime
≠
Data Storage
```

推荐总体架构：

```text
┌───────────────────────────────┐
│        CAN-X Desktop          │
│                               │
│ Tauri                         │
│ ┌───────────────────────────┐ │
│ │ React / TypeScript UI     │ │
│ │                           │ │
│ │ Trace   DBC    Plot       │ │
│ │ Agent   Script Diagnostics│ │
│ └──────────────┬────────────┘ │
└────────────────┼──────────────┘
                 │
        Control  │  Stream
                 │
┌────────────────▼──────────────┐
│       Python Runtime          │
│                               │
│ Device Manager                │
│ Capture Engine                │
│ Protocol Engine               │
│ Recorder                      │
│ Replay                        │
│ DBC                           │
│ Query                         │
│ Automation                    │
│ Safety Kernel                 │
└───────┬──────────────┬────────┘
        │              │
┌───────▼──────┐ ┌────▼─────────┐
│ Data Engine  │ │ Agent Runtime│
│ SQLite       │ │ Tool Registry│
│ Parquet      │ │ Planner      │
│ DuckDB       │ │ Memory       │
└──────────────┘ │ Permission   │
                 │ Task Runtime │
                 │ Sandbox      │
                 └──────────────┘
```

---

# 3. Repository Layout

初始推荐：

```text
CAN-X/
├─ PRD.md
├─ SPEC.md
├─ AGENTS.md
├─ README.md
│
├─ apps/
│  └─ desktop/
│     ├─ src/
│     ├─ public/
│     ├─ package.json
│     ├─ vite.config.ts
│     └─ src-tauri/
│
├─ runtime/
│  └─ canx/
│     ├─ api/
│     ├─ domain/
│     ├─ devices/
│     ├─ capture/
│     ├─ protocols/
│     ├─ dbc/
│     ├─ recorder/
│     ├─ replay/
│     ├─ query/
│     ├─ automation/
│     ├─ agent/
│     ├─ safety/
│     └─ project/
│
├─ tests/
│  ├─ unit/
│  ├─ integration/
│  ├─ performance/
│  └─ fixtures/
│
├─ tools/
│  ├─ generators/
│  └─ benchmarks/
│
├─ docs/
│  ├─ ADR/
│  ├─ REUSE_LEDGER.md
│  └─ architecture/
│
└─ scripts/
```

目录可以在 V0.1 前轻微调整。

但：

> UI 与 Runtime 的物理目录隔离不得取消。

---

# 4. Legacy Reuse Boundary

CAN-Space 不属于 CAN-X source tree。

禁止：

```text
CAN-X/
└─ canlab/
```

禁止直接复制：

- `mainwindow.py`；
- `tabs/`；
- PyQt GUI；
- `AppState(QObject)`；
- Qt signals；
- QThread-based architecture；
- QTableWidget Trace implementation。

CAN-X 不使用 PyQt。

---

## 4.1 允许评估复用的旧模块类型

候选包括：

- pure parsing logic；
- CAN ID utilities；
- DBC algorithms；
- protocol algorithms；
- ISO-TP；
- UDS；
- J1939；
- DoIP；
- XCP；
- log parsers；
- replay algorithms；
- signal analysis；
- reverse engineering algorithms。

前提：

- 没有 GUI 依赖；
- 没有全局 Qt State；
- 许可证允许；
- 测试通过；
- 符合 CAN-X domain interfaces。

---

## 4.2 REUSE_LEDGER

任何 legacy code 进入 CAN-X 前必须记录：

```text
Source repository:
Source path:
Original copyright:
License:
CAN-X destination:
Reuse type:
    copied
    adapted
    rewritten
Reason:
Tests:
Notes:
```

---

# 5. Process Model

至少存在：

```text
Process 1:
Tauri Desktop

Process 2:
Python CAN Runtime
```

未来可增加：

```text
Process 3:
Python Sandbox Worker

Process 4:
Local AI Runtime
```

核心原则：

> Runtime crash 不应直接造成 Desktop UI 进程崩溃。

UI 应检测：

- Runtime disconnected；
- Runtime restarting；
- Runtime unavailable。

---

# 6. Tauri Responsibilities

Rust/Tauri 负责：

- desktop lifecycle；
- window management；
- sidecar lifecycle；
- safe filesystem bridge；
- updater；
- OS integration；
- capabilities；
- process health；
- future performance bridges。

Rust 初期禁止承担：

- DBC domain logic；
- UDS；
- ISO-TP；
- J1939；
- Agent reasoning；
- reverse engineering algorithms。

只有性能测试证明 Python 是明确瓶颈时，才允许创建 Rust implementation。

---

# 7. Frontend Architecture

React 负责 UI。

React 不负责：

- CAN capture；
- durable frame storage；
- protocol state machines；
- Agent task execution；
- device handles。

---

## 7.1 Frontend State Categories

必须区分：

### UI State

例如：

```text
active panel
theme
sidebar state
layout
selection
dialog
```

使用 Zustand。

### Server/Runtime State

例如：

```text
device list
connection status
project metadata
task status
DBC state
```

通过 Runtime API 获取。

### Realtime Frame Stream

不得进入普通 React global state。

实时帧应进入：

```text
WebSocket
→ Worker
→ ring/view buffer
→ virtualized renderer
```

---

# 8. Workspace

Dockview 用于：

- dock；
- split；
- tab；
- float/popout；
- layout persistence。

Workspace model 应独立保存。

示例：

```json
{
  "workspace": "reverse-engineering",
  "version": 1,
  "panels": [],
  "layout": {}
}
```

未来需要支持：

- multi-window；
- multi-monitor；
- named layout presets。

---

# 9. Realtime Transport

CAN-X 将 API 分成两个平面。

---

## 9.1 Control Plane

使用：

```text
HTTP + JSON
```

适合：

- connect；
- disconnect；
- configuration；
- DBC；
- project；
- Agent command；
- replay control；
- query creation。

---

## 9.2 Data Plane

使用：

```text
WebSocket + Binary MessagePack
```

用于：

- CAN frames；
- live decoded signals；
- metrics；
- Agent progress；
- Plot streaming。

禁止实时 CAN 帧使用：

```text
GET /frames every 300ms
```

轮询只能用于 debug，不得用于正式 Trace。

---

# 10. Frame Domain Model

CAN-X 内部禁止把 CAN frame 定义成 pandas row。

必须存在正式 domain object/schema。

建议逻辑模型：

```text
Frame

sequence
channel_id

arbitration_id
is_extended

is_fd
bitrate_switch
error_state_indicator

dlc
data

direction

hardware_timestamp
host_timestamp
normalized_timestamp

clock_domain
timestamp_quality

flags
```

---

## 10.1 Timestamp

时间禁止只有一个：

```text
timestamp: float
```

必须允许保存：

```text
hardware_timestamp
host_timestamp
normalized_timestamp
clock_domain
timestamp_quality
```

---

# 11. Frame Batch

实时传输以 batch 为基本单位。

示意：

```text
FrameBatch

stream_id
first_sequence
last_sequence
frame_count
frames[]
```

默认 batch 大小不得在 SPEC 中永久写死。

V0.1 benchmark 应测试：

```text
50
100
250
500
1000
```

找到合理默认值。

---

# 12. Capture Pipeline

目标结构：

```text
Hardware
↓
Adapter
↓
Capture Worker
↓
Timestamp Normalizer
↓
Ring Buffer
├─ Recorder
├─ Protocol Engine
├─ Metrics
└─ Realtime Stream
```

Recorder 和 UI Stream 必须并列。

禁止：

```text
Hardware
↓
UI
↓
Recorder
```

否则 UI 卡顿会污染采集。

---

# 13. Hardware Adapter

定义统一接口，例如：

```python
class CanAdapter:
    def open(self, config): ...
    def close(self): ...
    def recv(self): ...
    def send(self, frame): ...
    def capabilities(self): ...
    def statistics(self): ...
```

具体实现：

```text
PythonCanAdapter
VirtualAdapter
PandaAdapter
VendorSpecificAdapter
```

V0.1 只要求：

```text
VirtualAdapter
```

---

# 14. Hardware Capabilities

能力不得通过品牌硬编码判断。

必须使用 capability model：

```text
classic_can
can_fd
hardware_timestamp
tx
listen_only
error_frames
bus_statistics
multi_channel
hardware_sync
```

UI 和 Agent 根据 capabilities 决定能做什么。

---

# 15. Data Storage

## 15.1 SQLite

负责：

- project metadata；
- workspace；
- device config；
- DBC index；
- marker；
- task metadata；
- audit；
- memory；
- settings。

---

## 15.2 Parquet

负责：

- large CAN datasets；
- derived signal datasets；
- analysis datasets。

按时间或数据大小分段。

---

## 15.3 DuckDB

负责：

- large dataset filtering；
- aggregation；
- time-window queries；
- Agent query preparation；
- cross-file query。

---

## 15.4 pandas

允许：

- 小型分析；
- algorithm compatibility；
- isolated working sets。

禁止：

> 把整个 50GB 工程转换成单个 pandas DataFrame。

---

# 16. Project Model

推荐：

```text
Example.canx/
├─ project.json
├─ project.db
├─ dbc/
├─ data/
├─ logs/
├─ scripts/
├─ agent/
├─ exports/
└─ cache/
```

以后支持：

```text
Export / Package Project
```

生成单一分享文件。

---

# 17. Recorder

Recorder 必须：

- background；
- chunked；
- failure detectable；
- disk error detectable；
- queue observable；
- session based。

Recorder 不得依赖 UI 是否打开。

---

# 18. Deterministic Replay

Replay domain 应支持：

```text
original timing
speed
pause
resume
single step
loop
range
trigger
channel remapping
CAN FD
TX permission
automation
```

Replay 如果发送到真实 CAN：

必须进入 TX Safety Gate。

离线 replay：

不要求 TX 授权。

---

# 19. UI Performance Model

UI 使用：

**Performance Governor**

监控：

```text
runtime frame rate
frontend queue
render latency
CPU
memory
stream lag
plot workload
```

允许自动降低：

```text
decorative animation
Trace refresh frequency
Plot sample rate
nonessential background render
```

禁止降低：

```text
CAN capture integrity
Recorder integrity
Timestamp integrity
Safety checks
```

---

# 20. Trace Architecture

禁止使用类似：

```text
for each visible refresh:
create thousands of component objects
```

Trace 必须：

- virtualized；
- incremental；
- immutable schema；
- Worker-assisted；
- filtered through query engine where appropriate。

长期若 DOM virtualization 到达性能瓶颈，可升级：

```text
Canvas / WebGL Trace Renderer
```

但不因此改变整个应用架构。

---

# 21. Plot

第一阶段：

```text
ECharts Canvas
```

Plot API 必须与 renderer 解耦。

未来允许：

```text
HighPerformanceTimeSeriesRenderer
```

用于极大数据。

---

# 22. Agent Runtime Architecture

目标：

```text
AgentRuntime
├─ Planner
├─ ToolRegistry
├─ ToolExecutor
├─ PermissionEngine
├─ ApprovalGate
├─ TaskStore
├─ Memory
├─ AuditLog
└─ ModelProvider
```

Agent 不得 import React。

Agent 不得直接访问硬件 SDK。

所有真实操作必须通过 Tool。

---

# 23. Tool Contract

Tool 必须结构化。

例如：

```text
name
description
input_schema
output_schema
risk_level
permissions
timeout
idempotency
```

风险等级建议：

```text
READ
COMPUTE
WRITE_PROJECT
TX
ECU_MUTATION
CRITICAL
```

---

# 24. Permission Model

默认：

```text
READ
COMPUTE
WRITE_PROJECT
```

可自动执行。

以下要求 Approval：

```text
TX
ECU_MUTATION
CRITICAL
```

权限必须由 Runtime 检查。

禁止仅由 UI 检查。

---

# 25. Audit

Agent 每次 Tool 调用至少记录：

```text
task_id
step_id
tool
parameters hash / sanitized parameters
time
risk level
approval
result
error
duration
```

敏感信息不得原样写入日志。

---

# 26. Python Sandbox

Sandbox 用于：

- data analysis；
- algorithm validation；
- temporary transforms；
- statistics；
- Agent-generated analysis code。

Sandbox：

- 独立进程；
- 无真实 CAN device handle；
- 无直接 TX 权限；
- 文件访问受工程范围限制；
- 默认无网络或受策略控制。

注意：

> V0.x 的受限 Worker 不宣称为绝对安全的任意代码沙箱。

---

# 27. Agent Memory

两个 namespace：

```text
project_memory
personal_engineering_memory
```

Memory item 至少包含：

```text
id
scope
content
source
confidence
created_at
updated_at
```

跨工程提升必须显式处理。

---

# 28. Model Provider

统一接口：

```text
ModelProvider

chat()
tool_call()
stream()
capabilities()
```

Provider 不进入 domain logic。

需要支持：

```text
Cloud provider
OpenAI-compatible provider
Local provider
```

---

# 29. Headless Mode

Python Runtime 必须可以不启动 GUI。

未来命令示例：

```text
canx-runtime run
canx-runtime capture
canx-runtime replay
canx-runtime test
```

V0.1 不要求实现完整 CLI，但架构不能依赖 Tauri 才能启动 Runtime。

---

# 30. Plugin Architecture

V0.x 仅做内部扩展接口。

第一批 extension points：

```text
CanAdapter
Importer
Exporter
Analyzer
Protocol
AgentTool
```

禁止插件直接获取整个 application global object。

禁止类似：

```python
register(main_window)
```

Plugin API 必须是最小能力接口。

---

# 31. i18n

所有 UI 文案：

```text
translation key
→ language resource
```

禁止：

```tsx
<button>连接设备</button>
```

长期硬编码。

应使用类似：

```tsx
t("device.connect")
```

---

# 32. Safety

任何 `send()` 最终必须经过统一 TX Policy。

逻辑：

```text
Tool / Script / UI
↓
TX Request
↓
Permission Engine
↓
ARM State
↓
Approval if required
↓
Runtime TX
↓
Audit
```

禁止 Adapter 被 UI 直接调用发送。

SAFETY-01 已将这条规则实现为 Runtime-owned Safety Kernel：

```text
Runtime 组件：runtime/canx/safety/
架构契约：    docs/architecture/SAFETY_ARCHITECTURE.md
```

Safety Kernel 是权限决策 authority，统一持有 Risk Taxonomy、Caller Model、
ARM State、Scope、Capability-based Permission、Approval、Policy Decision、
Audit Contract 与 Emergency Stop Contract。危险操作默认 DENY。

授权由**两个互相独立的维度**共同决定，而不是单一 RiskLevel：

```text
operation effect risk      这个操作对车辆做了什么（READ / TX / ECU_MUTATION / …）
required capabilities      执行它需要哪些 authority（一个集合，不是单值）
```

任何会在车辆总线上发帧的操作，无论 effect risk 是什么，都必须要求 `CAN_TX`。
因此 `diagnostic.read` 的 effect risk 是 `READ`，执行 authority 仍包含 `CAN_TX`——
不能因为"这是只读操作"而绕开真实 TX 的授权链路。

**Safety Audit contract。** Audit 事件是一组预先声明的字段，没有自由文本槽位，
也没有 `dict[str, Any]` 兜底。每一个引用字段都是**有 grammar 与长度预算的
identifier**：`caller_id` / `operation_id` / `approval_id` / `device_id` /
`channel` / `event_id`；digest 字段是校验过的 sha256；`message` 是 kernel 固定文本；
`detail` 是 kernel 自己渲染的坐标。不在其中接受 caller 可控的自由文本，
也不用"看起来像不像 secret"作为安全边界——secret 可以是任意字符串。
契约唯一定义在 `runtime/canx/safety/identifiers.py`。

**Audit transaction 语义。** authority-increasing 动作只有在**完整** audit
transaction 成功时才提交：event 准备 + event 构造 + sink 写入。任一步失败，
都必须先把 authority 回退到安全状态（rollback 只能是减少方向的动作），
再向上传播 typed fault；rollback 本身失败时抛出独立的强类型 fault。
audit 失败可以导致 authority 丢失，但**绝不**可以导致 authority 被恢复——
fail safe，不是 fail transactionally symmetric。

**Emergency Stop 语义（S22–S24）。** Emergency Stop 是**安全 epoch 边界**，
不是 pause/resume：

```text
ENGAGE      → DISARM
            → clear approvals
            → authority creation blocked（arm / confirm_arm / grant_approval 全部拒绝）
            → request cancellation of active dangerous work

RELEASE     → emergency_stop = False
            → runtime remains DISARMED
            → approvals empty
            → 危险 authority 必须被显式重建
```

**Release ≠ resume。** Stop engaged 期间不得建立也不得预置危险 authority；
release 之后既不是 ARMED，也不存在遗留 Approval。取消（cancellation）反馈同样
属于 Safety Audit 的一部分，因此其引用必须是 typed identifier / bounded
structured failure code，而不是任意子系统文本；canceller 收到的是 reason digest，
不是 operator 的原始 reason。

该文档冻结的安全不变量（S1-S24）是架构条款：任何任务指令都不得绕过。
SAFETY-01 只建立 domain / policy / state machine / contract / test，
不引入任何真实 TX、replay 发送、UDS 或 ECU 变更能力——
那些能力由后续任务的 SPEC 变更引入，并且必须经过 Safety Kernel。

---

# 33. V0.1 Exact Scope

V0.1 只实现：

```text
Tauri bootstrap
React shell
Dock workspace
Python sidecar lifecycle
FastAPI health endpoint
Virtual CAN generator
Frame domain model
Frame batch
Binary WebSocket
Trace prototype
Plot prototype
Recorder prototype
Basic performance telemetry
One read-only Agent tool
Benchmark runner
```

---

# 34. V0.1 Agent Tool

建议只实现一个无风险 Tool：

```text
trace.summary
```

输入：

```text
time range
optional channel
optional ID
```

输出：

```text
frame count
unique IDs
top IDs
frame rate
time range
```

目的：

> 验证 Agent → Tool Registry → Runtime → Data → Result 的整条架构。

V0.1 不连接真实 LLM 也可先使用 deterministic mock Agent。

---

# 35. V0.1 Benchmark

必须提供可重复生成器。

至少测试：

```text
1 channel
4 channels
8 channels

Classic CAN
CAN FD

low load
medium load
high synthetic load
```

必须记录：

```text
generated frames
captured frames
recorded frames
streamed frames

lost frames
sequence gaps

runtime CPU
desktop CPU
memory
disk write

Trace render latency
stream latency
Plot update latency
```

---

# 36. V0.1 Performance Acceptance

V0.1 最重要的是建立 baseline。

第一阶段不虚构未经测试的绝对性能数字。

但必须满足：

1. Capture / Recorder 不依赖 Trace rendering；
2. UI freeze 不导致 Runtime 停止；
3. sequence gap 可检测；
4. queue pressure 可观察；
5. Recorder backlog 可观察；
6. Runtime memory 不随运行时间无界增长；
7. Trace 只渲染可见/必要数据；
8. Plot 支持 downsample；
9. UI 动画可以单独降级。

测试完成后再把实测数字写回 SPEC。

---

# 37. Testing

测试分四层：

```text
Unit
Integration
Performance
UI/E2E
```

每个核心模块必须可在 headless 环境单测。

---

## Python

使用：

```text
pytest
```

---

## TypeScript

使用：

```text
Vitest
React Testing Library
```

---

## E2E

后续建议：

```text
Playwright
```

用于 Web UI 逻辑。

Tauri 特有操作另外建立集成测试。

---

## Rust

必须：

```text
cargo fmt
cargo clippy
cargo test
```

---

# 38. Error Model

所有跨边界错误必须结构化：

```text
code
message
details
recoverable
source
```

禁止 API 只返回：

```text
"something went wrong"
```

---

# 39. Logging

必须使用结构化日志。

日志区分：

```text
runtime
device
capture
protocol
storage
agent
security
desktop
```

禁止日志记录：

- API secret；
- raw credentials；
- unrestricted personal data。

---

# 40. Observability

Runtime 至少暴露：

```text
uptime
capture rate
stream rate
record rate
queue depth
dropped count
memory
active channel
recorder state
```

这些 metrics 同时服务：

- UI；
- tests；
- Agent；
- diagnostics。

---

# 41. Coding Constraints

禁止以下架构模式：

```text
global mutable application singleton
Qt QObject state
UI-owned CAN bus handle
UI-owned recorder
DataFrame as domain model
one giant MainWindow controller
Agent calling UI components
unsafe plugin receiving app object
```

---

# 42. Dependency Policy

增加依赖前必须回答：

```text
Why needed?
License?
Maintenance state?
Binary size?
Runtime impact?
Cross-platform?
Commercial restrictions?
Can existing dependency solve it?
```

核心优先：

- permissive open-source；
- MIT；
- Apache-2.0；
- BSD。

商业依赖只有明显价值时考虑。

---

# 43. Architecture Change

任何改变以下内容的提交必须先更新 SPEC：

- process boundaries；
- storage model；
- frame schema；
- realtime protocol；
- security model；
- Agent permissions；
- plugin architecture；
- primary technology stack。

重大设计决策建议创建：

```text
docs/ADR/ADR-XXXX-*.md
```

---

# 44. Definition of Done

一个技术任务完成必须满足：

```text
implementation
unit tests
integration tests where applicable
lint/type check
docs
error paths
cleanup paths
no TODO hiding incomplete behavior
```

性能相关任务额外要求：

```text
benchmark before
benchmark after
result recorded
```

---

# 45. Highest-Level Technical Principle

CAN-X 的核心架构必须允许未来：

```text
Desktop UI
CLI
Headless Runner
Agent
Automation
Remote Runtime
```

共同使用同一套：

> **CAN Runtime + Project Model + Tool API**

任何只适用于单个 UI 页面、但破坏上述原则的设计都应被视为架构退化。
