# Deep Agents ACP Integration

> `deepagents-acp` — 将 Deep Agent 接入 Agent Client Protocol (ACP) 的桥接层

## 概述

`deepagents-acp` (`libs/acp/`) 是一个轻量级 ACP 适配器包，允许在支持 ACP 的编辑器（如 [Zed](https://zed.dev/)）中运行 Python Deep Agent。它实现了 `acp.Agent` 接口，起到 Deep Agents SDK 与 ACP 客户端之间的双向桥接作用。

```
┌─────────────────────────────────────────────────────────┐
│                    ACP Client (Zed)                      │
│   initialize / new_session / prompt / cancel / config    │
└──────────────┬──────────────────────────────────────────┘
               │ ACP Protocol (stdio / WebSocket)
┌──────────────▼──────────────────────────────────────────┐
│              AgentServerACP (server.py, 993 行)          │
│                                                          │
│  ┌─────────────────────────────────────────────────┐    │
│  │  协议适配层                                        │    │
│  │  • Content Block 转换 (Text/Image/Audio/Resource) │    │
│  │  • Tool Call → ACP 通知 (start/update/completed)  │    │
│  │  • Plan (todo) → AgentPlanUpdate                  │    │
│  │  • HITL Interrupt → request_permission            │    │
│  │  • Session Mode / Model 切换                      │    │
│  └─────────────────────────────────────────────────┘    │
│                          │                               │
│  ┌───────────────────────▼──────────────────────────┐    │
│  │  Agent 生命周期管理                                 │    │
│  │  • Agent Factory (支持动态重建)                     │    │
│  │  • Checkpointer (MemorySaver, thread ID = session) │    │
│  │  • 取消机制 (_cancelled flag)                      │    │
│  └──────────────────────────────────────────────────┘    │
│                          │                               │
│  ┌───────────────────────▼──────────────────────────┐    │
│  │  安全层 (utils.py)                                 │    │
│  │  • Dangerous pattern 检测 ($(), ``, redirects…)   │    │
│  │  • Command type 提取 + Allowlist 自动批准          │    │
│  └──────────────────────────────────────────────────┘    │
└──────────────┬──────────────────────────────────────────┘
               │ LangGraph astream
┌──────────────▼──────────────────────────────────────────┐
│             CompiledStateGraph (Deep Agent)              │
│   create_deep_agent() + middleware + tools + backend     │
└─────────────────────────────────────────────────────────┘
```

---

## 核心组件

### 1. `AgentServerACP` (`server.py`)

继承 `acp.Agent`，实现完整的 ACP 协议接口。

```python
from deepagents_acp.server import AgentServerACP, AgentSessionContext

# 方式 1：直接传入编译好的 graph
agent = create_deep_agent(...)
server = AgentServerACP(agent)

# 方式 2：传入工厂函数（支持动态 model/mode 切换 + 按 session 重建）
def build_agent(context: AgentSessionContext):
    return create_deep_agent(
        model=context.model,
        backend=FilesystemBackend(root_dir=context.cwd, virtual_mode=True),
        interrupt_on=_get_interrupt_config(context.mode),
    )

server = AgentServerACP(agent=build_agent, modes=modes, models=models)
```

**关键属性**:

| 属性 | 类型 | 用途 |
|------|------|------|
| `_agent_factory` | `CompiledStateGraph \| Callable` | Agent 来源：静态 graph 或工厂函数 |
| `_session_cwds` | `dict[str, str]` | 每个 session 的工作目录 |
| `_session_modes` | `dict[str, str]` | 每个 session 的当前 mode |
| `_session_models` | `dict[str, str]` | 每个 session 的当前 model |
| `_session_plans` | `dict[str, list[dict]]` | 每个 session 的 todo plan 状态 |
| `_allowed_command_types` | `dict[str, set[tuple]]` | per-session 自动批准的 shell 命令类型 |
| `_cancelled` | `bool` | 取消标志 |

### 2. `AgentSessionContext` (`server.py` L83-89)

```python
@dataclass(frozen=True, slots=True)
class AgentSessionContext:
    cwd: str              # 工作目录（来自 ACP new_session）
    mode: str             # 当前 session mode（如 "accept_edits"）
    model: str | None     # 当前 model（如 "anthropic:claude-sonnet-4-6"）
```

工厂函数每次重建 agent 时接收此上下文，确保 agent 使用正确的 cwd、mode 和 model。

---

## 协议转换链路

### Content Block 转换 (`utils.py`)

ACP 的多模态 content block 需要转换为 LangChain 的内容格式：

| ACP Block | 转换函数 | 输出格式 |
|-----------|---------|---------|
| `TextContentBlock` | `convert_text_block_to_content_blocks()` | `[{"type": "text", "text": "..."}]` |
| `ImageContentBlock` | `convert_image_block_to_content_blocks()` | `[{"type": "image_url", "image_url": {"url": "data:..."}}]` |
| `AudioContentBlock` | `convert_audio_block_to_content_blocks()` | `NotImplementedError` |
| `ResourceContentBlock` | `convert_resource_block_to_content_blocks()` | `[{"type": "text", "text": "[Resource: ...]"}]` |
| `EmbeddedResourceContentBlock` | `convert_embedded_resource_block_to_content_blocks()` | `[{"type": "text", "text": "[Embedded ...]"}]` |

`ResourceContentBlock` 的路径会被截断到相对路径（去掉 root_dir 前缀），确保与 agent 的 cwd 一致。

### Tool Call 生命周期

```
Agent astream 产生 tool_call_chunks
    │
    ▼
_process_tool_call_chunks()         ← 按 index 累积 chunks，args 完整时 json.loads
    │
    ▼
_create_tool_call_start()           ← 根据 tool_name 映射到 ACP ToolKind
    │                                  ┌──────────────┬──────────┐
    │                                  │ read_file     │ "read"   │
    │                                  │ edit_file     │ "edit"   │
    │                                  │ write_file    │ "edit"   │
    │                                  │ ls/glob/grep  │ "search" │
    │                                  │ execute       │ "execute"│
    │                                  │ 其他          │ "other"  │
    │                                  └──────────────┴──────────┘
    ▼
conn.session_update(tool_call_start) ← ACP 客户端显示工具调用开始
    │
    ▼
Agent 执行工具 → ToolMessage 返回
    │
    ▼
conn.session_update(tool_call_completed) ← 含 formatted result
    │  • execute: 格式化 command + output + exit code
    │  • edit_file: 使用 start_edit_tool_call + tool_diff_content (diff 显示)
    │  • 其他: text_block(content)
```

**`edit_file` 特殊处理** (L523-545):
当 old_string 和 new_string 都存在时，创建 `start_edit_tool_call` 并使用 `tool_diff_content()` 生成 diff rich content，在 ACP 客户端中以内联 diff 形式展示。

### Plan (Todo) 同步

```
Agent 调用 write_todos
    │
    ▼
工具调用开始时 → _handle_todo_update(session_id, todos, log_plan=False)
    │               │
    │               ├─ 转换为 PlanEntry[] → AgentPlanUpdate → session_update
    │               └─ 状态验证 (pending|in_progress|completed)
    │
    ▼
工具执行中断时 → HITL 审批流程
    │               ├─ approve → 存储 plan 到 _session_plans
    │               ├─ reject  → _clear_plan + feedback
    │               ├─ 已有 plan 且未完成 → 自动批准更新
    │               └─ approve_always → 不支持（write_todos 仅 "always allow" 对其他工具）
    │
    ▼
updates 节点中 → 检测 tools.todos 更新 → _handle_todo_update (log_plan=False)
```

### HITL 中断处理 (`_handle_interrupts`, L781-962)

```
Agent 触发 interrupt (如 interrupt_on={"edit_file": True})
    │
    ▼
__interrupt__ 出现在 astream updates 中
    │
    ▼
_handle_interrupts(session_id, current_state)
    │
    ├─ 遍历 action_requests
    │
    ├─ write_todos 特殊逻辑:
    │   ├─ 已有未完成 plan → 自动批准 (auto-approve updates)
    │   └─ 新 plan / 已完成 → 请求审批
    │
    ├─ execute 自动批准逻辑:
    │   ├─ 含 dangerous patterns ($(), ``, redirects…) → 永不自动批准
    │   ├─ 所有 command types 已在 allowlist → 自动批准
    │   └─ 否则 → 请求审批
    │
    ├─ 其他已 allowlisted 工具 → 自动批准
    │
    └─ 默认 → request_permission(options)
        ├─ approve → {"type": "approve"}
        ├─ reject  → {"type": "reject"}
        ├─ approve_always → 将 command type 加入 _allowed_command_types + approve
        └─ cancel   → {"type": "reject"}
```

**权限选项** (ACP 客户端中展示):

| Option ID | 名称 | 效果 |
|-----------|------|------|
| `approve` | Approve | 仅本次批准 |
| `reject` | Reject | 拒绝本次 |
| `approve_always` | Always allow `<type>` commands | 将 command type 加入 allowlist，后续自动批准 |

### 流式响应 (`prompt`, L590-779)

```
prompt(content_blocks, session_id)
    │
    ├─ 1. 初始化 agent (首次或重建后)
    ├─ 2. 确保 checkpointer 存在
    ├─ 3. 转换 content blocks
    ├─ 4. 进入 while loop (处理 interrupt 循环)
    │      │
    │      └─ agent.astream(Command(...), stream_mode=["messages", "updates"])
    │           │
    │           ├─ stream_mode="messages":
    │           │   ├─ str chunk → _log_text (流式输出文本)
    │           │   ├─ tool_call_chunks → _process_tool_call_chunks
    │           │   ├─ ToolMessage → update_tool_call (completed)
    │           │   └─ content (str/list) → _log_text
    │           │
    │           └─ stream_mode="updates":
    │               ├─ "__interrupt__" → _handle_interrupts → user_decisions
    │               ├─ "tools".todos → _handle_todo_update
    │               └─ break (回到 while loop 顶部，resume with decisions)
    │
    └─ 5. 返回 PromptResponse(stop_reason="end_turn")
```

---

## 会话生命周期

```
Client 连接
    │
    ▼
initialize() → InitializeResponse(agent_capabilities)
    │  • prompt_capabilities.image = True
    │
    ▼
new_session(cwd, mcp_servers) → NewSessionResponse(session_id, modes?, config_options?)
    │  • 生成 session_id (uuid4)
    │  • 存储 cwd, mode, model
    │  • 构建 config_options (mode selector + model selector)
    │
    ▼
prompt(content_blocks, session_id) → PromptResponse(stop_reason)
    │  • 首次调用: _reset_agent(session_id) 初始化/重建 agent
    │  • astream 处理 → streaming text + tool calls + HITL
    │  • 可中断: cancel(session_id) → stop_reason="cancelled"
    │
    ├─ set_session_mode(mode_id, session_id)
    │    • 切换 mode → _reset_agent (重建 agent)
    │
    ├─ set_config_option("model", session_id, value)
    │    • 切换 model → _reset_agent (以新 model 重建 agent)
    │
    └─ cancel(session_id) → 设置 _cancelled = True
```

### Agent 重建 (`_reset_agent`, L574-588)

```python
def _reset_agent(self, session_id: str) -> None:
    if isinstance(self._agent_factory, CompiledStateGraph):
        self._agent = self._agent_factory   # 静态 graph 直接复用
    else:
        context = AgentSessionContext(
            cwd=self._cwd,
            mode=self._session_modes.get(session_id, "auto"),
            model=self._session_models.get(session_id),
        )
        self._agent = self._agent_factory(context)  # 工厂重建
```

重建发生在：
- 首次 prompt 调用
- mode 切换 (`set_session_mode`)
- model 切换 (`set_config_option("model", ...)`)

---

## 安全层 (`utils.py`)

### Dangerous Pattern 检测

```python
DANGEROUS_SHELL_PATTERNS = (
    "$(",     # 命令替换
    "`",      # 反引号命令替换
    "$'",     # ANSI-C quoting
    "\n",     # 换行注入
    "\r",     # 回车注入
    "<(",     # 进程替换（输入）
    ">(",     # 进程替换（输出）
    "<<<",    # Here-string
    "<<",     # Here-doc
    ">>",     # 追加重定向
    ">",      # 输出重定向
    "<",      # 输入重定向
    "${",     # 变量展开 with braces
)
```

含有这些模式的命令**永远不自动批准**，即使该命令类型已在 allowlist 中。

### Command Type 提取

`extract_command_types()` 解析 shell 命令链（支持 `&&`、`||`、`;`、`|`），对敏感命令提取签名：

| 命令 | 签名策略 | 示例 |
|------|---------|------|
| `python` / `python3` | `-m <module>` 含模块名；`-c` 仅含 flag | `python -m pytest` → `python -m pytest` |
| `node` | `-e` / `-p` 仅含 flag | `node -e 'code'` → `node -e` |
| `npm` / `yarn` / `pnpm` | 含 subcommand；`run <script>` 含脚本名 | `npm run build` → `npm run build` |
| `uv` | 含 subcommand；`run <tool>` 含工具名 | `uv run pytest` → `uv run pytest` |
| `npx` | 含包名 | `npx jest` → `npx jest` |
| 其他 | 仅 base command | `ls -la` → `ls` |

Allowlist 以 `(tool_name, command_type)` 元组形式存储。当命令的**所有** command types 都在 allowlist 中时，自动批准。

---

## Model 动态切换

`AgentServerACP` 支持在会话中途动态切换 LLM model，无需丢失对话历史。

```python
models = [
    {"value": "anthropic:claude-opus-4-7", "name": "Claude Opus 4.7"},
    {"value": "anthropic:claude-sonnet-4-6", "name": "Claude Sonnet 4.6"},
    {"value": "openai:gpt-5.5", "name": "GPT-5.5"},
]

def build_agent(context: AgentSessionContext):
    return create_deep_agent(
        model=context.model,  # context.model 来自 session 的当前选择
        checkpointer=checkpointer,
        backend=FilesystemBackend(root_dir=context.cwd, virtual_mode=True),
    )

server = AgentServerACP(agent=build_agent, models=models)
```

切换流程：
1. ACP 客户端调用 `set_config_option("model", session_id, "openai:gpt-5.5")`
2. `_session_models[session_id]` 更新为 `"openai:gpt-5.5"`
3. `_reset_agent(session_id)` 用新 model 重建 agent
4. 由于 `checkpointer` 共享且 `thread_id` 不变，对话历史保留

---

## Session Mode 系统

Mode 控制 agent 的权限请求行为。示例配置：

```python
modes = SessionModeState(
    current_mode_id="accept_edits",
    available_modes=[
        SessionMode(id="ask_before_edits", name="Ask before edits",
                    description="编辑、写入、shell 和计划前询问"),
        SessionMode(id="accept_edits", name="Accept edits",
                    description="自动接受编辑，但 shell 和计划前询问"),
        SessionMode(id="accept_everything", name="Accept everything",
                    description="自动接受所有操作"),
    ],
)
```

Mode 通过 `interrupt_on` 配置映射到不同的 HITL 规则：

```python
def _get_interrupt_config(mode_id: str) -> dict:
    return {
        "ask_before_edits": {
            "edit_file": {"allowed_decisions": ["approve", "reject"]},
            "write_file": {"allowed_decisions": ["approve", "reject"]},
            "write_todos": {"allowed_decisions": ["approve", "reject"]},
            "execute": {"allowed_decisions": ["approve", "reject"]},
        },
        "accept_edits": {
            "write_todos": {"allowed_decisions": ["approve", "reject"]},
            "execute": {"allowed_decisions": ["approve", "reject"]},
        },
        "accept_everything": {},
    }[mode_id]
```

---

## LocalContextMiddleware (`examples/local_context.py`)

示例中间件，通过运行 bash 脚本检测本地环境并注入 system prompt。

**检测内容**:
- 当前目录 (CWD) + Git 状态 (branch, uncommitted changes)
- 项目语言 (Python/JS/Rust/Go/Java) + monorepo 检测
- Package manager (uv/poetry/pipenv/pip/bun/npm/yarn/pnpm)
- Runtime 版本 (python3, node)
- 测试命令 (make test / pytest / npm test)
- 文件列表 + 目录树（过滤无关目录）

**架构亮点**:
- 并行检测：独立 sections 作为 subshell 后台任务并发运行
- Backend 无关：脚本通过 `backend.execute()` 执行，对 local shell 和 remote sandbox 均适用
- Post-summarization 刷新：对话摘要后重新检测（可能已生成新文件）
- Retry-loop 防护：检测失败后记录 cutoff 避免无限重试

---

## 测试覆盖 (`tests/`)

| 测试文件 | 覆盖内容 |
|---------|---------|
| `test_agent.py` (1063 行) | 文本流式输出、取消、多模态 content block、initialize/mode、tool call 生命周期、interrupt 处理、permission 请求、plan 更新 |
| `test_model_switching.py` (269 行) | 动态 model 切换、config option 构建、session 生命周期 |
| `test_command_allowlist.py` (318 行) | `extract_command_types` 各命令类型、dangerous pattern 检测、allowlist 批处理 |
| `test_utils.py` | Content block 转换函数 |
| `test_dangerous_patterns.py` | Danger pattern 检测边界情况 |
| `test_main.py` | 模块入口点 |

---

## 依赖

```toml
# pyproject.toml
[project]
name = "deepagents-acp"
version = "0.0.6"
dependencies = [
    "agent-client-protocol>=0.8.0",  # ACP 协议实现
    "deepagents",                     # Deep Agents SDK
    "python-dotenv>=1.2.2",          # .env 加载
]
```

- **`agent-client-protocol`**: 提供 `acp.Agent` 基类、所有 schema 类型、`run_agent` 入口
- **`deepagents`**: editable install，指向 `../deepagents`
- **兼容性**: 同时支持 ACP v0.8.x (SessionConfigOption wrapper) 和 v0.9.0+ (bare SessionConfigOptionSelect)

---

## 与 CLI 的关系

| 维度 | ACP | CLI |
|------|-----|-----|
| **协议** | Agent Client Protocol (标准化编辑器协议) | Textual TUI (自有终端 UI) |
| **客户端** | Zed、Toad 等 ACP 兼容编辑器 | 终端 |
| **UI** | 由客户端渲染（Agent 面板、内联 diff、plan 面板） | 自带 TUI 渲染 |
| **流式** | `conn.session_update()` 推送 text/tool/plan 更新 | Textual widget 更新 |
| **HITL** | `conn.request_permission()` → approve/reject/allow_always | `AskUserMiddleware` + 模式配置 |
| **Model 切换** | `set_config_option("model", ...)` via Session Config Options | `ConfigurableModelMiddleware` runtime 切换 |
| **复用** | 共享 `create_deep_agent()` 底层、checkpointer、backend | 独立中间件栈 (Model, TokenState, AskUser, LocalContext, ShellAllowList) |

两者是**互补关系**：CLI 提供完整的终端 coding agent 体验，ACP 提供标准化的编辑器集成接口。

---

## 快速启动

```bash
cd deepagents/libs/acp
uv sync --group examples
# 配置 .env (ANTHROPIC_API_KEY)
# Zed settings.json 中添加:
# "agent_servers": {"DeepAgents": {"command": "/path/to/run_demo_agent.sh", "type": "custom"}}
```

或使用 Toad（通用 ACP 客户端）:

```bash
uv tool install -U batrachian-toad --python 3.14
toad acp "uv run python examples/demo_agent.py" .
```
