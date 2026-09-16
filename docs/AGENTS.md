# CAN-X — Agent Development Guide

> **Document**: `AGENTS.md`
> **Applies To**: Codex, coding agents, AI assistants and automated contributors  
> **Version**: 0.1  
> **Date**: 2026-09-15
>
> 本文件是所有 AI 开发代理进入 CAN-X 仓库后必须首先阅读的执行规则。
>
> CAN-X 使用 **Document-Driven Development**。
>
> 不理解本文档时，不得开始大规模编码。

---

# 1. Project Identity

项目名称：

**CAN-X**

CAN-X 是一个全新的：

> **Agent-native Professional CAN Engineering Workbench**

CAN-X 不是：

- CAN-Space 重命名；
- CAN-Space V2；
- CanLab fork 的继续开发；
- PyQt 项目的 UI 重构。

CAN-Space 已冻结。

它只能作为：

- legacy reference；
- algorithm reference；
- behavior reference；
- candidate code source。

---

# 2. Mandatory First Read

开始任何开发任务前，按顺序阅读：

```text
1. AGENTS.md
2. PRD.md
3. SPEC.md
```

如果存在：

```text
docs/ADR/
docs/REUSE_LEDGER.md
```

涉及对应模块时也必须读取。

---

# 3. Source of Truth

职责划分：

```text
PRD.md
= product intent

SPEC.md
= technical architecture

AGENTS.md
= development execution rules
```

代码不能反过来定义产品。

如果代码与 SPEC 冲突：

> 默认视为代码需要修改。

如果新需求会改变 PRD 或 SPEC：

> 先修改文档，再修改代码。

---

# 4. Direct Human Instructions

明确的人类当前任务指令优先于一般开发偏好。

但任何指令如果意味着：

- 绕过安全；
- 未经审计迁入旧代码；
- 把 CAN-X 退回 CAN-Space 架构；
- 隐藏失败测试；
- 伪造性能结果；

不得静默执行。

必须明确指出冲突。

---

# 5. CAN-Space Freeze Rule

CAN-Space 代码冻结。

禁止：

```text
“直接在 CAN-Space 上继续开发”
```

禁止：

```text
“先复制整个 CAN-Space，再逐步重构”
```

禁止在 CAN-X 创建：

```text
canlab/
mainwindow.py
tabs/
PyQt-based AppState
```

除非未来 SPEC 被正式修改。

---

# 6. Legacy Code Reuse Rule

任何 CAN-Space / CanLab 代码进入 CAN-X 前必须：

```text
Inspect
↓
Classify
↓
Check license
↓
Check dependencies
↓
Remove UI coupling
↓
Write migration/reuse record
↓
Write tests
↓
Import minimum required code
```

若 `docs/REUSE_LEDGER.md` 不存在：

> 第一次实际复用 legacy code 时创建。

---

# 7. Author / License Rule

CAN-X 原创代码由项目唯一作者维护。

不得把第三方代码重新声明为 CAN-X 原创。

任何复用代码必须：

- 保留必要版权；
- 保留必要许可证；
- 记录来源。

AI 不得删除 LICENSE / NOTICE，只为了让新项目“看起来原创”。

---

# 8. Forbidden Legacy Patterns

CAN-X 新代码禁止引入：

```python
from PyQt6 import ...
QObject
pyqtSignal
QThread
QTableWidget
```

除非 SPEC 未来正式改变 UI 技术栈。

禁止 legacy `AppState` 风格：

```text
one giant mutable singleton
```

禁止把：

```text
frames = pandas.DataFrame
```

作为 CAN-X canonical domain model。

---

# 9. Required Architecture

所有实现必须尊重：

```text
UI
≠
CAN Runtime
≠
Agent Runtime
≠
Storage
```

UI 不拥有：

- CAN device handle；
- recorder；
- Agent executor；
- protocol state machine。

---

# 10. Technology Baseline

除非 SPEC 改变，否则使用：

```text
Tauri 2
React 19
TypeScript
Vite
Python 3.13 target
FastAPI
WebSocket
MessagePack
python-can
cantools
SQLite
Parquet
DuckDB
```

不要擅自更换：

- Electron；
- PyQt；
- Qt/QML；
- Flutter；
- .NET；
- 全 Rust backend。

---

# 11. Rust Policy

不要因为 Tauri 使用 Rust，就把业务迁到 Rust。

Rust 初期只用于：

- Tauri；
- OS integration；
- sidecar lifecycle；
- capabilities；
- window management；
- proven performance bottlenecks。

任何把 Python 算法迁到 Rust 的工作必须提供：

```text
benchmark
identified bottleneck
expected benefit
migration cost
```

---

# 12. Development Workflow

任何非微小任务都遵循：

```text
1. Understand requirement
2. Read relevant docs
3. Inspect current implementation
4. Define expected behavior
5. Update docs if behavior/architecture changes
6. Implement smallest coherent increment
7. Unit test
8. Integration test
9. Run lint/type checks
10. Run performance test if relevant
11. Review diff
12. Report result
```

禁止：

```text
设计十个模块
→ 一次性写几千行
→ 最后才测试
```

---

# 13. Small Increment Rule

每次提交或任务应尽量只完成一个 coherent change。

例如：

```text
GOOD:
Implement Frame domain model + tests

GOOD:
Implement VirtualAdapter + tests

BAD:
Implement CAN, DBC, Agent, Plot, Replay and UI simultaneously
```

---

# 14. Test Before Claim

任何 Agent 不得声称：

```text
“已完成”
“已通过”
“性能优秀”
“无丢帧”
“跨平台正常”
```

除非真的运行了对应测试。

如果没有运行：

必须写：

```text
NOT VERIFIED
```

或：

```text
Implemented but not executed in this environment.
```

---

# 15. No Fake Compatibility

当前主要真实开发环境为 Windows。

没有真实 Mac 时：

禁止声称：

> macOS 已验证。

可以声称：

```text
macOS build architecture supported
CI compile successful
```

前提是实际 CI 通过。

真实硬件同理。

没有 Vector / PCAN / Kvaser / ZLG：

不得声称真实设备已验证。

---

# 16. Safety Rule

CAN-X 的所有真实 TX 最终必须进入：

```text
TX Policy
↓
ARM State
↓
Permission
↓
Approval when required
↓
Adapter.send
↓
Audit
```

禁止出现绕过路径。

---

# 17. Agent Safety

Agent 默认可以自动执行：

```text
READ
COMPUTE
WRITE_PROJECT
```

以下风险等级必须由 Runtime 控制：

```text
TX
ECU_MUTATION
CRITICAL
```

危险工具不能因为 AI 请求就绕过 Approval。

---

# 18. Sandbox Rule

Agent-generated Python：

必须运行在独立 Sandbox Worker。

Sandbox 不得获得：

```text
raw CAN device handle
direct python-can bus instance
TX credentials
unrestricted host filesystem
```

Sandbox 需要真实 CAN 能力时：

必须调用受控 Tool API。

---

# 19. Realtime Data Rule

禁止：

```tsx
setFrames([...allFrames, frame])
```

逐帧存储整个 CAN 历史到 React state。

禁止：

```text
poll /frames
rebuild entire table
```

作为正式 Trace 架构。

实时数据应：

```text
Runtime
→ Batch
→ Binary WebSocket
→ Worker
→ viewport buffer
→ virtualized render
```

---

# 20. Capture Integrity Rule

任何 UI 优化都不得改变：

```text
capture correctness
record correctness
timestamp correctness
sequence correctness
```

发生压力时：

允许降低 UI FPS。

不允许优先丢 Recorder 数据来保护动画。

---

# 21. Frame Model Rule

Frame 是正式 domain object/schema。

不要让 domain API 返回：

```text
pandas.Series
QTableWidgetItem
React object shape invented by UI
```

API schema 应是 Runtime 定义。

---

# 22. Timestamp Rule

不要新增只有：

```python
timestamp: float
```

的核心 Frame 模型。

时间系统必须保留扩展：

```text
hardware_timestamp
host_timestamp
normalized_timestamp
clock_domain
timestamp_quality
```

---

# 23. Device Rule

禁止 UI 判断：

```text
if brand == "Vector":
```

优先判断：

```text
adapter.capabilities
```

厂商特殊能力应进入 Adapter。

---

# 24. Persistence Rule

不要把所有数据塞进 SQLite。

不要把所有数据读进 pandas。

默认：

```text
SQLite
= metadata

Parquet
= large datasets

DuckDB
= large queries
```

---

# 25. Agent Tool Rule

任何 Agent Tool 必须具有：

```text
name
description
input schema
output schema
risk level
permission
timeout
```

不要把任意 Python function 自动暴露给 Agent。

---

# 26. No UI Tool Calling

Agent 不能：

```text
click Trace button
open dialog by DOM automation
simulate mouse to run UDS
```

Agent 必须调用结构化 Tool。

UI 只是 Tool 状态的可视化客户端。

---

# 27. Error Handling

禁止：

```python
except Exception:
    pass
```

除非明确属于 best-effort cleanup，并有注释。

跨 API 错误必须可诊断。

至少包含：

```text
error code
human message
recoverable
context
```

---

# 28. Logging

使用结构化日志。

不要：

```text
print()
```

作为长期生产日志方案。

不得打印：

- API keys；
- credentials；
- auth token。

---

# 29. Frontend Rules

React component 应尽量：

- 小；
- 单一职责；
- 无业务协议逻辑。

业务协议不能写在：

```text
TracePanel.tsx
AgentPanel.tsx
DiagnosticsView.tsx
```

---

# 30. TypeScript Rule

禁止大量：

```ts
any
```

公共 Runtime API 必须具有明确类型。

Server schema 与 frontend schema 应尽量自动生成或共享 schema 定义。

---

# 31. Python Rule

公共 Python API 必须：

- type hints；
- docstring；
- 明确异常；
- unit tests。

核心 domain 不依赖：

- FastAPI；
- UI；
- Tauri。

FastAPI 是 adapter/interface layer。

---

# 32. Dependency Rule

新增 dependency 前检查：

```text
license
maintenance
release activity
binary size
cross-platform
security
commercial limitation
```

优先 permissive open-source。

不得为了一个小 helper 引入巨大 framework。

---

# 33. Commercial Component Rule

项目策略：

> Open-source first.

只有当：

- 性能；
- 可维护性；
- 专业功能；

存在明显收益且无合适开源替代时，才建议商业组件。

任何商业 dependency 必须记录成本和授权限制。

---

# 34. UI Animation Rule

CAN-X 需要流畅动画。

但动画不得：

- 阻塞 capture；
- 阻塞 Recorder；
- 触发大量 React re-render；
- 与实时 frame rate 一一绑定。

优先：

- transform；
- opacity；
- compositor-friendly animation。

高负载时允许关闭非关键动画。

---

# 35. i18n Rule

所有正式 UI 文案使用 translation key。

禁止散落硬编码：

```text
English
中文
```

测试 fixture 可例外。

---

# 36. V0.1 Scope Guard

当前 V0.1 只做：

```text
Tauri
React shell
Dock layout
Python Runtime
Virtual CAN
Frame model
Realtime stream
Trace
Basic Plot
Recorder prototype
Metrics
One safe Agent Tool
Benchmark
```

如果任务开始扩展：

- UDS；
- J1939；
- DoIP；
- full DBC editor；
- cloud；
- account；
- license server；

应立即检查是否违反 V0.1 scope。

不要顺手增加。

---

# 37. V0.1 Suggested Build Order

建议严格按：

```text
Step 1
Repository bootstrap

Step 2
Python Runtime health endpoint

Step 3
Tauri sidecar lifecycle

Step 4
Frame domain model

Step 5
Virtual CAN generator

Step 6
Capture pipeline

Step 7
Frame batching

Step 8
Binary WebSocket

Step 9
Frontend worker

Step 10
Virtualized Trace

Step 11
Recorder

Step 12
Plot

Step 13
Metrics

Step 14
Agent Tool Registry

Step 15
trace.summary

Step 16
Performance benchmark

Step 17
V0.1 architecture review
```

除非出现技术阻塞，不要跳过基础层直接做漂亮 UI。

---

# 38. Testing Requirements

每一步必须：

```text
Design
→ Code
→ Unit Test
→ Integration Test
```

不要连续完成多个步骤后才测试。

---

# 39. Expected Commands

项目初始化后应逐渐建立以下标准命令。

Frontend:

```bash
pnpm install
pnpm lint
pnpm test
pnpm build
```

Desktop:

```bash
pnpm tauri dev
pnpm tauri build
```

Python:

```bash
python -m pytest
```

Rust:

```bash
cargo fmt --check
cargo clippy -- -D warnings
cargo test
```

实际命令发生变化时同步更新本文件。

---

# 40. Performance Tasks

涉及：

- Trace；
- capture；
- recorder；
- WebSocket；
- Plot；
- large data；

的修改不能只看“感觉更快”。

必须提供 benchmark。

记录：

```text
before
after
hardware/environment
dataset
duration
metrics
```

---

# 41. Definition of Done

一个任务只有同时满足以下条件才算 Done：

```text
[ ] Requirement understood
[ ] Architecture respected
[ ] Code implemented
[ ] Unit tests written
[ ] Unit tests pass
[ ] Integration tests pass where applicable
[ ] Types/lint pass
[ ] Error paths tested
[ ] Cleanup/lifecycle tested
[ ] Documentation updated
[ ] No hidden failing tests
[ ] No unverified performance claims
```

性能任务额外：

```text
[ ] Benchmark executed
[ ] Results recorded
```

---

# 42. Completion Report Format

完成开发任务后 Agent 应报告：

```text
Implemented
- ...

Changed files
- ...

Tests
- command
- result

Performance
- result / not applicable

Known limitations
- ...

Documentation
- updated / not needed

Next recommended step
- ...
```

不要只回复：

> Done.

---

# 43. If a Test Fails

不得：

- 删除测试；
- skip 测试；
- 降低 assertion；
- 修改 expected value；

只是为了让 CI 变绿。

必须先判断：

```text
implementation bug
test bug
requirement change
```

如果 requirement 变了：

先更新文档。

---

# 44. If Architecture Seems Wrong

Agent 可以提出更好的技术方案。

但不要直接重构核心架构。

流程：

```text
Identify issue
↓
Explain evidence
↓
Propose alternative
↓
Create/update ADR or SPEC
↓
Then implement
```

---

# 45. Do Not Overbuild

CAN-X 长期目标很大。

但当前每个阶段必须保持 scope discipline。

禁止：

> “以后可能需要，所以我现在顺便做。”

只有当前需求、SPEC 或明确 roadmap 需要时才实现。

---

# 46. No Placeholder Production Code

禁止正式实现包含：

```text
TODO implement later
pass
fake response
hardcoded demo data
```

除非：

- 明确属于 V0.1 mock；
- 文件和行为清晰标注；
- 测试明确知道它是 mock。

---

# 47. Naming

正式产品名始终：

**CAN-X**

代码 namespace 建议：

```text
canx
```

禁止新代码继续使用：

```text
canlab
can_space
CAN-Space
```

除：

- migration；
- provenance；
- reuse documentation。

---

# 48. Architecture Quality Gate

任何新功能合并前问：

```text
Can Runtime run without UI?
Can this be tested headlessly?
Can Agent call it through a Tool/API?
Does it preserve safety?
Does it scale beyond demo data?
Does it introduce legacy coupling?
```

如果多个答案为 No：

应重新考虑设计。

---

# 49. Long-Term North Star

每一次技术决策都应服务于：

```text
Professional CAN Workbench
+
Automation Runtime
+
AI Agent
+
Engineering Memory
```

CAN-X 最终要让工程师可以说：

> “分析这个问题并验证你的判断。”

然后 Agent 能够在明确的权限和安全边界内：

```text
读取
→ 分析
→ 编程
→ 执行
→ 验证
→ 记录
→ 报告
```

而不是只输出一段自然语言建议。

---

# 50. Final Agent Instruction

当你作为 Codex / AI Agent 开发 CAN-X 时：

> **优先保护架构，其次保护正确性，再考虑开发速度。**

不要为了快速实现：

- 复制旧架构；
- 绕过层次；
- 把业务塞进 UI；
- 把所有状态放入单例；
- 跳过测试；
- 伪造验证结果。

CAN-X 是一个长期项目。

每一个 V0.x 决策都应该让 V1.x 更容易，而不是留下一个以后必须再次推倒的原型。
