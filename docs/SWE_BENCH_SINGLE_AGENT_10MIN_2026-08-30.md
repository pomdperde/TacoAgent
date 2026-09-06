# Single-agent SWE-bench 10 分钟预算实验（2026-08-30）

## 目标与改动

本实验把每个 SWE-bench Verified 实例的硬运行上限由 300 秒提高到 600 秒，并把节点预算参数化。正式配置为：

| 预算项 | 秒数 | 用途 |
|---|---:|---|
| Planner | 150 | 生成真实 LLM 计划；不使用预制计划或 benchmark fallback |
| Actor | 300 | 调查、编辑与最小相关测试 |
| Verifier | 120 | 检查工作区、验证命令与 production diff gate |
| 运行时余量 | 30 | 路由、context monitor、事件落盘、diff 导出与进程回收 |
| 每实例硬上限 | 600 | runner 进程级 wall-clock timeout |

新增 CLI 参数：

```text
--planner-time-budget 150
--actor-time-budget 300
--verifier-time-budget 120
--instance-timeout 600
```

正式运行前的 v1 校准暴露了并行 tool call 的消息顺序问题：编辑失败提示被插入到剩余 ToolMessage 之前，兼容 API 返回 HTTP 400。该运行被中止，不计入正式结果。修复后确保一条 AIMessage 中的所有 tool calls 都先收到 ToolMessage，再追加编辑重试 HumanMessage；完整测试通过后使用新 run-id 从头执行。

## 正式实验配置

| 项目 | 值 |
|---|---|
| Run ID | `single-agent-10min-v2-5-20260830` |
| 数据集 | `princeton-nlp/SWE-bench_Verified` |
| 实例 | 顺序前 5 个 |
| 工作流 | 完整 `single-agent` graph |
| 模型 | `deepseek-v4-pro` |
| 最大验证次数 | 3 |
| 运行时间 | 2,830.011 秒 |
| 官方 Docker harness | 未运行 |

## 汇总结果

| 指标 | 10 分钟结果 | 上一轮 5 分钟结果 |
|---|---:|---:|
| 实例 | 5 | 5 |
| 正常结束 | 1 | 0 |
| 超时 | 4 | 2（另 3 个受 API 余额影响） |
| 非空候选补丁 | 3（60%） | 0 |
| Verifier 通过 | 0 | 0 |
| 工具调用 | 214 | 26 |
| 事件 | 954 | 156 |
| 平均实例时间 | 560.889 秒 | 184.444 秒 |

10 分钟预算显著提高了补丁产出率，但尚未提高已验证通过率。3 个补丁都是生产代码 diff，并非仅测试或文档变更。

## 逐实例结果

| 实例 | 状态 | 时间（秒） | 工具 | 编辑尝试 | 补丁字符 | 修改文件 |
|---|---:|---:|---:|---:|---:|---|
| `astropy__astropy-12907` | timed_out | 600.392 | 6 | 0 | 0 | 无 |
| `astropy__astropy-13033` | timed_out | 600.533 | 63 | 2 | 862 | `astropy/timeseries/core.py` |
| `astropy__astropy-13236` | completed / verifier failed | 402.525 | 52 | 7 | 935 | `astropy/table/table.py` |
| `astropy__astropy-13398` | timed_out | 600.442 | 55 | 3 | 4,088 | `builtin_frames/__init__.py`、新增 `itrs_observed_transforms.py` |
| `astropy__astropy-13453` | timed_out | 600.552 | 38 | 6 | 0 | 编辑尝试未形成最终 diff |

命令执行结果中包含环境、复现和测试命令，不等同于官方评分：

| 实例 | command_run | 工具返回失败 |
|---|---:|---:|
| `12907` | 0 | 0 |
| `13033` | 28 | 12 |
| `13236` | 21 | 7 |
| `13398` | 12 | 7 |
| `13453` | 7 | 2 |

失败数量包含测试失败、环境缺失、超时或非零退出，不能直接解释为补丁错误。由于没有实例被 Verifier 判定通过，本次未运行官方 Docker harness，resolved rate 仍为未知而不是 0%。

## 结论

延长至 10 分钟解决了“完全没有补丁”的主要症状：补丁产出由 0/5 提升到 3/5。但 4/5 仍耗尽完整 600 秒，说明瓶颈已经从“无法进入编辑”转移到“测试、编辑修正和验证不能及时收敛”。

下一轮优先级：

1. 为 Planner、首次 production diff、最小测试和 Verifier 分别记录实际耗时，基于数据动态分配剩余预算。
2. production diff 出现后禁止重新进行宽泛调查，优先完成一个最小测试和补丁自检。
3. 为严格唯一文本编辑提供行范围或小片段读取反馈，降低重复 `NonUniqueTextError`。
4. Actor 阶段结束时保留足够的强制 Verifier 时间，避免有补丁但在硬超时前没有验证结果。
5. 在 Docker 可用时对 3 个候选补丁运行官方 harness，获得真实 resolved rate。

## 产物与上传

- 本地报告：`evals/results/swebench-smoke/single-agent-10min-v2-5-20260830/evaluation_report.json`
- 原始结果：同目录的 `results.jsonl`、`predictions.jsonl`、`events/` 和 `workspaces/`
- LangSmith dataset：`TacoAgent SWE-bench Verified single-agent 10min v2 2026-08-30`
- LangSmith dataset ID：`5d5af92c-a985-405b-af35-cd5e914ea32f`
- 上传实例：5

代码回归验证：`113 passed, 1 skipped, 11 subtests passed`，`git diff --check` 通过。
