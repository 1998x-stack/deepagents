# Comparable Agent Frameworks & Harnesses

> 与 Deep Agents 架构相似的代理框架生态分析

## 框架分类

### 1. 图驱动编排运行时（最接近 LangGraph 层）

Deep Agents 运行在 **LangGraph** 之上。以下框架在编排运行时层面与之相似：

| 框架 | 语言 | Stars | 架构模式 | 核心差异 |
|------|------|-------|---------|---------|
| **LangGraph** | Python, TS | 32.2k | 有向状态图 + 条件边 + checkpoint | 持久执行的金标准；checkpoint-resume 作为一等公民 |
| **Pydantic AI** | Python | ~17k | pydantic-graph FSM + 类型参数化 Agent | 类型安全的工具契约；依赖注入；FastAPI 风格 DX |
| **Graphon** | Python | new | 基于队列的图引擎 + 事件驱动 | Dify 团队出品；比 LangGraph 更轻量 |
| **Beluga AI** | Go | new | 7 层架构 | Go 原生；持久工作流引擎与 LLM 调用分离 |

**LangGraph** (`github.com/langchain-ai/langgraph`): 被 Klarna、Uber、J.P. Morgan 使用。核心特性是**持久执行** — 代理通过 checkpoint-resume 在进程崩溃后存活（v0.3 起默认开启）。

**Pydantic AI** (`github.com/pydantic/pydantic-ai`): Python 生态中最强的类型安全竞争者。每个工具参数和输出都通过 Pydantic schema 验证。`deps_type` 依赖注入保持工具无状态和可测试。内置 OpenTelemetry 追踪。

---

### 2. 多代理角色驱动框架

| 框架 | Stars | 架构 | 核心差异 |
|------|-------|------|---------|
| **CrewAI** | 49.8k | 角色驱动 crews（sequential/parallel/hierarchical） | YAML 可配置代理；直观的 role/goal/backstory 模型 |
| **Agno** (ex-Phidata) | 40.2k | 3 层：SDK → Runtime → Control Plane | 框架无关运行时；声称 ~2µs 代理实例化 |

**CrewAI** (`github.com/crewAIInc/crewAI`): 将代理建模为具有角色、目标和背景故事的团队成员。v1.0 (2026 Q1) 新增 Flows — 包裹 Crews 的事件驱动生产工作流。月下载 520 万次。

**Agno** (`github.com/agno-agi/agno`): 从 Phidata 更名。可在其运行时内编排 LangGraph、DSPy 或 Claude Agent SDK 代理。430 贡献者，191 个 release。

---

### 3. 企业级框架

| 框架 | 语言 | Stars | 架构 | 核心差异 |
|------|------|-------|------|---------|
| **Semantic Kernel** | C#, Python, Java | 27.6k | 基于 Plugin 的 planner + kernel services | 企业模式（DI、日志、配置）；Azure 集成 |
| **Microsoft Agent Framework** | Python, .NET | — | AutoGen + Semantic Kernel 合并 | 统一 MS 代理 SDK；对话式 + 企业插件 |

**Semantic Kernel** (`github.com/microsoft/semantic-kernel`): 所有代理框架中最强的 .NET/Java 支持。Azure IAM/RBAC 治理集成。

**Microsoft Agent Framework**: 2026 年初，Microsoft 将 AutoGen + Semantic Kernel 合并为统一 SDK。AutoGen (57.4k stars) 进入维护模式。

---

### 4. TypeScript 原生框架

| 框架 | Stars | 架构 | 核心差异 |
|------|-------|------|---------|
| **Vercel AI SDK** | ~100k+ | `ToolLoopAgent` + `Agent` 接口 + LM middleware | 最成熟的 TS 代理生态；subagents-as-tools |
| **Mastra** | ~23k | 中心 `Mastra` 编排器 + `Harness` 类 | Gatsby 团队出品；内置 evals + 可观测性 |

**Vercel AI SDK** (`sdk.vercel.ai`): SDK 6 (2026) 引入 `ToolLoopAgent` 作为一等代理。关键架构特性：
- `Agent` 接口（版本化）— 自定义实现可透明替换
- `LoopControl` — `stopWhen`、`prepareStep` 每步变更 model/tools/settings
- Language model middleware — `wrapLanguageModel` 含 `transformParams`/`wrapGenerate`/`wrapStream`
- Subagents as tools — `agent.generate()` 包装在 `tool()` 定义中
- Tool approval — 每工具 `needsApproval: true`

**Mastra** (`github.com/mastra-ai/mastra`): Gatsby 团队出品。`Harness` 类 (v1.5.0, 2026 Feb) 编排多代理模式、共享状态、memory、storage。Workflows 使用 `.then()`、`.branch()`、`.parallel()` 原语。

---

### 5. 中间件驱动框架（架构最相似）

| 框架 | 语言 | 中间件模型 | 核心差异 |
|------|------|-----------|---------|
| **agent-express** | TypeScript | Express/Koa 风格 `(ctx, next)` | 3 个概念：Agent、Session、Middleware |
| **Gollem** | Go | Agent middleware chain + message/response interceptors | PII 脱敏 + 审计日志拦截器 |
| **Forge** | Python | Guardrails middleware for self-hosted tool-calling | 86.5% on 26-scenario eval with 8B local model |

**agent-express** (`agent-express.ai`): 架构上与 deepagents 最接近 — 两者都是 middleware 驱动。agent-express 将 Express 的 `(ctx, next)` 模式应用于代理；deepagents 使用 `AgentMiddleware.wrap_model_call()` 模式。

**Gollem** (`github.com/fugue-labs/gollem`): Go 中最全面的中间件链：
- Agent middleware: `LoggingMiddleware`, `TimingMiddleware`, `MaxTokensMiddleware`
- Message interceptors: 拦截/修改/丢弃传出模型请求
- Response interceptors: 过滤/转换传入模型响应
- 内置 PII 脱敏和审计日志

---

### 6. 轻量级 / 特殊用途

| 框架 | 语言 | Stars | 特点 |
|------|------|-------|------|
| **Smolagents** | Python | — | ~1000 行代码；代理写 Python 来使用工具 |
| **Orloj** | YAML/Python | — | 基础设施即代码；治理策略；分布式 worker |
| **Alphora** | Python | 347 | 全栈：ReAct/Plan-Execute, hooks, sandbox |
| **Crabtalk** | Rust | 553 | Rust 原生代理守护进程 + MCP |

---

## Deep Agents 的独特组合

没有任何其他框架在**同一个包**中同时提供以下三项：

| 特性 | Deep Agents | 最接近的替代品 |
|------|-------------|---------------|
| **规划** (`write_todos`) | ✅ 内置 | CrewAI Flows, Semantic Kernel Planners |
| **子代理委派** (`task` 工具 + 上下文隔离) | ✅ 内置 | Mastra Harness, Vercel AI SDK agent-as-tool |
| **持久文件系统工作区** (read/write/edit/glob/grep) | ✅ 内置 | Mastra workspace tools |

---

## 对比矩阵

| 维度 | Deep Agents | LangGraph | Pydantic AI | Vercel AI SDK | Mastra | CrewAI | Agno | Semantic Kernel |
|------|-------------|-----------|-------------|---------------|--------|--------|------|-----------------|
| **语言** | Python | Python, TS | Python | TypeScript | TypeScript | Python | Python | C#, Python, Java |
| **GitHub Stars** | — | 32.2k | ~17k | ~100k+ | ~23k | 49.8k | 40.2k | 27.6k |
| **License** | MIT | MIT | MIT | MIT | Apache 2.0 | MIT | Apache 2.0 | MIT |
| **编排模型** | Graph-based | Graph-based | Type-safe FSM | ToolLoop + Agent interface | Central orchestrator + Harness | Role-based crews | SDK → Runtime → CP | Plugin + Planner |
| **持久执行** | ✅ (LangGraph) | ✅ (checkpoint-resume) | ✅ (via Temporal/DBOS) | ❌ (手动) | ✅ (storage-backed) | ❌ | ❌ (stateless) | ✅ (Azure) |
| **状态管理** | TypedDict + checkpoint | TypedDict + checkpoint | `RunContext` DI | `runtimeContext` + `toolsContext` | Storage (LibSQL/PG/Mongo) | Implicit (task outputs) | Session + memory | Semantic memory + KV |
| **多代理** | Graph nodes | Graph nodes | Agent-as-tool | Agent-as-tool (via tool()) | Supervisor agents | Native crews | Agent teams | Composable kernels |
| **HITL** | `interrupt_before/after` | `interrupt_before/after` | Deferred tools | `needsApproval` on tools | Suspend/resume | Manual | Approval flows | `human_input_mode` |
| **MCP 支持** | Partial | Partial | Native | Native | Native (MCP server authoring) | Partial | Context providers | Partial |
| **可观测性** | LangSmith | LangSmith | OpenTelemetry | Built-in tracing | Built-in evals + tracing | Verbose logging | OpenTelemetry + AgentOS | Azure monitoring |
| **Profile 系统** | ✅ `HarnessProfile` | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| **Provider 无关** | ✅ | ✅ | ✅ | ✅ | ✅ (via AI SDK) | ✅ | ✅ | ✅ |
| **LangGraph 原生** | ✅ | N/A | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |

---

## 选型指南

| 场景 | 最佳选择 | 备选 |
|------|---------|------|
| **Python，最大控制，生产级** | LangGraph | Deep Agents（开箱即用） |
| **TypeScript，流式响应，Next.js** | Vercel AI SDK | Mastra |
| **TypeScript，开箱即用代理** | Mastra | deepagents.js |
| **多代理团队，快速上手** | CrewAI | AutoGen |
| **企业级，.NET/C#** | Semantic Kernel | Microsoft Agent Framework |
| **类型安全的 Python 代理** | Pydantic AI | Deep Agents |
| **Go，生产级** | Gollem | Beluga AI |
| **自托管/本地模型** | Forge | Smolagents |
| **基础设施即代码治理** | Orloj | Agno |

---

## 关键仓库

### 图驱动代理运行时
- [langchain-ai/langgraph](https://github.com/langchain-ai/langgraph) — 32.2k ⭐, MIT
- [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai) — ~17k ⭐, MIT
- [langgenius/graphon](https://github.com/langgenius/graphon) — new, Apache 2.0
- [lookatitude/beluga-ai](https://github.com/lookatitude/beluga-ai) — new, Apache 2.0

### 多代理框架
- [crewAIInc/crewAI](https://github.com/crewAIInc/crewAI) — 49.8k ⭐, MIT
- [agno-agi/agno](https://github.com/agno-agi/agno) — 40.2k ⭐, Apache 2.0

### 企业级
- [microsoft/semantic-kernel](https://github.com/microsoft/semantic-kernel) — 27.6k ⭐, MIT

### TypeScript
- [vercel/ai](https://github.com/vercel/ai) — Vercel AI SDK (monorepo)
- [mastra-ai/mastra](https://github.com/mastra-ai/mastra) — ~23k ⭐, Apache 2.0

### Go
- [fugue-labs/gollem](https://github.com/fugue-labs/gollem) — 生产级 Go 框架
- [lookatitude/beluga-ai](https://github.com/lookatitude/beluga-ai) — Go 原生 7 层架构

### 专项/研究
- [antoinezambelli/forge](https://github.com/antoinezambelli/forge) — 自托管 guardrails
- [opencmit/alphora](https://github.com/opencmit/alphora) — 全栈 hooks + sandbox
- [OrlojHQ/orloj](https://github.com/OrlojHQ/orloj) — YAML 声明式编排运行时
- [harvard-cns/orla](https://github.com/harvard-cns/orla) — 学术多 LLM 服务

---

## 参考文章

| 标题 | 链接 | 日期 |
|------|------|------|
| All Agent Harnesses: The Live Comparison | [dev.to](https://dev.to/htekdev/all-agent-harnesses-the-live-comparison-1km5) | 2026-05 |
| The Complete Guide to Agent Harness | [harness-engineering.ai](https://harness-engineering.ai/blog/agent-harness-complete-guide/) | 2026-03 |
| Architecting Agentic AI: How SDKs, Scaffolding, Frameworks & Harnesses Are Different | [Medium](https://cobusgreyling.medium.com/architecting-agentic-ai-how-sdks-scaffolding-frameworks-harnesses-are-different-0b7348deee90) | 2026-04 |
| AI Agent Frameworks in 2026: LangGraph vs Mastra vs Vercel AI SDK | [DEV](https://dev.to/alexcloudstar/ai-agent-frameworks-in-2026-langgraph-vs-mastra-vs-vercel-ai-sdk-vs-openai-agents-sdk-vs-pydantic-539b) | 2026 |
| Best AI Agent Frameworks in 2026: A Builder's Guide | [agent-harness.ai](https://agent-harness.ai/blog/best-ai-agent-frameworks-in-2026-a-builders-guide/) | 2026-03 |
| Top 10 Agentic AI Frameworks Compared (Benchmarks Inside) | [dev.to](https://dev.to/dextralabs/top-10-agentic-ai-frameworks-compared-langgraph-vs-crewai-vs-autogen-vs-benchmarks-inside-1d6g) | 2026-05 |
