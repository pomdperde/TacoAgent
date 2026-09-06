# TacoAgent 项目总结（2026-09-01）

## 项目定位

TacoAgent 是一个面向本地代码仓库的终端 Agent 框架。它以 Typer/Rich 提供命令行入口，以 LangGraph 组织任务状态和节点流转，通过 OpenAI 兼容模型完成规划、工具调用、代码修改与验证。所有文件和命令操作都受工作区边界约束，并写入 JSONL 审计记录。

## 核心结构

- `src/tacoagent/core/`：模型工厂、Agent/Session 运行时、检查点、审批与 trace。
- `src/tacoagent/tools/`：受控的文件读写、正则搜索和命令执行工具；路径由 `WorkspaceSandbox` 校验。
- `src/tacoagent/graph/`：意图入口、Planner、Actor、Verifier、上下文压缩和最终输出节点。
- `src/tacoagent/workflow/`：`single-agent`、`multi-agent`、`react` 和 session 工作流装配。
- `src/tacoagent/memory/`：分层任务记忆及可选的 mem0/Qdrant 后端。
- `evals/swebench/`：SWE-bench Verified 数据加载、隔离工作区、patch 收集、断点续跑、本地汇总和官方 Docker harness 适配。
- `evals/`：DeepEval、LangSmith 与工作流对比入口。
- `tests/`：工具安全、图节点、工作流、CLI、记忆和评测适配器测试。

## Single-agent 工作流

默认工作流依次经过意图路由、单 Agent Planner、Actor 和 Verifier。Planner 生成计划与验收条件；Actor 使用同一代码 Agent 在工作区内调查、编辑和测试；Verifier 检查生产 diff、验证命令及结果，并在失败时将结构化反馈送回下一轮。默认最多 3 次尝试，SWE-bench 运行器的默认阶段预算为 Planner 150 秒、Actor 300 秒、Verifier 120 秒，每实例名义硬上限 600 秒。

## 当前能力与局限

项目已经具备工作区隔离、工具审计、事件流、生产 diff gate、断点续跑和官方 SWE-bench 验证链路。2026-09-01 的回归基线为 `118 passed, 1 skipped, 11 subtests passed`。

当前主要局限是端到端超时不能可靠取消阻塞中的模型请求、partial clone 仍可能在 checkout 时访问远端失败、Agent 在复杂实例上容易耗尽预算而没有形成产品代码 diff，以及本地 Verifier 与官方容器环境仍存在差异。具体证据和改进优先级见 [本轮 SWE-bench 记录](SWE_BENCH_SINGLE_AGENT_RERUN_2026-09-01.md)。
