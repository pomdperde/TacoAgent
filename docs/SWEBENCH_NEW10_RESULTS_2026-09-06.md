# TacoAgent SWE-bench Verified 新 10 项实验（2026-09-06）

## 实验配置

- 工作流：single-agent，node-trimmed memory
- 模型：deepseek-v4-pro
- 实例：SWE-bench Verified 缓存顺序第 11–20 项，与上一批不重叠
- 尝试次数：1
- 预算：planner 150 秒、actor 300 秒、verifier 120 秒、实例硬截止 600 秒
- 官方评测：修复 CRLF 后的 SWE-bench 4.1.0 Docker harness，单项 1800 秒

## 总结果

| 指标 | 结果 |
|---|---:|
| 实例数 | 10 |
| 非空补丁 | 9 |
| 补丁生成率 | 90% |
| 官方 resolved（4.1.0 原始） | 6 |
| 兼容修正后 resolved | 7 |
| 兼容修正后全部实例通过率 | 70% |
| 兼容修正后非空补丁通过率 | 77.8% |
| 兼容修正后 unresolved | 2 |
| 官方基础设施错误 | 0 |
| Agent completed / timed_out | 8 / 2 |
| 本地 verifier passed | 2 |

## Token 与耗时

| 指标 | 结果 |
|---|---:|
| 输入 token | 1,256,934 |
| 输出 token | 125,309 |
| 总 token | 1,382,243 |
| 平均 token/任务 | 138,224 |
| 中位 token/任务 | 135,551 |
| 输出/输入 token 比 | 9.97% |
| token 消耗速率 | 305.65 token/Agent 秒 |
| 每个 resolved 平均 token | 230,374 |
| 每百万 token resolved 数 | 4.34 |
| Agent 实例耗时总和 | 4,522.368 秒（75.37 分钟） |
| Agent 整批墙钟 | 4,587.134 秒（76.45 分钟） |
| 平均耗时/任务 | 452.237 秒 |
| 中位耗时/任务 | 471.622 秒 |
| 最大耗时 | 601.302 秒 |
| 官方 harness 墙钟 | 约 1,621 秒（27.02 分钟） |

## 逐实例

| 实例 | Agent 状态 | Patch 字符 | Token | Agent 秒 | 官方结果 |
|---|---|---:|---:|---:|---|
| astropy-14365 | completed | 537 | 142,853 | 369.588 | unresolved |
| astropy-14369 | timed_out | 0 | 54,068 | 600.700 | empty |
| astropy-14508 | completed | 1,688 | 188,267 | 579.445 | resolved |
| astropy-14539 | completed | 592 | 166,717 | 492.188 | resolved |
| astropy-14598 | timed_out | 569 | 109,521 | 601.302 | unresolved |
| astropy-14995 | completed | 774 | 112,601 | 298.556 | resolved |
| astropy-7166 | completed | 593 | 159,793 | 484.724 | resolved |
| astropy-7336 | completed | 853 | 70,528 | 384.706 | resolved |
| astropy-7606 | completed | 527 | 249,646 | 252.639 | resolved（兼容修正） |
| astropy-7671 | completed | 991 | 128,249 | 458.520 | resolved |

`astropy-7606` 的 pytest 输出为 242 passed；SWE-bench 4.1.0 解析器未匹配参数化
测试 ID `test_compose_roundtrip[]`，将其记为一个 PASS_TO_PASS failure。兼容层采用
保守族匹配：只有同一测试族存在成员且全部为 PASSED/XFAIL 时，才补齐空参数 ID；
任一成员 FAILED/ERROR 仍判失败。用原始日志重算后该实例为 resolved。

## 原始 unresolved 与兼容修正

- `astropy-14365`：`test_roundtrip[True]` 失败。补丁只让 `READ SERR/TERR`
  命令匹配不区分大小写，没有覆盖 roundtrip 中的数据行识别。
- `astropy-14598`：`test_long_string_value_with_quotes` 失败。长 FITS 字符串包含引号时，
  续行拆分/尾部内容仍不正确；Agent 在 600 秒处超时，未完成修正验证。
- `astropy-7606`：已由 4.1.0 空参数 ID 兼容规则修复为 resolved。

## 改进方向

1. 将 600 秒实例截止拆成“模型截止”和“强制清理截止”；本轮出现一个 verifier
   容器在父进程超时后继续运行数小时，已手动清理。
2. 本地 verifier passed 只有 2，但官方 resolved 有 6，说明本地环境 blocked/failed
   分类仍过于悲观。优先直接复用官方镜像与 eval script，而不是自行安装依赖。
3. 对 roundtrip、序列化、解析器类问题，minimal verification 应覆盖写入与读回完整
   路径，不能只测单个正则或辅助函数。
4. correction 后增加一次只读复验；`14598` 在硬超时前有 patch，但未经最终验证。
5. 对高 token、低耗时样本（`7606`：249,646 token/252.639 秒）增加每阶段上下文
   去重和工具输出截断，避免快速重复灌入大文件内容。
6. 升级或修补官方结果解析器对 `test_name[]` 的匹配，避免全通过日志被误判。

## 本地文件

- 原始结果：`evals/results/swebench-smoke/new10-lf-production-20260906/evaluation_report.json`
- Predictions：`evals/results/swebench-smoke/new10-lf-production-20260906/predictions.jsonl`
- 结构化汇总：`evals/results/swebench-smoke/new10-lf-production-20260906/experiment_summary.json`
- 官方结果：`.swe-official/tacoagent__single-agent__deepseek-v4-pro.new10lf20260906.json`
