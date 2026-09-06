# Single-agent SWE-bench Verified 复跑（2026-09-01）

## 结论

本轮使用 TacoAgent `single-agent` 工作流重新执行固定的 10 个 SWE-bench Verified smoke 实例。10 个实例均形成运行记录，但仅生成 1 个非空补丁；官方 SWE-bench 5.0.2 Docker harness 判定该补丁 unresolved。

| 指标 | 结果 |
|---|---:|
| 请求 / 记录实例 | 10 / 10 |
| 非空补丁 / 空补丁 | 1 / 9 |
| timed_out / no_patch / failed | 6 / 2 / 2 |
| Resolved / Unresolved（官方执行） | 0 / 1 |
| 全集 resolved rate | 0%（0/10） |
| 非空补丁 resolved rate | 0%（0/1） |
| Docker 基础设施错误 | 0 |
| 工具调用 / 事件 | 219 / 1,134 |
| 实例累计耗时 / 墙钟耗时 | 85,068.219 秒 / 85,121.510 秒 |

生成命令：

```powershell
python -m evals.swebench.run_evaluation `
  --mode single-agent --limit 10 --max-attempts 3 `
  --run-id single-agent-rerun-10-20260831 --no-langsmith
```

配置保持默认的每实例 600 秒硬上限，以及 Planner/Actor/Verifier 的 150/300/120 秒预算。LangSmith 上传关闭，避免遥测连接影响本地实验记录。

官方验证命令：

```powershell
python -m evals.swebench.official_windows `
  --dataset_name SWE-bench/SWE-bench_Verified `
  --predictions_path evals/results/swebench-smoke/single-agent-rerun-10-20260831/predictions.jsonl `
  --max_workers 1 --timeout 1800 --run_id rerun10 `
  --report_dir evals/results/swebench-smoke/single-agent-rerun-10-20260831/official
```

## 逐实例结果

| 实例 | 状态 | 耗时（秒） | 工具调用 | 补丁字符 | 官方结果 / 主要原因 |
|---|---:|---:|---:|---:|---|
| `astropy__astropy-12907` | timed_out | 600.435 | 13 | 0 | 未执行；预算内未形成产品 diff |
| `astropy__astropy-13033` | timed_out | 600.545 | 37 | 0 | 未执行；预算内未形成产品 diff |
| `astropy__astropy-13236` | no_patch | 457.937 | 50 | 0 | 三轮验证后仍无有效实现改动 |
| `astropy__astropy-13398` | timed_out | 600.690 | 21 | 0 | 未执行；预算内未形成产品 diff |
| `astropy__astropy-13453` | timed_out | 601.132 | 39 | 0 | 未执行；预算内未形成产品 diff |
| `astropy__astropy-13579` | timed_out | 600.884 | 0 | 0 | 模型调用阶段阻塞 |
| `astropy__astropy-13977` | failed | 37,873.524 | 10 | 0 | `APITimeoutError`；实例硬超时未能取消请求 |
| `astropy__astropy-14096` | timed_out | 600.669 | 35 | 1,172 | **unresolved**；唯一非空补丁 |
| `astropy__astropy-14182` | no_patch | 28,447.452 | 14 | 0 | 三轮后无补丁；阶段调用远超名义预算 |
| `astropy__astropy-14309` | failed | 14,684.951 | 0 | 0 | partial clone checkout 时远端 RPC/early EOF |

官方 harness 对 `14096` 的 1 个 FAIL_TO_PASS 测试仍为 0/1，通过了 426/426 个 PASS_TO_PASS 测试。9 个空补丁不执行官方测试，不能记作 harness unresolved。

## 与上一轮对比

2026-08-30 verifier-v2 同一组 10 例生成 5 个非空补丁并 resolved 1 例（10%）。本轮仅生成 1 个非空补丁且 resolved 0 例。由于本轮出现两个超长模型阻塞和一个 checkout 网络失败，这次下降不能只归因于推理质量；它同时反映运行时取消语义和仓库缓存不稳定。可直接比较的补丁覆盖率从 50% 降至 10%。

## 改进记录

### 本轮已确认有效

1. 模型余额已恢复，前六例能进入工作流；不再是上一轮 general-v3 的 HTTP 402 全局阻塞。
2. 前五例均成功创建工作区，说明缓存完整性检查避免了立即 checkout 失败。
3. 超时实例工作区复查显示仅有 `.tacoagent` 日志，没有被收集器遗漏的产品 diff。
4. Windows 官方 harness 正常完成 1 个非空补丁验证，无容器或镜像构建基础设施错误。
5. 项目回归测试通过：`118 passed, 1 skipped, 11 subtests passed`。

### P0：端到端超时与取消

名义 600 秒的实例分别运行了 37,873.524 秒和 28,447.452 秒，证明线程级超时无法中断阻塞的模型 SDK 请求。应把每个实例放入独立子进程，在 deadline 到达时终止进程树；同时为 ChatOpenAI 显式设置更短的 connect/read timeout 和有限重试。结果记录应同时保存 `deadline_seconds`、`cancel_requested_at` 和 `process_terminated_at`。

### P0：仓库缓存改为完整对象存储

`14309` 的 partial clone 在 checkout 目标提交时仍需访问 promisor remote，最终 RPC early EOF。应使用完整 bare mirror，预取所选实例的 commit/tree/blob 并以 `git fsck` 或离线 checkout 作为缓存验收；实例工作区创建阶段禁止隐式联网，网络失败才可明确归类为 `clone_failed`。

### P1：优先形成最小产品 diff

6 个名义超时实例中只有 1 个留下补丁。Planner 应在早期锁定疑似文件和一个目标测试；Actor 在预算前半段必须完成首个最小编辑，后半段只做定向测试和修正。达到软截止时间时停止宽泛搜索，并在有产品 diff 时立即快照 patch。

### P1：可恢复的阶段检查点

每次成功编辑后保存过滤后的 patch 和阶段状态。即使后续模型请求或 Verifier 阻塞，也能输出 `partial_patch`，同时保留 `timed_out` 状态，避免将“有候选但未完成验证”和“完全无修改”混为一类。

### P1：指标口径

将状态拆分为 `resolved`、`unresolved`、`no_patch`、`agent_timeout`、`model_timeout`、`clone_failed` 和 `verification_blocked`。墙钟耗时与 Agent 活跃耗时分开报告；官方 resolved rate 继续以全部请求实例为分母，并单独报告非空补丁 resolved rate。

## 产物

- 综合报告：`evals/results/swebench-smoke/single-agent-rerun-10-20260831/evaluation_report.json`
- 官方预测：`evals/results/swebench-smoke/single-agent-rerun-10-20260831/predictions.jsonl`
- 实例事件：`evals/results/swebench-smoke/single-agent-rerun-10-20260831/events/`
- 官方报告：`evals/results/swebench-smoke/single-agent-rerun-10-20260831/official/tacoagent__single-agent__deepseek-v4-pro.rerun10.json`
- 官方逐实例日志：`logs/run_evaluation/rerun10/tacoagent__single-agent__deepseek-v4-pro/astropy__astropy-14096/`
