# Single-agent SWE-bench Verified 10 实例实验（2026-08-30）

## 结论

TacoAgent 的 single-agent 工作流完成了 10 个 SWE-bench Verified 实例的补丁生成，并在官方 SWE-bench 5.0.2 Docker harness 中验证：

| 指标 | 结果 |
|---|---:|
| 提交实例 | 10 |
| 非空补丁 / 空补丁 | 5 / 5 |
| Resolved / Unresolved | 1 / 4 |
| 基础设施错误 | 0 |
| 全部实例 resolved rate | 10%（1/10） |
| 非空补丁 resolved rate | 20%（1/5） |

唯一通过的实例是 `astropy__astropy-14309`。它的 1 个 FAIL_TO_PASS 测试和 141 个 PASS_TO_PASS 测试全部通过。

## 逐实例官方结果

| 实例 | 补丁 | 官方结果 | FAIL_TO_PASS | PASS_TO_PASS | 主要问题 |
|---|---:|---:|---:|---:|---|
| `astropy__astropy-12907` | 空 | 未执行 | — | — | Agent 超时前未形成补丁 |
| `astropy__astropy-13033` | 1,575 字符 | unresolved | 0/1 | 20/20 | `test_required_columns` 仍失败 |
| `astropy__astropy-13236` | 空 | 未执行 | — | — | 三轮验证后仍无生产 diff |
| `astropy__astropy-13398` | 空 | 未执行 | — | — | 生成阶段 GitHub RPC/clone 失败 |
| `astropy__astropy-13453` | 空 | 未执行 | — | — | 生成阶段 GitHub TLS 失败 |
| `astropy__astropy-13579` | 空 | 未执行 | — | — | Agent 超时前未形成补丁 |
| `astropy__astropy-13977` | 1,468 字符 | unresolved | 12/20 | 318/322 | 仍有 8 个目标测试及 4 个回归测试失败 |
| `astropy__astropy-14096` | 1,404 字符 | unresolved | 0/1 | 426/426 | 仅生成 `_patch_skycoord.py`，没有修复产品代码 |
| `astropy__astropy-14182` | 520 字符 | unresolved | 0/1 | 9/9 | `test_rst_with_header_rows` 仍失败 |
| `astropy__astropy-14309` | 1,178 字符 | **resolved** | 1/1 | 141/141 | FITS connector 修复及测试全部通过 |

所有 5 个非空补丁均成功应用。最终官方运行没有容器未停止、评测错误或基础设施失败；harness 保留了 5 个实例镜像以便复查。

## 生成阶段

```powershell
python -m evals.swebench.run_evaluation `
  --mode single-agent --limit 10 --max-attempts 3 `
  --run-id single-agent-verifier-v2-10-20260830
```

每个实例硬上限为 600 秒，Planner、Actor、Verifier 默认预算为 150、300、120 秒。共记录 295 次工具调用和 1,411 个事件；实例累计耗时 4,560.956 秒，墙钟耗时 4,621.924 秒。生成状态为 4 个 `completed`、1 个 `no_patch`、3 个 `timed_out`、2 个 clone 基础设施失败。

## 官方 Docker 验证

```powershell
python -m evals.swebench.official_windows `
  --dataset_name SWE-bench/SWE-bench_Verified `
  --predictions_path evals/results/swebench-smoke/single-agent-verifier-v2-10-20260830/predictions.jsonl `
  --max_workers 1 --timeout 1800 --run_id o10lf `
  --report_dir evals/results/swebench-smoke/single-agent-verifier-v2-10-20260830/official/lf
```

Windows 兼容处理：

1. `princeton-nlp/SWE-bench_Verified` 缺少新版 harness 所需的 `image` 等字段，因此使用 `SWE-bench/SWE-bench_Verified`。
2. Docker build context 在深目录中会触发 Windows 路径长度限制，验证时使用短路径工作目录。
3. Windows 默认 CRLF 会令容器内 shell 命令携带 `\r`。新增 `official_windows.py`，只对 harness 生成的 `.sh`、`.diff`、`.patch` 文件强制使用 LF。

## 分析与改进

本地 Verifier 将所有候选判为未通过，总体较谨慎，但无法代替目标项目的标准环境。官方结果证明 `14309` 实际已满足全部测试，本地缺少可构建 Astropy 的依赖和扩展，造成一个 false negative；同时也确认其余四个候选补丁确实不完整。

主要瓶颈及建议：

1. 补丁覆盖率仅 50%。增加仓库 bare mirror、clone 重试和断点缓存，避免网络错误直接淘汰实例。
2. 将官方 Docker harness 纳入每个非空补丁的最终 Verifier；失败后把测试名和精简 traceback 回送 Actor，允许一次有边界的修复。
3. production diff gate 排除 `_patch_*`、`_fix_*`、`debug_*`、`tmp_*` 等一次性脚本，并要求至少修改一个项目跟踪文件。
4. 补丁出现后进入收敛模式：限制宽泛搜索，只读取失败附近代码、做小范围编辑并运行目标测试。
5. 单次 LLM 调用使用可取消的执行单元，确保 Actor 不越过为 Verifier 保留的时间。
6. 分别报告 `resolved`、`agent_failed`、`clone_failed`、`timed_out`、`verification_blocked`，避免混淆模型与环境问题。

## 产物

- 生成记录：`evals/results/swebench-smoke/single-agent-verifier-v2-10-20260830/evaluation_report.json`
- 官方精简记录：`evals/results/swebench-smoke/single-agent-verifier-v2-10-20260830/official/official_evaluation_report.json`
- 官方原始报告：`evals/results/swebench-smoke/single-agent-verifier-v2-10-20260830/official/lf/tacoagent__single-agent__deepseek-v4-pro.o10lf.json`
- 逐实例日志：`logs/run_evaluation/o10lf/tacoagent__single-agent__deepseek-v4-pro/`
- LangSmith dataset：`TacoAgent SWE-bench Verified single-agent verifier-v2 10 cases 2026-08-30`
- LangSmith dataset ID：`1e7ce79e-750f-4250-a032-390283ebde55`

生成前回归测试为 `116 passed, 1 skipped, 11 subtests passed`，`git diff --check` 通过。
