# Single-agent 收敛改造与 SWE-bench 复测报告（2026-08-30）

## 本次改造

本轮保持完整 single-agent 工作流和真实 LLM Planner，没有使用预制计划、确定性答案或 benchmark fallback。实现了以下收敛约束：

- Planner、Actor、Verifier 设置独立的模型请求/阶段时间上限，SWE-bench runner 继续使用每实例 300 秒硬上限。
- Actor 在 6 次只读调查调用后收到强制编辑指令，并实时发布 `patch_status` 和 `edit_required` 事件。
- Actor 的命令执行时间受剩余阶段预算限制，优先保留编辑和最小验证时间。
- 编辑失败时发布 `edit_retry_required`，要求读取最小目标区域并立即重试；ReAct 上限由 10 轮增至 14 轮以容纳修正。
- Verifier 对 SWE-bench 启用 production diff gate：没有生产代码 diff 时不可通过。
- Verifier 失败后直接返回 Actor，避免重新运行完整 Planner；达到 `max_attempts` 后结束。

production diff gate 仅在评测 runner 传入 `require_production_patch=True` 时启用，因此不会错误阻止文档类普通任务。

## 验证

项目测试结果：

```text
113 passed, 1 skipped, 11 subtests passed
git diff --check: passed
```

## 正式复测配置

| 项目 | 值 |
|---|---|
| 数据集 | `princeton-nlp/SWE-bench_Verified` |
| 实例 | 顺序前 5 个 |
| 工作流 | `single-agent` |
| 模型 | `deepseek-v4-pro` |
| 每实例硬超时 | 300 秒 |
| 最大验证轮次 | 3 |
| 官方 Docker harness | 未运行（无非空补丁） |
| Run ID | `single-agent-convergence-v2-5-20260830` |

正式运行前曾用 30 秒 Planner 请求上限做校准，前三例均发生 `APITimeoutError`。该校准运行被中止，没有作为正式结果；正式运行将 Planner 请求上限调整为 120 秒，以适配当前兼容 API 的实际延迟。

## 运行结果

| 指标 | 结果 |
|---|---:|
| 实例数 | 5 |
| `timed_out` | 2 |
| `failed` | 3 |
| 非空补丁 | 0 |
| 工具调用 | 26 |
| 事件数 | 156 |
| 总耗时 | 922.221 秒 |
| 平均耗时 | 184.444 秒 |

逐实例结果：

| 实例 | 状态 | 时间（秒） | 工具调用 | 补丁字符 | 说明 |
|---|---:|---:|---:|---:|---|
| `astropy__astropy-12907` | timed_out | 309.336 | 0 | 0 | Planner 未在硬上限内返回 |
| `astropy__astropy-13033` | timed_out | 300.834 | 26 | 0 | 进入两轮 Actor；尝试编辑但唯一文本匹配失败，未在超时前重试成功 |
| `astropy__astropy-13236` | failed | 125.021 | 0 | 0 | 模型 API 返回 402 Insufficient Balance |
| `astropy__astropy-13398` | failed | 109.700 | 0 | 0 | 模型 API 返回 402 Insufficient Balance |
| `astropy__astropy-13453` | failed | 77.330 | 0 | 0 | 模型 API 返回 402 Insufficient Balance |

`astropy__astropy-13033` 证明强制收敛事件确实生效：共出现 2 次 `edit_required`，第二轮 Actor 最终调用了 `file_edit`。编辑因模型提供的 `old_text` 与文件不完全一致而返回 `NonUniqueTextError: ... not found`。这次失败直接促成了新增的编辑失败重试机制，但该机制是在正式运行后根据记录补充，尚未获得新的付费模型实验验证。

## 结论与后续建议

本轮不能形成有效的 SWE-bench resolved-rate 结论：两个可运行实例仍未产出补丁，后三个实例受外部余额耗尽影响。可确认的工程结果是 diff gate、失败直返 Actor 和强制编辑事件均已进入真实运行路径；强制编辑将一个实例推进到了实际 `file_edit`，但编辑工具的严格唯一匹配成为下一瓶颈。

建议在补充模型余额后使用相同 run 配置复测，重点观察：

1. `edit_retry_required` 是否能把首次文本匹配失败转化为成功 diff；
2. Planner 首包延迟是否仍超过 120 秒；
3. 每个实例首次 production diff 的时间；
4. 有非空补丁后再运行官方 Docker harness，报告真正的 resolved rate。

## 产物

- 本地统一报告：`evals/results/swebench-smoke/single-agent-convergence-v2-5-20260830/evaluation_report.json`
- 逐实例记录：同目录下 `results.jsonl`、`predictions.jsonl` 和 `events/`
- LangSmith dataset：`TacoAgent SWE-bench Verified single-agent convergence v2 2026-08-30`
- LangSmith dataset ID：`806b450a-fa90-436b-8c69-627445433c9b`
- 上传示例数：5
