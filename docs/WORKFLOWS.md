# TacoAgent 工作流说明

本文记录当前 TacoAgent 的三种可执行工作流。三者共用工作区安全工具、运行时状态、事件追踪和入口意图路由，但执行拓扑和质量控制方式不同。

## 总览

| 工作流 | CLI 模式 | 核心结构 | 主要优势 | 主要代价 |
|---|---|---|---|---|
| Single-agent graph | `single-agent`（默认） | Planner → Actor 内层工具循环 → Verifier，失败后定向续接 | 有计划、验证和重试闭环，适合一般代码任务 | 模型调用较多，流程比纯 ReAct 慢 |
| Multi-agent graph | `multi-agent` | Supervisor → Search/Code specialists → Verifier | 研究与实现职责分离，适合需要资料与多阶段协作的任务 | 委派成本高，状态合并和提示词质量更敏感 |
| ReAct | `react` | 单个 ReAct 节点循环调用工具 | 结构简单、事件直接、启动成本低 | 没有独立 planner/verifier 闭环，容易耗尽循环而未落盘 |

所有模式先经过 `intent_router`。普通对话进入 `chat_responder` 后结束；需要操作工作区的输入进入所选任务工作流。评测程序可用 `force_workflow=True` 跳过模型意图判断。

## Single-agent graph

构建入口：`build_workflow()`；事件入口：`stream_workflow_events()`。

```text
START
  ↓
intent_router ──chat──→ chat_responder → END
  │ workflow
  ↓
single_agent_planner
  ↓
context_monitor ──需要压缩──→ context_compressor
  │                              │
  └──────────────→ actor ←───────┘
                    ↓
                 verifier
                 │      │
              失败      通过/达到上限
                 ↓      ↓
               actor   final → END
```

特点：

- Planner 生成或修订计划、TODO、验收标准和验证命令。
- Actor 根据计划执行显式的 turn 状态机：模型响应、工具批次、结果回灌、轮间准备，直到自然完成、失败、停滞或预算耗尽。
- Verifier 使用只读工具检查真实工作区，同时执行验证命令。
- 验证失败且未达到 `max_attempts` 时把结构化反馈作为 follow-up 送回 Actor，形成外层 reflection 闭环；初始 Planner 不重复运行。
- Planner 下游和压缩后路由经过 context monitor；上下文过大时由 compressor 压缩并持久化历史摘要。
- 默认 CLI 模式，适合实现、修复、测试、重构等需要明确验收的任务。

Actor 与图共同构成两层状态机：

```text
外层：plan → [actor → verifier] → follow-up actor / final
                    │
内层：turn_start → model → tool batch → tool results → turn_end
                    ↑                              │
                    └──── prepare / stop policy ───┘
```

内层通过事件回调报告生命周期，模型和工具失败被编码为结果。模型响应若因 token 长度截断，其中携带的工具参数一律不执行，而是返回错误要求下一轮完整重发。轮间策略负责调查额度、补丁后的收敛额度、最终收尾和停滞退出；外层只处理 Verifier 的定向续接，因此两种时机不会混在同一个循环中。

局限：

- Planner、Actor、Verifier 都可能调用模型，单次任务成本高于 ReAct。
- Verifier 的判断质量依赖验收标准和验证命令是否具体。
- 多轮失败可能重复读取或执行类似命令。

## Multi-agent graph

构建入口：`build_multi_agent_workflow()`；事件入口：`stream_multi_agent_workflow_events()`。

```text
START
  ↓
intent_router ──chat──→ chat_responder → END
  │ workflow
  ↓
multi_agent_supervisor
  ├── TodoWriteTool
  ├── CallSearchAgentTool → searchAgent
  └── CallCodeAgentTool   → codeAgent
  ↓
context_monitor ──需要压缩──→ context_compressor
  ↓                              ↓
verifier ←───────────────────────┘
  │      │
失败     通过/达到上限
  ↓      ↓
supervisor  final → END
```

特点：

- Supervisor 自身不直接编辑文件或搜索，而是先发布计划，再委派专家。
- `searchAgent` 只使用 WebSearchTool，返回研究摘要和来源。
- `codeAgent` 使用工作区工具和 TodoUpdateTool，负责实现与检查。
- 交接记录、研究笔记、来源和代码摘要写回共享状态，供 verifier 和下一轮 supervisor 使用。
- Verifier 失败后只应委派缺失修复，避免整项任务重做。
- SWE-bench runner 会设置 `disable_web_search=True`，防止基准答案泄漏。

适合：需要当前资料、来源链接、研究后实现，或任务天然可拆分为研究/编码阶段的工作。

局限：

- Supervisor 和专家之间可能出现信息损失或重复委派。
- 调用链最长，延迟和 token 成本通常最高。
- 需要严格管理共享状态、消息合并和 Todo 状态的一致性。

## ReAct workflow

构建入口：`build_react_workflow()`；事件入口：`stream_react_workflow_events()`。

```text
START
  ↓
intent_router ──chat──→ chat_responder → END
  │ workflow
  ↓
react（最多 max_loops 次：思考 → 工具 → 结果）
  ↓
END
```

特点：

- 单个模型绑定工作区工具，在一个消息历史中循环调用工具。
- 每轮输出 `ai_message`、`tool_call`、`tool_result`，最后输出 `final_answer`。
- 注入分层 memory 快照，但图中没有独立 context monitor/compressor 节点。
- 不经过 Planner、Verifier 或失败后重新规划，`passed=True` 代表节点正常结束，不代表外部验收通过。
- 适合短小、目标明确、工具路径简单的任务，也适合作为低开销基线。

局限：

- 调查阶段可能占满 `max_loops`，没有为编辑和最终总结预留轮次。
- 缺少独立 verifier，无法自动发现“只写验证脚本但未修改生产代码”等问题。
- benchmark 的 inline approval 若没有 handler，会拒绝风险命令并消耗循环。
- 对大型陌生仓库的复杂 issue，稳定性通常弱于带规划和反思的 graph 工作流。

## 共同基础设施

- `RuntimeState` 将文件和命令操作限制在指定 workspace。
- 三种模式均实时产生工具、节点和最终结果事件，并写入本地 trace。
- session workflow 负责多轮会话记录，并按 CLI 选择转发到三种工作流之一。
- graph 模式使用分层 memory、NOTEPAD 和 HISTORY_SUMMARY；ReAct 会读取分层 memory 快照，但不走图级压缩节点。
- SWE-bench smoke runner 为每个实例创建独立仓库，强制任务路由，排除 TacoAgent 运行时文件，并导出官方格式的 `model_patch`。

### 上下文压缩

Graph 工作流把完整历史存储和模型上下文视图分离：

- Monitor 优先使用最近一次模型返回的 token usage 作为权威锚点，只估算锚点后的消息；没有 usage 时才退回模型 tokenizer 或字符启发式估算。
- 压缩在 `context_token_limit - context_reserve_tokens` 处提前触发，默认保留约 `context_keep_recent_tokens` 的近期原文。
- Compressor 选择协议合法的切点，不会让 retained tail 以孤立 `ToolMessage` 开头；超大单轮通过 turn-prefix 摘要衔接近期后缀。
- 摘要采用目标、约束、进度、文件、验证、错误和下一步组成的结构化交接格式，并基于 `previous_summary` 增量更新。
- 模型视图变为 `[Context compaction] + retained tail`，原始被压缩消息追加保存到 `.tacoagent/memory/compactions.jsonl`，`HISTORY_SUMMARY.md` 只保存最新折叠视图。
- 摘要调用失败会编码成 `compression_failed` 事件并保留原消息，不让异常击穿主工作流。

TacoAgent 当前会话拓扑是线性的，尚无交互式会话树导航，因此没有伪造 branch API；append-only compaction ledger 为后续增加分支摘要与回溯保留了原始数据基础。

### 外部任务记忆

任务记忆采用“轻索引 + 详细记录”两级结构：完整结构化记录以 `memory_id` 为主键存入工作区内的 `.tacoagent/memory/external_memory.sqlite3`；layered memory 只向模型提供 ID、任务、状态和短提示。Agent 判断某条旧记忆确实相关时，通过只读 `memory_get(memory_id)` 工具加载详情。可选 Mem0/Qdrant 只负责语义召回相关 ID，SQLite 是详细信息的本地真源；JSONL 继续作为可读审计记录。

Layered memory 按节点裁剪：Planner 和 Actor 最多接收 4 条轻量外部索引；Verifier 不加载长期任务索引，只保留极短恢复信息；SearchAgent 不加载历史摘要、NOTEPAD 或外部索引；Context Monitor 使用最小视图；Context Compressor 只保留生成交接摘要所需的计划与验证状态。任务、TODO、研究内容等当前状态由各节点的显式输入传递，不再在 memory 中重复注入。默认只读取 SQLite 最近索引，不启动可能产生额外 embedding 请求的 Mem0 语义召回；需要时可设置 `memory_semantic_recall=True`。

## 选择建议

- 默认使用 `single-agent`：对大多数工作区任务提供最平衡的可靠性与成本。
- 任务需要网络研究、来源或明显的专业分工时使用 `multi-agent`。
- 任务很小、希望快速执行或需要建立简单基线时使用 `react`。
- 对 SWE-bench 一类大型仓库修复任务，优先评测 single-agent graph；ReAct 需要增加编辑截止策略和验证闭环后再作为主方案。
