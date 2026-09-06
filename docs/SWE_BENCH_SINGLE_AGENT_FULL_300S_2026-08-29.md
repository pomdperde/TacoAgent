# Full Single-agent SWE-bench Verified Smoke：300 秒预算

## 评测目标

在不使用 deterministic planner、fallback plan 或节点绕过的前提下，将完整 single-agent 工作流的每实例硬超时从 120 秒提高到 300 秒，观察额外时间能否改善 Planner 返回率、Actor/Verifier 覆盖率和补丁产出。

## 配置

| 项目 | 配置 |
|---|---|
| 执行日期 | 2026-08-29 |
| 总结日期 | 2026-08-30 |
| 数据集 | `princeton-nlp/SWE-bench_Verified` test split |
| 实例 | 顺序前 5 例，与之前 smoke 相同 |
| 模型 | `deepseek-v4-pro` |
| 工作流 | 完整 `single-agent` graph |
| 最大验证尝试 | 3 |
| Planner 降级 | 无 |
| 每实例硬超时 | 300 秒 |
| Docker harness | 未运行；Docker daemon 不可用 |

Planner、Actor 和 Verifier 均由真实模型执行。Planner prompt 仅去除 top-level task 与 memory 中完全相同的 task 副本，不删除任务信息、不替换模型计划，也不改变图路由。

## 汇总结果

| 指标 | 结果 |
|---|---:|
| 请求/完成记录 | 5 / 5 |
| `timed_out` | 5 / 5 |
| Planner 在预算内返回 | 4 / 5 |
| 进入 Actor | 4 / 5 |
| 进入 Verifier | 3 / 5 |
| 形成第二轮规划 | 2 / 5 |
| 非空补丁 | 0 / 5 |
| 总耗时 | 1,502.382 秒 |
| 平均耗时 | 300.476 秒/例 |
| 持久化事件 | 277 |
| 工具调用 | 69 |
| 官方 resolved rate | N/A |

## 实例明细

| Instance ID | 最远阶段 | Planner 访问 | Actor 访问 | Verifier 访问 | 事件 | 工具调用 | 补丁 |
|---|---|---:|---:|---:|---:|---:|---:|
| `astropy__astropy-12907` | Planner | 1 | 0 | 0 | 5 | 0 | 0 |
| `astropy__astropy-13033` | 第二轮 Actor | 3 | 2 | 2 | 107 | 25 | 0 |
| `astropy__astropy-13236` | Actor | 1 | 1 | 0 | 41 | 11 | 0 |
| `astropy__astropy-13398` | Verifier | 1 | 1 | 1 | 65 | 19 | 0 |
| `astropy__astropy-13453` | 第二轮 Planner | 2 | 1 | 1 | 59 | 14 | 0 |

五个实例耗时分别为 300.329、300.563、300.381、300.503 和 300.606 秒。超时分支已从事件日志恢复真实事件和工具计数，并在终止子进程后检查 partial diff；五例均没有可导出的补丁。

## 工具行为

进入 Actor 的四例共执行 69 次工具调用：

- `file_read`：25 次
- `command_run`：27 次
- `regex_search`：10 次
- `list_workspace_files`：5 次
- `TodoUpdateTool`：2 次

没有实例调用文件创建或编辑工具形成 Git diff。增加的 180 秒主要转化成更多读取、搜索、命令和反思节点访问，没有转化成生产代码修改。

## 与 120 秒版本对比

| 指标 | 120 秒 | 300 秒 | 变化 |
|---|---:|---:|---:|
| Planner 返回 | 2/5 | 4/5 | +2 |
| 进入 Actor | 2/5 | 4/5 | +2 |
| 进入 Verifier | 0/5 | 3/5 | +3 |
| 工具调用 | 23 | 69 | +46 |
| 非空补丁 | 0/5 | 0/5 | 无改善 |
| 总耗时 | 600.656 秒 | 1,502.382 秒 | +901.726 秒 |

延长预算明显提高了工作流覆盖率，证明 120 秒不足以观察完整 graph 行为。但补丁产出仍为零：额外 2.5 倍总时间带来 3 倍工具调用，却没有解决“调查多、编辑少”的核心问题。

## 结果解释

1. `astropy__astropy-12907` 在 5 分钟内仍未完成 Planner，属于严重的实例级 Planner 延迟。
2. 其余四例的 Planner 能返回，说明端点并非完全不兼容 TodoWriteTool，而是延迟高度不稳定。
3. 三例进入 Verifier，说明完整图路由可工作；两例根据验证结果重新规划。
4. 没有任何文件编辑调用，表明瓶颈已经从“是否进入 Actor”推进到“Actor 是否收敛并修改代码”。
5. 所有实例最终 timed out，说明三轮 graph 反思无法在 5 分钟预算内稳定收尾。
6. Docker harness 未运行，因此不能将本地 `0 patch` 写成官方 resolved rate 0%。

## LangSmith 登记

本轮结果已上传为独立数据集：

- 名称：`TacoAgent SWE-bench Verified single-agent full 300s 2026-08-29 v1`
- Dataset ID：`75c97ea1-3115-41b0-9315-a02e4e983ae4`
- 样例数：5
- `officially_graded=False`

数据集包含实例输入、仓库、base commit、超时状态、空 patch、耗时和 runner 指标。实时节点 trace 保存在本地事件 JSONL，本轮采用完成后批量上传数据集的方式。

## 改进建议

### P0：让 Actor 从调查切换到编辑

1. 为 Actor 增加阶段状态：`inspect → edit → test → summarize`，而不是让同一个开放式 ReAct 循环自行决定何时收敛。
2. Actor 完成约 40% 工具预算仍无 diff 时，发出强制收敛提示：“选择最可能根因并实施最小生产代码修改”。
3. 在进入 Verifier 前设置 deterministic diff gate；无生产代码 diff 时返回 `no_implementation`，避免用 LLM verifier 消耗预算。
4. 把文件编辑工具列为计划中实现 TODO 的必需动作；若无法编辑，必须显式标记 blocked 并说明证据。

### P1：控制 Planner 和反思延迟

1. 记录 Planner 首 token 延迟和完整响应时间，区分服务排队与长推理。
2. Planner 使用严格 structured output，而不是唯一 TodoWriteTool 调用，以减少兼容端点对工具 schema 的不稳定延迟。
3. 阶段预算应独立：例如 Planner 45 秒、Actor 180 秒、Verifier 45 秒、收尾 30 秒，而不是共享 300 秒后由进程统一终止。
4. Verifier 失败后只允许一次定向修复；若没有新增 diff，不启动下一轮 Planner。

### P2：提高事件与评测质量

1. 在结果中增加 `terminal_node`、各节点耗时和最后一次工具调用。
2. 超时事件明确区分 `planner_timeout`、`actor_timeout`、`verifier_timeout`。
3. 将失败命令、审批拒绝和测试结果单独统计。
4. 选择跨仓库实例集，避免五个连续 Astropy 样本造成偏差。
5. Docker 可用后，对任何非空 partial patch 运行官方 harness，并记录 resolved 和 test logs。

## 产物位置

- `evals/results/swebench-smoke/single-agent-full-300s-5-20260829/summary.json`
- `evals/results/swebench-smoke/single-agent-full-300s-5-20260829/results.jsonl`
- `evals/results/swebench-smoke/single-agent-full-300s-5-20260829/predictions.jsonl`
- `evals/results/swebench-smoke/single-agent-full-300s-5-20260829/events/*.jsonl`
- `evals/results/swebench-smoke/single-agent-full-300s-5-20260829/workspaces/*`

## 最终结论

将超时延长到 5 分钟显著改善了 Planner、Actor 和 Verifier 的覆盖率，但没有改善补丁生成率。当前优先级应从“继续增加总超时”转向“阶段预算、强制编辑收敛、diff gate 和 verifier 定向反馈”。在这些机制完成前，继续把单例预算从 5 分钟提高到更长时间，预计主要增加成本和重复调查，而非稳定提高解题率。

