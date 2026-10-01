# Deep Agents Harness Architecture

> 内部架构分析 — `create_deep_agent()` 装配流水线的完整拆解

## 概述

Deep Agents 的 "harness"（脚手架/运行壳）不是一个单一的类或模块，而是一套由 `create_deep_agent()` (位于 `libs/deepagents/deepagents/graph.py`) 驱动的完整装配流水线，将裸 chat model 转化为具备规划、文件系统、子代理和上下文管理能力的自主代理。

## 八层架构

```
create_deep_agent(model, tools, system_prompt, middleware, subagents, ...)
    │
    ├─ Layer 1: Model Resolution   (ProviderProfile → BaseChatModel)
    ├─ Layer 2: Harness Profile    (per-model 运行时调优)
    ├─ Layer 3: Middleware Stack   (13+ 层有序 AgentMiddleware)
    ├─ Layer 4: System Prompt      (USER → BASE/CUSTOM → SUFFIX 四段式)
    ├─ Layer 5: Subagent System    (SubAgent | CompiledSubAgent | AsyncSubAgent)
    ├─ Layer 6: State Management   (DeltaChannel, O(N) checkpoint 增长)
    ├─ Layer 7: Backend            (State | Filesystem | Composite | Sandbox)
    └─ Layer 8: → CompiledStateGraph (LangGraph native)
```

---

## Layer 1: Model Resolution

**文件**: `_models.py` + `provider/provider_profiles.py`

`"provider:model"` 字符串（如 `"anthropic:claude-sonnet-4-6"`）通过 `resolve_model()` 解析为 `BaseChatModel` 实例，结合 `ProviderProfile` 的额外参数控制模型构造阶段。也可以直接传入预初始化的模型实例。

```python
# 字符串方式
agent = create_deep_agent(model="openai:gpt-4o")

# 预初始化实例方式
from langchain.chat_models import init_chat_model
model = init_chat_model("anthropic:claude-sonnet-4-6")
agent = create_deep_agent(model=model)
```

---

## Layer 2: Harness Profile

**文件**: `profiles/harness/harness_profiles.py` (1316 行)

`HarnessProfile` 是 per-model 运行时配置的冻结数据类，按 `provider:model` 键注册到全局 `_HARNESS_PROFILES` 注册表。Profile 采用**叠加合并**语义：provider 级默认值 + model 级覆盖。

```python
@dataclass(frozen=True)
class HarnessProfile:
    base_system_prompt: str | None = None        # 替换 BASE_AGENT_PROMPT
    system_prompt_suffix: str | None = None       # 追加在末尾的模型调优指令
    tool_description_overrides: Mapping[str, str]  # per-tool 描述重写
    excluded_tools: frozenset[str]                # 从可见工具集中移除的工具名
    excluded_middleware: tuple[...]               # 要剥离的中间件（受保护脚手架不可排除）
    extra_middleware: tuple[...]                   # 额外的中间件实例/工厂
    general_purpose_subagent: GeneralPurposeSubagentProfile  # 默认子代理调优
```

**内置 Profiles**:
| Profile | 文件 | 特点 |
|---------|------|------|
| `claude-sonnet-4-6` | `_anthropic_sonnet_4_6.py` | 并行工具调用 + 先调查后回答 |
| `claude-opus-4-7` | `_anthropic_opus_4_7.py` | 额外工具/子代理使用引导 |
| `claude-haiku-4-5` | `_anthropic_haiku_4_5.py` | 与 Sonnet 相同的通用 Claude 引导 |
| `gpt-*-codex` | `_openai_codex.py` | 自主高级工程师 + 并行工具使用 |

**第三方扩展**: 通过 `deepagents.harness_profiles` 和 `deepagents.provider_profiles` 入口点自动发现。

---

## Layer 3: Middleware Stack

**核心设计模式**: 一切皆 `AgentMiddleware`。中间件在 LLM 调用**之前**拦截（过滤工具、注入 prompt、转换消息），而不仅仅在工具执行之后。

### 完整堆叠顺序（graph.py L670-746）

| 位置 | 中间件 | 功能 | 可选性 |
|------|--------|------|--------|
| 1 | `TodoListMiddleware` | `write_todos` 规划工具 | 自动 |
| 2 | `SkillsMiddleware` | 加载 SKILL.md 文件 | 条件（有 skills 参数时） |
| 3 | **`FilesystemMiddleware`** | `ls/read/write/edit/glob/grep` + `execute` | **受保护脚手架** |
| 4 | **`SubAgentMiddleware`** | `task` 工具：同步子代理委派 | **受保护脚手架** |
| 5 | `AsyncSubAgentMiddleware` | 远程 LangGraph 部署的异步子代理工具 | 条件（有 async subagents 时） |
| 6 | `SummarizationMiddleware` | 上下文超限时自动摘要 | 自动 |
| 7 | `PatchToolCallsMiddleware` | 工具调用 ID 修补 | 自动 |
| 8 | *用户中间件* | `create_deep_agent(middleware=[...])` | 用户提供 |
| 9 | *Profile extra_middleware* | HarnessProfile 的额外中间件 | Profile 提供 |
| 10 | `_ToolExclusionMiddleware` | 剥离被排除的工具名 | 条件（有 excluded_tools 时） |
| 11 | `AnthropicPromptCachingMiddleware` | Prompt 缓存（非 Anthropic 模型无操作） | 无条件 |
| 12 | `MemoryMiddleware` | 加载 AGENTS.md 文件到系统提示 | 条件（有 memory 参数时） |
| 13 | `HumanInTheLoopMiddleware` | 人工审批中断 | 条件（有 interrupt_on 时） |

### 受保护脚手架

`FilesystemMiddleware` 和 `SubAgentMiddleware` **不可被排除**（`_REQUIRED_MIDDLEWARE`）。这保证了文件操作和子代理派发始终可用。只能通过 `excluded_tools` 或禁用 GP 子代理来移除相应功能。

### 关键中间件详情

#### FilesystemMiddleware (`middleware/filesystem.py`, 2193 行)
- 提供工具：`ls`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`
- 条件工具：`execute`（仅当 backend 实现 `SandboxBackendProtocol` 时）
- 权限系统：`FilesystemPermission` 规则（allow/deny by path pattern），先声明先匹配
- 子代理继承父级权限，除非显式覆盖

#### SubAgentMiddleware (`middleware/subagents.py`, 683 行)
- 提供 `task` 工具用于委派给子代理
- 三种子代理形态：
  - `SubAgent` — 声明式 spec（name, description, system_prompt, model, tools, middleware...）
  - `CompiledSubAgent` — 预构建的 runnable graph
  - `AsyncSubAgent` — 远程 LangGraph 部署（graph_id, url, headers）
- 自动添加 `general-purpose` 默认子代理（除非 HarnessProfile 禁用）

#### SummarizationMiddleware (`middleware/summarization.py`)
- 私有 `_DeepAgentsSummarizationMiddleware`：统计 token、截断旧工具参数、用摘要替换历史

#### MemoryMiddleware (`middleware/memory.py`)
- 从可配置源加载 AGENTS.md 文件
- 支持 Anthropic 模型的 cache control 断点

---

## Layer 4: System Prompt Assembly

**文件**: `graph.py` L69-141, L748-754

四段式拼接，顺序固定：

```
USER ──── 调用者的 system_prompt 参数（str 或 SystemMessage）
  ↓
BASE ──── BASE_AGENT_PROMPT（或被 profile 的 base_system_prompt 替换）
  ↓
SUFFIX ── profile 的 system_prompt_suffix（如有）
```

- **`USER` 始终最前**：调用者指令优先于 SDK 和 profile 内容
- **`SUFFIX` 始终最后**：模型调优指导最接近对话历史（模型注意力最高区域）
- 当 `USER` 是 `SystemMessage` 时，保留其 `cache_control` 标记

---

## Layer 5: Subagent System

### 子代理类型

| 类型 | 用途 | 关键字段 |
|------|------|---------|
| `SubAgent` | 声明式同步子代理 | name, description, system_prompt, model?, tools?, middleware?, permissions?, interrupt_on?, skills? |
| `CompiledSubAgent` | 预构建 runnable | name, description, runnable |
| `AsyncSubAgent` | 远程/后台子代理 | name, description, graph_id, url?, headers? |

### 默认通用子代理

自动添加名为 `general-purpose` 的子代理，继承主代理的 tools、model 和 middleware。可通过 `GeneralPurposeSubagentProfile(enabled=False)` 禁用。

### SubagentTransformer (`_subagent_transformer.py`)

LangGraph stream transformer，将子代理执行提升为类型化的 `SubagentRunStream` / `AsyncSubagentRunStream` 句柄，追踪 `subagent_type`、`tool_call_id` 和 `task_input`。

---

## Layer 6: State Management

**文件**: `graph.py` L63-67 + `_messages_reducer.py`

```python
class _DeepAgentState(AgentState):
    messages: Required[Annotated[list[AnyMessage], 
        DeltaChannel(_messages_delta_reducer, snapshot_frequency=50)]]
```

- 自定义 `DeltaChannel` reducer：按消息 ID 去重、通过 `RemoveMessage` 逻辑删除、支持 `REMOVE_ALL_MESSAGES`
- **Checkpoint 增长从 O(N²) 降至 O(N)**
- `snapshot_frequency=50`：每 50 步做一次完整快照

---

## Layer 7: Backend Abstraction

**文件**: `backends/protocol.py` + `backends/__init__.py`

所有 I/O 通过 `BackendProtocol` 实现：

| Backend | 用途 |
|---------|------|
| `StateBackend` | 临时内存存储（默认） |
| `FilesystemBackend` | 本地磁盘（支持虚拟模式） |
| `LocalShellBackend` | 文件系统 + Shell 执行 |
| `SandboxBackend` | 远程沙箱（LangSmith, AgentCore, Daytona, Modal, Runloop） |
| `CompositeBackend` | 按子路径路由到不同 backend |
| `StoreBackend` | 基于 LangGraph Store 的持久存储 |
| `ContextHubBackend` | 远程上下文存储 |

---

## Layer 8: CompiledStateGraph

最终 `create_deep_agent()` 返回 `langchain.agents.create_agent()` 编译的 LangGraph 图，配置：

```python
.with_config({
    "recursion_limit": 9_999,
    "metadata": {
        "ls_integration": "deepagents",
        "versions": {"deepagents": __version__},
        "lc_agent_name": name,
    },
})
```

- 原生支持 LangGraph 全部特性：streaming、checkpointers、store、Studio
- 默认递归上限 9,999 步

---

## CLI Overlay

**文件**: `libs/cli/deepagents_cli/agent.py`

`create_cli_agent()` 在 SDK 基础上叠加 CLI 专属中间件：

| 中间件 | 功能 |
|--------|------|
| `ConfigurableModelMiddleware` | 运行时模型切换 |
| `TokenStateMiddleware` | Token 使用追踪 |
| `AskUserMiddleware` | 向用户提问 |
| `LocalContextMiddleware` | Git 信息 + 目录树上下文 |
| `ShellAllowListMiddleware` | Shell 命令白名单 |

- 从 `~/.deepagents/<agent_id>/agents/` 和 `.deepagents/agents/` 加载自定义子代理
- 使用 `CompositeBackend` 路由大工具结果和对话历史到临时目录

---

## 核心设计原则

1. **Middleware 是扩展机制** — 不是工具函数。中间件在 LLM 调用前拦截；普通工具只是被动响应 LLM 调用。

2. **Profiles 分离关注点** — `ProviderProfile` 控制模型构造；`HarnessProfile` 控制运行时行为。两者正交且独立注册。

3. **受保护脚手架** — `FilesystemMiddleware` 和 `SubAgentMiddleware` 不可排除，保证核心功能始终可用。

4. **叠加式 Profile 合并** — provider 级默认 + model 级覆盖，排除集合取并集，中间件按类型合并。

5. **LangGraph 原生** — 输出是编译后的 LangGraph 图，所有 LangGraph 特性开箱即用。
