# SWE-bench 官方校验失败分析与修复（2026-09-05）

## 结论

首次官方运行 `taco10v2` 的 0 resolved 不可信。Windows 上的 SWE-bench
harness 使用 CRLF 写出 `eval.sh`，Linux 容器把 `\r` 解析进参数，造成
`activate\r`、`cd /testbed\r`、`pytest ...\r` 全部失败，隐藏测试补丁也无法应用。

加入 LF 兼容层后，以相同 Agent 补丁复评分（`taco10lf`）：

- 全部任务：10
- 非空补丁：8（80%）
- 官方 resolved：2（占全部任务 20%；占 7 个成功完成官方评分的非空补丁 28.6%）
- 官方 unresolved：5
- 基础设施错误：1（Docker 镜像拉取失败）
- 空补丁/Agent 超时：2

## 逐实例结果

| 实例 | 修复后官方结果 | 主要原因 |
|---|---:|---|
| astropy-12907 | infra error | 复评分时官方镜像拉取失败，不能判断补丁质量 |
| astropy-13033 | unresolved | `test_required_columns` 失败；实现改变了既有错误消息契约 |
| astropy-13398 | unresolved | 4 个 FAIL_TO_PASS 仍失败，坐标变换修复不完整 |
| astropy-13453 | unresolved | 目标测试失败且 7 个 PASS_TO_PASS 回归，修改范围/兼容性不足 |
| astropy-13579 | resolved | FAIL_TO_PASS 与 PASS_TO_PASS 全部通过 |
| astropy-13977 | unresolved | 8 个目标用例及 4 个既有用例失败，ufunc fallback 语义错误 |
| astropy-14182 | unresolved | `test_rst_with_header_rows` 仍失败，未满足 header rows 行为 |
| astropy-14309 | resolved | FAIL_TO_PASS 与 PASS_TO_PASS 全部通过 |

## 已实施修复

1. `evals/swebench/harness_compat.py` 在 Windows 上强制所有 `.sh` 使用 LF，
   并内置 Unix `resource` 导入兼容；官方包装器通过该入口启动 harness。
2. SWE-bench prediction 使用 `production_only=True` 导出，测试、fixture、snapshot
   和测试配置不会进入提交补丁，避免与官方隐藏 test patch 冲突。
3. benchmark 任务提示明确禁止修改测试侧文件。
4. planner 被要求优先使用 issue 中明确给出的测试路径、测试名和精确期望值，
   不再猜测相似文件。
5. actor payload 现在实际接收 planner 的 `verification_commands`；此前 planner
   虽生成命令但 actor 看不到，是选择错误测试文件的重要原因。
6. correction 阶段明确要求依据失败输出保留兼容性契约，只允许聚焦修改生产代码。

## 验证

- TacoAgent 全量测试：145 passed、1 skipped、11 subtests passed。
- 修复后的官方运行：`taco10lf`，2 resolved、5 unresolved、1 infra error、2 empty。
- 官方报告：`.swe-official/tacoagent__single-agent__deepseek-v4-pro.taco10lf.json`
- 原始 Agent 结果：
  `evals/results/swebench-smoke/latest-docker-verifier-10-20260905/evaluation_report.json`

## 后续优先级

1. 在 actor 首次编辑前强制建立“当前行为/期望行为/不得破坏行为”三项契约，减少
   `13033`、`13977` 这类语义漂移。
2. 对最小验证命令增加路径存在检查及修改模块到测试文件的映射；错误命令不应消耗
   唯一一次验证机会。
3. correction 后允许一次只读复验；当前“修正后不再验证”会提交未经验证的改动。
4. Docker 镜像预拉取增加有限重试、指数退避与独立墙钟超时，并将 infra error 与
   unresolved 永久分开统计。
5. 对 PASS_TO_PASS 回归设置硬门槛；目标测试通过但邻近回归时不得输出最终 patch。
