# Single-agent SWE-bench Verified Smoke 评测总结

## 评测目标

使用 TacoAgent 默认的 single-agent graph 对与 ReAct smoke 相同的 5 个 SWE-bench Verified 实例进行测试，观察 Actor、Verifier、失败重试、补丁生成、执行成本和 LangSmith 数据登记情况。

本次运行不是完整 single-agent 端到端能力评测：LLM Planner 在第一个大型输入上长期阻塞，且客户端 90 秒 timeout 未能中断兼容端点请求。为继续测试 Actor→Verifier→反思链，runner 启用了明确标记的 `benchmark_mode`，使用固定通用计划替代 LLM Planner。本文将其称为 **planner-bypassed single-agent smoke**。

## 环境与配置

| 项目 | 配置 |
|---|---|
| 执行日期 | 2026-08-28 |
| 总结日期 | 2026-08-29 |
| 数据集 | `princeton-nlp/SWE-bench_Verified` test split |
| 样本 | 与 ReAct smoke 相同的顺序前 5 例 |
| 工作流 | `single-agent` graph |
| 模型 | `deepseek-v4-pro` |
| 最大验证尝试 | 3 |
| Planner | deterministic benchmark fallback |
| Actor / Verifier | 真实模型调用 |
| GitHub | HTTPS 经本机代理，按 base commit 浅取 |
| Docker harness | 未运行，Docker daemon 不可用 |

固定计划要求 Actor：定位问题、修改生产代码并添加回归测试、运行测试并检查最终 diff。所有实例仍经过 context monitor、Actor、Verifier、失败路由和 final 节点。

## 汇总指标

| 指标 | 结果 |
|---|---:|
| 请求/完成实例 | 5 / 5 |
| 非空补丁 | 0 |
| 补丁生成率 | 0% |
| Verifier 通过 | 0 / 5 |
| 完成三次尝试 | 5 / 5 |
| 总耗时 | 841.343 秒 |
| 平均耗时 | 168.269 秒/例 |
| 工具调用总数 | 205 |
| 平均工具调用 | 41 次/例 |
| 事件总数 | 835 |
| 平均事件数 | 167/例 |
| 事件日志总大小 | 5,195,972 字节（约 4.96 MiB） |
| 官方 resolved rate | N/A（Docker 未运行） |

## 实例明细

| Instance ID | 状态 | 耗时（秒） | 工具调用 | 事件 | Verifier | 补丁 |
|---|---|---:|---:|---:|---|---:|
| `astropy__astropy-12907` | `no_patch` | 170.168 | 41 | 167 | failed | 0 |
| `astropy__astropy-13033` | `no_patch` | 205.507 | 34 | 153 | failed | 0 |
| `astropy__astropy-13236` | `no_patch` | 162.266 | 44 | 173 | failed | 0 |
| `astropy__astropy-13398` | `no_patch` | 175.266 | 52 | 189 | failed | 0 |
| `astropy__astropy-13453` | `no_patch` | 128.136 | 34 | 153 | failed | 0 |

全部实例的最终状态均为：

```text
Task did not pass verification after 3 attempt(s).
Verifier returned no valid assessment
```

这比 ReAct 的空 final answer 更清楚地表达了失败，但也表明 Verifier 的结构化 JSON 输出没有被可靠解析。

## 节点与工具行为

每例节点访问模式一致：

```text
intent_router ×1
single_agent_planner ×3
context_monitor ×3
actor ×3
verifier ×3
final ×1
```

Planner 每次回访都使用同一个 deterministic benchmark plan。205 次工具调用主要由以下工具构成：

- `command_run`：84 次
- `file_read`：68 次
- `regex_search`：32 次
- `list_workspace_files`：20 次
- `TodoUpdateTool`：1 次

五例分别出现 12、6、7、6、2 次失败工具结果，共 33 次。除最后一例调用一次 TodoUpdateTool 外，模型基本没有显式维护 TODO 状态；没有实例调用文件创建或编辑工具形成最终 diff。

## 与 ReAct smoke 对比

| 指标 | ReAct | Single-agent（planner bypass） |
|---|---:|---:|
| 补丁生成率 | 20%（1/5） | 0%（0/5） |
| 平均耗时 | 86.839 秒 | 168.269 秒 |
| 平均工具调用 | 14 | 41 |
| 平均事件数 | 45 | 167 |
| 明确失败终态 | 否，final answer 为空 | 是 |
| 独立 Verifier | 无 | 有，但本次输出无有效 assessment |

本次不能据此断言 ReAct 优于完整 single-agent，因为 single-agent 的 LLM Planner 被绕过。不过可以确认：当前 Actor/Verifier 反思循环消耗约 1.94 倍时间、2.93 倍工具调用，却没有提高补丁产出。

## 评测结论

1. 评测基础设施能够完成五个实例、保存完整状态事件并导出官方 predictions 格式。
2. single-agent 能在达到最大尝试后给出明确失败结论，终态控制优于当前 ReAct。
3. Actor 在大型陌生仓库中仍表现为“读取和运行命令很多、实际编辑为零”。
4. Verifier 连续返回不可解析或无效 assessment，使三轮反思没有形成有针对性的修复指令。
5. 三次循环让事件日志膨胀到约 5 MiB，但 context compressor 未解决执行策略重复问题。
6. 官方 resolved rate 必须保持 N/A；不能把 verifier failed 或 no patch 直接称作官方 0 分。

## LangSmith 登记

专用数据集已成功创建：

- 名称：`TacoAgent SWE-bench Verified smoke single-agent 2026-08-28 v1`
- Dataset ID：`edc90f0f-51ef-4ce5-8d21-79407d29e3ce`
- 样例数：5

每条样例包含 issue 输入、仓库、base commit、模型补丁、状态、耗时、工具调用和事件数量，元数据标记 `officially_graded=False`。

实时 LangSmith tracing 曾让首次 Planner 调用长期阻塞，因此正式运行关闭了实时 tracing，完成后使用 `register_langsmith.py` 批量登记结果。LangSmith 中保存的是数据集与运行指标，不是这次完整节点 trace。

## 改进建议

### P0：保证能产生可验收修改

1. 给 Actor 设置阶段预算：前 3 轮定位，第 4–7 轮必须编辑，第 8–9 轮测试，第 10 轮总结。
2. 到 Actor 中段仍无 Git diff 时，注入强制收敛指令；如果确实无法修改，则返回明确 blocker。
3. 在 verifier 前增加 deterministic gate：检查是否存在生产文件 diff；无 diff 时无需调用 LLM verifier，直接生成具体失败原因。
4. benchmark 使用 `approval_mode=auto` 或专用非交互审批 handler，避免命令因无人审批失败。

### P1：修复 Planner 和 Verifier 协议

1. Planner 改用模型原生 structured output，限制输入体积并设置进程级 deadline，而不仅是 HTTP timeout。
2. 减少 Planner 输入中的 task/memory 重复，避免把完整 issue 多次 JSON 嵌套。
3. Verifier 使用严格 Pydantic schema；JSON 解析失败时保留原始响应并只重试一次格式修复。
4. Verifier 必须输出具体的 `recommended_next_instruction`，Planner/Actor 下一轮只处理该缺口。
5. 区分 graph 执行成功、verifier 判断成功和官方 benchmark resolved 三种状态。

### P2：控制成本和上下文

1. 不要把完整节点 state 重复写入每条事件；trace 保存摘要和 artifact 引用。
2. 对 stdout、文件内容和重复搜索结果做结构化截断。
3. 若连续两轮没有新增 diff 或新测试结果，提前终止反思循环。
4. 下一批显式选择跨仓库实例，避免五例均来自 Astropy 的样本偏差。
5. Docker 可用后执行官方 harness，再补充 resolved rate、测试通过率和失败日志分类。

## 产物位置

- `evals/results/swebench-smoke/single-agent-5-20260828/summary.json`
- `evals/results/swebench-smoke/single-agent-5-20260828/results.jsonl`
- `evals/results/swebench-smoke/single-agent-5-20260828/predictions.jsonl`
- `evals/results/swebench-smoke/single-agent-5-20260828/events/*.jsonl`
- `evals/results/swebench-smoke/single-agent-5-20260828/workspaces/*`

## 最终判断

本次 planner-bypassed smoke 表明，当前 single-agent 的框架级失败控制存在价值，但 Actor 与 Verifier 尚未把额外成本转化为代码修改和有效验证。最优先的工程目标不是增加尝试次数，而是建立“必须形成 diff → 必须运行针对性测试 → verifier 必须给出可执行反馈”的确定性门控。

