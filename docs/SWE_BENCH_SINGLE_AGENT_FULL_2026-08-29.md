# Full Single-agent SWE-bench Verified Smoke（无降级）

## 目标与原则

本轮重新执行 TacoAgent `single-agent` graph，明确禁止 deterministic planner、fallback plan 或跳过节点。Planner、Actor、Verifier 均保持真实工作流逻辑。

为防止当前兼容模型端点无限阻塞，每个实例由独立子进程运行，并设置 120 秒硬墙钟超时。超时不是计划降级：实例仍执行原始 LLM Planner；到达预算时记录 `timed_out` 并继续下一个实例。

## 配置

| 项目 | 配置 |
|---|---|
| 日期 | 2026-08-29 |
| 数据集 | `princeton-nlp/SWE-bench_Verified` test split |
| 实例 | 顺序前 5 例，与既有 ReAct/single-agent smoke 相同 |
| 模型 | `deepseek-v4-pro` |
| 工作流 | 完整 `single-agent` graph |
| 最大验证尝试 | 3 |
| Planner 降级 | 无 |
| 实例硬超时 | 120 秒 |
| Docker harness | 未运行；Docker daemon 不可用 |

Planner prompt 保留全部唯一信息，但去除了 top-level task 与 layered memory 中完全相同的 task 副本。这是输入去重，不替换计划、不删除任务信息，也不改变图路由。

## 结果

| 指标 | 结果 |
|---|---:|
| 请求/完成记录 | 5 / 5 |
| `timed_out` | 5 |
| 进入 Actor | 2 / 5 |
| 进入 Verifier | 0 / 5 |
| 产生补丁 | 0 / 5 |
| 事件日志工具调用 | 23 |
| 总墙钟耗时 | 600.656 秒 |
| 平均耗时 | 120.131 秒/例 |
| 官方 resolved rate | N/A |

| Instance ID | 最远阶段 | 耗时（秒） | 事件日志行数 | 工具调用 | 补丁 |
|---|---|---:|---:|---:|---:|
| `astropy__astropy-12907` | Planner | 120.042 | 5 | 0 | 0 |
| `astropy__astropy-13033` | Actor | 120.141 | 36 | 8 | 0 |
| `astropy__astropy-13236` | Planner | 120.153 | 5 | 0 | 0 |
| `astropy__astropy-13398` | Actor | 120.167 | 50 | 15 | 0 |
| `astropy__astropy-13453` | Planner | 120.153 | 5 | 0 | 0 |

三个实例在首次 LLM Planner 调用中没有于 120 秒内返回。两个实例完成 Planner 和 context monitor 后进入 Actor，主要进行 workspace listing、command run、regex search 和 file read，但在超时前没有调用文件编辑工具，也没有进入 Verifier。

本次 ledger 生成时，超时子进程被终止后无法回传内存中的事件计数，因此 `results.jsonl` 的早期版本显示 `event_count=0/tool_calls=0`。上表采用已持久化事件 JSONL 的真实计数。runner 已修复：后续超时会从事件日志恢复计数并尝试导出 partial patch。

## 结论边界

- 这是一轮无降级、带 120 秒工程预算的 smoke，不是完整 500 例官方排行榜评测。
- 结果不能写成官方 resolved rate 0%，因为官方 Docker harness 没有运行。
- 0 个补丁说明在当前时间预算和模型端点组合下没有形成候选解。
- 不能单独归因于 Actor：3/5 根本没有离开 Planner；另 2/5 留给 Actor 的有效时间少于完整 120 秒。
- 该结果首先衡量端到端可用性和延迟，而不是无限时间条件下的最高解题能力。

## LangSmith

结果已登记为独立数据集：

- 名称：`TacoAgent SWE-bench Verified single-agent full 2026-08-29 v1`
- Dataset ID：`8d3ccfbf-c6f9-4b0e-bf2c-ba7329c5127c`
- 样例数：5
- `officially_graded=False`

登记内容包括 issue、repo、base commit、状态、空补丁、耗时和本地 runner 指标。此次未开启实时 trace，完成后通过专用同步脚本上传。

## 与前次 planner-bypassed 结果的区别

前次使用固定计划后，5/5 都能进入 Actor/Verifier，但 5/5 无补丁。该数据只能评估 Actor/Verifier 链。

本次完全移除固定计划：3/5 Planner 超时，2/5 进入 Actor，5/5 总体超时且无补丁。它更真实地揭示完整 single-agent 在当前模型服务上的首要瓶颈是 Planner 延迟和端到端预算控制。

## 改进建议

### P0：修复 Planner 可用性，不使用降级计划

1. 将 Planner 改成严格 structured output API，而不是要求模型通过普通 tool call 发布唯一计划。
2. 为 Planner 单独设置阶段预算，例如 30–45 秒；超时应将实例标为 Planner timeout，而不是生成固定计划。
3. 保存请求体大小、首 token 延迟、provider request ID 和原始错误，区分服务排队、网络和 schema 处理。
4. 精简但不丢失信息：任务只出现一次，memory 只包含本轮规划需要的字段，大段历史通过引用或摘要提供。
5. 用相同真实 issue prompt 做 provider A/B 测试，确认问题是否特定于 `deepseek-v4-pro` 兼容端点。

### P1：分配阶段预算

1. 120 秒总预算应拆成 Planner、Actor、Verifier 子预算，防止 Planner 吞掉全部时间。
2. Actor 中段仍无 diff 时发出收敛提醒，但不替换其计划。
3. 保证 Verifier 至少获得固定时间窗口；否则状态应为 `actor_timeout`，而不是笼统 timed_out。
4. 超时退出时保留 partial diff、最后节点、最后工具、事件计数和失败命令。

### P2：提高 benchmark 可信度

1. 在 Docker 可用后使用官方 harness 对任何非空 patch 评分。
2. 使用跨仓库显式实例集，避免顺序前五例全部来自 Astropy。
3. 将 hard timeout 提升到官方评测更常见的时间预算后再做一轮成本允许的验证。
4. LangSmith 中分别记录 `planner_latency`、`actor_latency`、`verifier_latency` 和 `terminal_stage`。
5. 不再把 runner 的 `completed/no_patch` 与 verifier passed 或官方 resolved 混为同一状态。

## 产物

- `evals/results/swebench-smoke/single-agent-full-strict-5-20260829/summary.json`
- `evals/results/swebench-smoke/single-agent-full-strict-5-20260829/results.jsonl`
- `evals/results/swebench-smoke/single-agent-full-strict-5-20260829/predictions.jsonl`
- `evals/results/swebench-smoke/single-agent-full-strict-5-20260829/events/*.jsonl`
- `evals/results/swebench-smoke/single-agent-full-strict-5-20260829/workspaces/*`

## 最终判断

在不允许 Planner 降级的条件下，当前 single-agent 与 `deepseek-v4-pro` 兼容端点未能在 120 秒/例预算内产出候选补丁。最优先的改进方向是让真实 Planner 请求可观测、可限时且稳定返回，而不是用固定计划掩盖问题或简单增加 Actor 重试次数。

