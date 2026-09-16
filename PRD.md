# CAN-X — Product Requirements Document

> **Document**: `PRD.md`  
> **Product**: CAN-X  
> **Version**: 0.1-draft  
> **Date**: 2026-09-15  
> **Status**: Active / Source of Product Truth  
> **Primary Author**: CAN-X sole author  
> **Development Model**: Document-Driven Development  
> **Primary Language**: Chinese + English terminology  
>
> 本文档回答三个问题：
>
> 1. CAN-X 为什么存在？
> 2. CAN-X 要成为怎样的产品？
> 3. 当前阶段做什么、不做什么？
>
> 具体技术实现以 `SPEC.md` 为准。  
> AI/Codex 开发行为以 `AGENTS.md` 为准。

---

# 1. 项目身份 / Project Identity

## 1.1 正式名称

产品正式名称：

**CAN-X**

定位：

> **Agent-native Professional CAN Engineering Workbench**

中文定义：

> **面向 CAN 工程师高强度日常工作的、Agent 原生的专业 CAN 工程工作台。**

CAN-X 不是简单的 CAN Viewer，也不是传统 CAN 软件增加一个 AI 聊天窗口。

CAN-X 的长期目标是让：

- 实时 CAN/CAN FD 数据；
- DBC；
- Trace；
- Plot；
- Diagnostics；
- UDS；
- ISO-TP；
- J1939；
- Replay；
- Automation；
- Python；
- Test Workflow；
- AI Agent；

工作在同一个工程上下文和统一 Runtime 中。

---

# 2. 与 CAN-Space / CanLab 的关系

## 2.1 CAN-Space 状态

CAN-Space 自 CAN-X 项目启动之日起进入：

**Frozen Reference State**

即：

- 不再作为 CAN-X 的主开发代码库；
- 不在 CAN-Space 内继续重构 UI；
- 不在 CAN-Space 内逐步替换 PyQt；
- 不把 CAN-X 理解为 CAN-Space V2；
- 不要求 CAN-X 与 CAN-Space 保持源码兼容；
- CAN-Space 仅作为：
  - 功能参考；
  - 算法参考；
  - 协议实现候选来源；
  - 行为对比基准；
  - 回归验证参考。

CAN-X 必须创建新的代码库和新的架构。

---

## 2.2 代码复用原则

CAN-X 可以复用 CAN-Space 中经过审计的部分代码，但：

> **Reuse is explicit, selective and auditable.**

禁止整个目录直接复制。

禁止因为“旧代码能运行”就默认迁移。

每项旧代码复用必须记录：

- 来源文件；
- 原始许可证；
- 原始版权；
- 复用原因；
- 是否修改；
- 测试状态；
- CAN-X 对应目标模块。

项目首次发生旧代码迁移时，应创建：

`docs/REUSE_LEDGER.md`

---

## 2.3 作者身份

CAN-X 的原创代码由当前项目作者独立开发和维护。

但：

> 对从 CAN-Space、CanLab 或任何第三方项目复用的代码，不得重新声明为 CAN-X 作者原创代码。

相关第三方版权和许可证必须按照其许可证要求保留。

---

# 3. 产品愿景 / Vision

CAN-X 最终应成为 CAN 工程师能够全天候、高强度使用的专业工程环境。

目标体验不是：

> “打开 AI，然后问它 CAN 是什么。”

而是：

> “Agent 与工程师共同操作同一个 CAN 工程。”

未来典型工作流：

```text
工程师：
找出踩油门时变化最明显的 CAN 信号。

CAN-X Agent：

1. 查询当前采集数据
2. 获取事件时间窗口
3. 找出变化显著的 CAN ID
4. 分析 bit / byte 熵
5. 分析周期性
6. 分析信号相关性
7. 生成候选信号
8. 自动生成 Python 验证算法
9. 在 Sandbox 中执行
10. 验证候选
11. 生成 DBC 草稿
12. 添加 Plot
13. 输出推断依据和置信度
```

工程师不需要在多个独立工具之间复制数据。

---

# 4. 核心用户 / Target Users

CAN-X 首要面向：

## 4.1 CAN 逆向分析工程师

核心能力：

- Trace；
- Filtering；
- Signal discovery；
- DBC；
- Correlation；
- Entropy；
- Periodicity；
- Change detection；
- AI-assisted reverse engineering。

---

## 4.2 ECU / 整车测试工程师

核心能力：

- CAN/CAN FD；
- ISO-TP；
- UDS；
- Diagnostic Workflow；
- Replay；
- Periodic TX；
- Test Sequence；
- Automation；
- Python Script；
- Result analysis。

---

## 4.3 综合 CAN 工程师

CAN-X 的最终目标用户。

用户可能同时完成：

```text
采集
→ 分析
→ DBC
→ 诊断
→ 自动化
→ 回放
→ 测试
→ Agent 分析
→ 报告
```

---

# 5. 产品设计原则 / Product Principles

以下原则为长期原则，不应因局部功能开发而破坏。

## P1 — Local First

核心 CAN 能力必须在无互联网环境下工作。

断网后仍需可使用：

- CAN/CAN FD；
- Trace；
- DBC；
- Plot；
- Recorder；
- Replay；
- Diagnostics；
- Script；
- Test；
- Local AI（若配置）。

云服务只能作为增强能力。

---

## P2 — Data Integrity First

在高负载情况下优先级：

```text
1. CAN RX / 原始数据完整性
2. Recorder
3. Timestamp
4. Safety
5. User interaction
6. Plot refresh
7. Trace refresh
8. Decorative animation
```

UI 可以降帧。

原始采集不能因为 UI 卡顿主动丢帧。

---

## P3 — Agent Native

所有核心能力应逐步形成结构化 Tool API。

Agent 不通过“模拟鼠标点击 UI”操作 CAN-X。

示例：

```text
trace.query
dbc.decode
dbc.create_signal
plot.create
uds.request
replay.start
test.run
script.execute
```

---

## P4 — Professional Before Feature Count

CAN-X 不追求：

> “功能菜单数量最多。”

CAN-X 追求：

> “每一个正式发布的模块都达到专业工程使用标准。”

功能按阶段进入正式版本。

---

## P5 — UI Is a Client

GUI 不是业务核心。

CAN Engine 必须可以：

- Headless；
- CLI；
- 自动化；
- Agent；
- 测试环境；

独立运行。

---

## P6 — Safety Cannot Be UI-Only

任何危险操作的安全控制必须位于后端 Runtime。

禁止仅依赖：

- React confirm；
- disabled button；
- UI checkbox；

保护 CAN TX。

---

## P7 — Architecture Before Optimization

先建立正确的数据流和职责边界。

再根据 profiling 决定是否使用 Rust 优化。

禁止提前使用 Rust 重写 Python CAN 协议能力。

---

# 6. 核心产品能力 / Product Pillars

CAN-X 长期包含以下核心工作区。

## 6.1 Connection & Device

支持：

- CAN；
- CAN FD；
- 多通道；
- Virtual CAN；
- Hardware Adapter；
- Hardware timestamp；
- Channel state；
- Bus health。

长期尽量兼容：

- Vector；
- PEAK PCAN；
- Kvaser；
- ZLG；
- Panda；
- SocketCAN；
- SLCAN；
- CANable；
- python-can ecosystem。

实际兼容性取决于 OS、厂商 SDK 和驱动。

---

## 6.2 Trace

专业级 Trace 需要：

- 高速滚动；
- Freeze；
- Follow；
- ID filter；
- mask/range/filter expression；
- channel filter；
- RX/TX；
- CAN/CAN FD；
- changed bytes；
- timestamp；
- relative timestamp；
- delta；
- markers；
- search；
- grouping；
- export；
- large dataset query。

Trace 不允许将全部数据放入 React state。

---

## 6.3 DBC

需要支持：

- DBC import；
- DBC edit；
- signal decode；
- message definition；
- signal definition；
- factor；
- offset；
- endian；
- signed/unsigned；
- enum；
- multiplexing；
- validation；
- DBC draft；
- Agent-assisted generation。

---

## 6.4 Plot

专业 Plot 需要：

- 多 signal；
- zoom；
- pan；
- cursor；
- measurement；
- marker；
- event overlay；
- raw/decoded compare；
- downsampling；
- large dataset；
- synchronized timeline。

---

## 6.5 Recorder & Replay

Recorder：

- 长时间记录；
- 分块写盘；
- 后台运行；
- UI 与录制解耦。

Replay：

- deterministic replay；
- original timing；
- speed multiplier；
- pause；
- step；
- loop；
- trigger；
- channel mapping；
- CAN FD preservation；
- automation integration。

---

## 6.6 Diagnostics

长期包括：

- ISO-TP；
- UDS；
- Diagnostic Sessions；
- Read DID；
- Write DID；
- DTC；
- Routine Control；
- ECU Reset；
- Security Access；
- DoIP；
- J1939；
- OBD-II。

危险服务必须经过权限控制。

---

## 6.7 Automation & Script

CAN-X 应包含专业脚本工作区：

- Python；
- Monaco-style editor；
- syntax highlighting；
- completion；
- output；
- test results；
- Agent-generated code；
- sandbox execution；
- workflow integration。

---

# 7. Agent 产品需求

## 7.1 Agent 模式

CAN-X 使用：

**Semi-Autonomous Agent**

Agent 可以自主：

- 查询；
- 分析；
- 过滤；
- 创建 Plot；
- 生成脚本；
- 执行安全分析代码；
- 创建 DBC 草稿；
- 整理测试结果；
- 运行低风险分析流程。

---

## 7.2 高风险操作

以下操作默认需要 Approval：

- CAN TX；
- Frame Injection；
- UDS Write；
- ECU Reset；
- Routine Control 中危险动作；
- Security Access；
- Flash；
- Fuzzing；
- Gateway TX；
- 修改 ECU 状态的操作。

Agent 不得绕过 Approval。

---

## 7.3 Agent Runtime

长期需要：

```text
Planner
Tool Registry
Tool Executor
Permission Engine
Approval Gate
Memory
Task Runtime
Checkpoint
Audit Log
Python Sandbox
```

---

## 7.4 多步骤任务

Agent 可以自主：

```text
Plan
→ Execute
→ Observe
→ Analyze
→ Re-plan
→ Continue
```

仅在：

- 高风险操作；
- 权限不足；
- 关键不可逆决策；

时要求人工确认。

---

## 7.5 Task Persistence

Agent 任务必须支持：

- pause；
- resume；
- retry；
- checkpoint；
- task history；
- failure recovery；
- execution trace。

长期任务不应因 UI 关闭而自动丢失。

---

# 8. Agent Memory

采用两层记忆。

## 8.1 Project Memory

仅属于当前工程：

- 已识别 ECU；
- 信号假设；
- DBC 推断；
- 用户标注；
- 已确认结论；
- 诊断记录；
- 测试结果；
- Agent evidence。

---

## 8.2 Personal Engineering Memory

跨工程保存：

- 用户习惯；
- 常用分析方法；
- 已确认经验；
- 通用 ECU 特征；
- 常见诊断模式；
- 工程工作流偏好。

跨工程记忆必须具有：

- 来源；
- confidence；
- 创建时间；
- 可编辑；
- 可删除；
- privacy boundary。

项目私有数据不得自动提升为跨工程记忆。

---

# 9. AI Provider

CAN-X 必须同时支持：

## Cloud Models

通过统一 Provider 接口连接云模型。

## Local Models

允许连接：

- Ollama；
- OpenAI-compatible local API；
- 后续其它本地 Runtime。

Agent 不得直接依赖某一家模型供应商 API。

---

# 10. UI / UX

## 10.1 总体方向

UI 采用：

> **Modern lightweight shell + professional high-density workspace**

不是传统 PyQt Tab 堆叠式 UI。

不是纯网页 Dashboard。

---

## 10.2 视觉目标

主要参考已确定的视频交互方向。

强调：

- smooth transition；
- fast navigation；
- low visual friction；
- panel continuity；
- restrained animation；
- professional density。

---

## 10.3 Workspace

必须支持：

```text
Dock
Split
Resize
Float
Pop-out
Multi-window
Multi-monitor
Save layout
Restore layout
Workspace preset
```

---

## 10.4 多显示器

典型布局：

```text
Display 1:
Trace + DBC

Display 2:
Plot + Timeline

Display 3:
Diagnostics + Agent
```

---

## 10.5 Animation

普通交互目标：

约 60 FPS 视觉体验。

但性能压力下允许：

- animation degrade；
- Plot downsampling；
- Trace refresh throttling。

Recorder / CAN RX 不受影响。

---

# 11. 国际化

CAN-X V1 必须支持：

- 简体中文；
- English。

所有用户可见文字必须通过 i18n。

禁止在业务组件中大量硬编码中文或英文。

---

# 12. 数据规模

CAN-X 按专业级长时间使用设计。

## V1 目标

稳定处理：

**10–50 GB 级工程数据。**

## Architecture Target

允许未来：

**100 GB+**

因此禁止：

> 整个日志一次性加载到 pandas / React。

数据必须支持：

- chunking；
- streaming；
- indexing；
- query-on-demand；
- downsampling。

---

# 13. 时间模型

CAN-X 必须从一开始区分：

- hardware timestamp；
- host receive timestamp；
- normalized timestamp；
- clock domain；
- sync quality；
- channel。

长期需要支持：

- 多 CAN 通道；
- LIN；
- Ethernet；
- DoIP；
- 视频；
- sensor；

统一时间轴。

---

# 14. 工程文件

工程真实存储形式：

```text
project/
├─ project.db
├─ project.json
├─ dbc/
├─ logs/
├─ data/
├─ scripts/
├─ agent/
├─ exports/
└─ cache/
```

其中：

- SQLite 保存结构化元数据；
- 大型时序数据独立存储；
- 支持未来打包为单个可分享工程文件。

---

# 15. 插件策略

V1：

**Internal Plugin Architecture**

暂不公开完整第三方 Plugin SDK。

优先定义：

- Hardware Adapter；
- Protocol Adapter；
- Analyzer；
- Importer；
- Exporter；
- Agent Tool。

公开 SDK 在 API 稳定后再发布。

---

# 16. 商业方向

长期倾向：

**Open Core + Professional + Agent**

潜在层级：

```text
CAN-X Community
CAN-X Professional
CAN-X Agent
CAN-X Enterprise
```

V0.x / V1 初期不投入账号、订阅、云同步系统。

但架构预留：

- Auth；
- Licensing；
- Cloud Sync；
- Team Workspace。

---

# 17. V0.1 — Technology Proof

CAN-X 第一个开发阶段不是完整 CAN 软件。

V0.1 只验证核心架构。

## V0.1 必须包含

1. Tauri Desktop Shell；
2. React + TypeScript UI；
3. Python Runtime sidecar；
4. Virtual CAN generator；
5. Realtime binary stream；
6. Trace；
7. Basic Plot；
8. Recorder prototype；
9. Minimal Agent Tool；
10. Performance benchmark。

---

## V0.1 不包含

- 完整 UDS；
- 完整 J1939；
- 真正硬件适配矩阵；
- Cloud；
- License server；
- Team collaboration；
- 完整 Plugin SDK；
- 所有 CAN-Space 功能迁移；
- 完整 Agent autonomous workflow；
- ECU flashing。

---

# 18. V0.1 成功标准

V0.1 成功不是：

> UI 能打开。

而是验证：

```text
Virtual CAN
→ Python Runtime
→ Buffer
→ Recorder
→ Realtime Stream
→ React Trace
→ Plot
→ Agent Tool
```

整个链路成立。

必须测量：

- ingest rate；
- displayed frame rate；
- dropped frames；
- queue depth；
- memory；
- CPU；
- recorder throughput；
- UI responsiveness；
- startup time。

必须验证：

> UI 压力不会导致采集链路主动停止或明显丢失原始数据。

---

# 19. 版本路线

建议：

## V0.1
Technology Proof

## V0.2
Runtime / Data foundation

## V0.3
Professional Trace / DBC foundation

## V0.4
Plot / Recorder / Replay

## V0.5
Project / Automation foundation

## V1.0
专业 CAN/CAN FD 基础工作台

至少达到专业级：

- Device；
- CAN/CAN FD；
- Trace；
- DBC；
- Plot；
- Recorder；
- Replay；
- Project；
- basic Agent。

后续：

## V1.1
Diagnostics / UDS / ISO-TP

## V1.2
Automation / Test / J1939

## V1.3
Reverse Engineering Intelligence

## V1.x
Advanced Agent / XCP / DoIP / Plugin ecosystem

---

# 20. 非目标 / Non-Goals

当前明确不做：

- CAN-Space 原地升级；
- 继续维护 PyQt UI；
- CAN-X 与 CAN-Space API 兼容；
- 全 Rust 重写；
- 一开始开发所有 CAN 功能；
- 第一阶段开发云平台；
- 第一阶段开放完整第三方插件；
- 把所有实时帧塞入 React state；
- 把全部历史数据塞进 SQLite；
- 让 Agent 绕过 TX 安全系统。

---

# 21. Product Definition of Done

任何功能只有满足以下全部条件才能称为完成：

```text
Requirement defined
Architecture defined
Implementation complete
Unit tests pass
Integration tests pass
Safety reviewed
Performance reviewed where applicable
Documentation updated
No undocumented architectural shortcut
```

---

# 22. 文档驱动规则

CAN-X 执行：

**Document-Driven Development**

重大功能顺序必须为：

```text
PRD
↓
SPEC
↓
Implementation Plan
↓
Code
↓
Unit Test
↓
Integration Test
↓
Performance Test
↓
Documentation Update
```

如果实现改变架构：

> 先更新 SPEC，再修改代码。

如果实现改变产品需求：

> 先更新 PRD，再修改 SPEC 和代码。

禁止：

> “代码已经写完，再回头修改文档让它看起来合理。”

---

# 23. CAN-X 的最终方向

CAN-X 的长期目标不是：

> Another CAN Tool.

而是：

> **An engineering runtime where CAN engineers and AI agents work on the same vehicle data, tools, workflows and evidence.**

最终产品应同时具备：

```text
Professional CAN Workbench
+
Engineering Automation
+
Agent Runtime
+
Engineering Memory
```

这是 CAN-X 后续所有架构和产品决策的最高级约束。
