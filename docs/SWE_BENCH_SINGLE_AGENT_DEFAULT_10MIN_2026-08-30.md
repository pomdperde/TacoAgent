# Single-agent 默认 10 分钟预算复现实验（2026-08-30）

## 变更

本轮将 600 秒从运行命令中的临时参数固化为 SWE-bench runner 和一键评测入口的默认值。默认节点预算为：

| 节点/阶段 | 默认预算 |
|---|---:|
| Planner | 150 秒 |
| Actor | 300 秒 |
| Verifier | 120 秒 |
| 图路由、记录和进程回收余量 | 约 30 秒 |
| 单实例硬上限 | 600 秒 |

正式运行命令没有传入任何 timeout 或节点预算参数，以验证默认配置：

```powershell
python -m evals.swebench.run_evaluation `
  --mode single-agent --limit 5 --max-attempts 3 `
  --run-id single-agent-default-10min-5-20260830
```

## 汇总结果

| 指标 | 结果 |
|---|---:|
| 实例 | 5 |
| 正常结束 | 2 |
| 超时 | 3 |
| 非空 diff | 4/5 |
| 修改目标项目生产文件 | 3/5 |
| Verifier 通过 | 0/5 |
| 工具调用 | 266 |
| 事件 | 1,186 |
| 总实例时间 | 2,849.965 秒 |
| 平均实例时间 | 569.993 秒 |
| 整次运行墙钟时间 | 2,883.734 秒 |

`patches_generated=4` 是 runner 按“非空 Git diff”统计的原始指标。其中 `astropy__astropy-12907` 只生成 `_fix_sep.py` 辅助脚本，没有修改目标项目文件，因此有意义的目标代码补丁是 3/5。该现象说明 production diff gate 还应排除根目录临时修复脚本。

## 逐实例结果

| 实例 | 状态 | 时间（秒） | 工具 | diff 字符 | 文件与结论 |
|---|---:|---:|---:|---:|---|
| `astropy__astropy-12907` | timed_out | 600.333 | 54 | 1,642 | 仅 `_fix_sep.py`，不是有效目标代码修改 |
| `astropy__astropy-13033` | completed / verifier failed | 521.187 | 62 | 3,653 | `_fix_core.py` 与 `astropy/timeseries/core.py`；包含目标代码修改 |
| `astropy__astropy-13236` | completed / verifier failed | 527.509 | 53 | 881 | `astropy/table/table.py`；包含目标代码修改 |
| `astropy__astropy-13398` | timed_out | 600.438 | 62 | 3,711 | 修改 `builtin_frames/__init__.py` 并新增 `itrs_observed_transforms.py` |
| `astropy__astropy-13453` | timed_out | 600.498 | 35 | 0 | 无最终 diff |

## 与上一轮 10 分钟实验对比

| 指标 | 默认配置复现 | 上一轮显式配置 |
|---|---:|---:|
| 非空 diff | 4/5 | 3/5 |
| 目标代码补丁 | 3/5 | 3/5 |
| 正常结束 | 2/5 | 1/5 |
| Verifier 通过 | 0/5 | 0/5 |
| 工具调用 | 266 | 214 |

两轮均稳定产生 3 个目标代码候选补丁，说明延长到 10 分钟后的改善具有一定重复性；但没有任何实例通过 Verifier，尚不能证明候选补丁正确。

## 结论和改进建议

1. 默认 600 秒配置已生效，且两轮 10 分钟实验均达到 3/5 目标代码补丁产出。
2. 600 秒仍不足以让大多数实例完成验证，下一步应动态保留最后 120 秒给 Verifier，而不是继续单纯增加总时间。
3. production diff gate 应排除 `_fix_*.py`、一次性迁移脚本等辅助产物，要求 diff 命中已有项目源码或明确允许的新模块。
4. Actor 应在首次生产 diff 后停止宽泛调查，只运行最小相关测试、检查 diff 并修正失败。
5. 候选补丁必须通过官方 SWE-bench Docker harness 后才能报告 resolved rate。本轮未运行官方 harness，因此 resolved rate 为未知。

## 本地与远程记录

- 本地统一报告：`evals/results/swebench-smoke/single-agent-default-10min-5-20260830/evaluation_report.json`
- 原始数据：同目录下 `results.jsonl`、`predictions.jsonl`、`events/`、`workspaces/`
- LangSmith dataset：`TacoAgent SWE-bench Verified single-agent default 10min 2026-08-30`
- LangSmith dataset ID：`144cefe9-c2b1-485f-9833-aeb8c07c84df`
- 上传实例：5

执行前完整回归测试：`113 passed, 1 skipped, 11 subtests passed`；`git diff --check` 通过。
