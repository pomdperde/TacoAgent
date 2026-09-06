# 最新 TacoAgent SWE-bench Docker 评测（2026-09-05）

## 配置

- 数据集：`princeton-nlp/SWE-bench_Verified` 固定前 5 个实例
- 工作流：`single-agent`，节点裁剪记忆，最新硬阶段 actor 与硬超时 planner
- 模型：`deepseek-v4-pro`
- 每实例：600 秒硬上限；planner/actor/verifier 为 150/300/120 秒
- 尝试次数：1
- 官方评分：SWE-bench 4.1.0，Docker，单 worker，测试上限 1800 秒
- 生成 run：`latest-planner-deadline-docker-5-20260905`
- 官方评分 run：`taco5v2`

## 结果

| instance | 生成 patch | 字符数 | agent 秒 | planner 降级 | Docker 结果 |
|---|---:|---:|---:|---:|---|
| `astropy__astropy-12907` | 是 | 1,748 | 356.812 | 是 | patch apply 失败（重复测试文件、CRLF hunk 冲突） |
| `astropy__astropy-13033` | 是 | 1,743 | 231.999 | 否 | unresolved；FAIL_TO_PASS 0/1 |
| `astropy__astropy-13236` | 是 | 769 | 244.443 | 否 | unresolved；FAIL_TO_PASS 0/2 |
| `astropy__astropy-13398` | 是 | 3,704 | 329.910 | 是 | harness error；实例镜像无法获取 |
| `astropy__astropy-13453` | 是 | 627 | 403.610 | 否 | unresolved；FAIL_TO_PASS 0/1 |

汇总：

- 补丁生成率：`5/5 = 100%`
- 官方 resolved 数：`0/5`
- 按全体样本计算的通过率：`0%`
- 成功进入完整 Docker 测试的样本：`3/5 = 60%`
- 已完整评分样本的通过率：`0/3 = 0%`
- patch apply 失败：`1/5 = 20%`
- harness 基础设施错误：`1/5 = 20%`
- agent 总耗时：1,566.774 秒；平均 313.355 秒/实例
- provider token：599,175（输入 531,809，输出 67,366）
- 本地 verifier：0/5；主要原因是 Windows 检出目录未构建 Astropy 扩展及验证证据没有完整传递，不作为官方通过率。

## 评分环境说明

本机原先安装的 SWE-bench 5.0.2 要求含 `image`、`eval_script`、`log_parser` 的新版数据 schema，和经典 Verified 数据不兼容。评分时固定为 SWE-bench 4.1.0，并为其 Windows 包初始化增加仅位于评分输出目录的 `resource` import shim。评分工作目录缩短为 `.swe-official`，规避 Windows 路径长度限制。

所有实例执行结束后，harness 在生成跨数据集总报告时访问无关 Pylint requirements URL 遭遇 SSL EOF；上述结果直接来自 3 个实例的官方 `report.json` 和 2 个实例的 `run_instance.log`，不依赖失败的跨数据集汇总步骤。

## 产物

- 生成汇总：`evals/results/swebench-smoke/latest-planner-deadline-docker-5-20260905/evaluation_report.json`
- 预测：`evals/results/swebench-smoke/latest-planner-deadline-docker-5-20260905/predictions.jsonl`
- agent 逐实例事件：`evals/results/swebench-smoke/latest-planner-deadline-docker-5-20260905/events/`
- Docker 逐实例报告与日志：`.swe-official/logs/run_evaluation/taco5v2/`
