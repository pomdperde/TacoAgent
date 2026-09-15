# TacoAgent 新 10 个 SWE-bench Verified 实例复测

## 结论

本轮使用最新 single-agent、`node-trimmed` 记忆模式和硬阶段状态机，执行了 10 个此前该批次未使用的新实例。Agent 生成 5 个非空生产代码补丁，补丁生成率 **50%（5/10）**。官方 SWE-bench 4.1.0 逐实例报告确认 **2 个 resolved**，因此端到端通过率为 **20%（2/10）**；按已生成补丁计为 **40%（2/5）**。

官方 harness 对 3 个补丁完成了可判定测试，其中 2 个 resolved、1 个 unresolved；另外 2 个因 Docker 镜像拉取或构建错误没有得到测试结论。不能把这 2 个基础设施错误记作补丁的功能失败，也不能把它们记作通过。

## 配置

- 数据集：`princeton-nlp/SWE-bench_Verified`
- 模型：`deepseek-v4-pro`
- 工作流：`single-agent`
- 记忆模式：`node-trimmed`
- planner / actor / verifier 预算：150 / 300 / 120 秒
- 单实例外层硬超时：600 秒
- Agent run：`new10-finalverify-20260906-v3`
- 官方 run：`new10v3official`

## 汇总指标

| 指标 | 结果 |
|---|---:|
| 实例数 | 10 |
| 非空补丁 | 5 |
| 补丁生成率 | 50% |
| 官方 resolved | 2 |
| 端到端 resolved 率 | 20% |
| 生成补丁后的 resolved 率 | 40% |
| 输入 token | 695,100 |
| 输出 token | 61,338 |
| 总 token | 756,438 |
| 平均 token / 实例 | 75,643.8 |
| 中位 token / 实例 | 67,071.5 |
| 原始 Agent 总墙钟 | 81,605.873 秒 |
| 原始平均墙钟 | 8,160.587 秒 |
| 中位墙钟 | 387.457 秒 |
| 未超过 600 秒的 7 次平均墙钟 | 232.772 秒 |
| 官方 harness 墙钟 | 约 1,737 秒 |

原始耗时不能直接代表正常性能：运行期间宿主/会话多次长时间挂起，3 个实例分别记录为 34,227.211、36,035.482 和 9,713.775 秒，明显违反配置的 600 秒上限。中位数与“600 秒以内均值”更接近有效执行成本。

## 逐实例结果

| 实例 | Agent 状态 | 补丁字符 | token | Agent 秒 | 官方结果 |
|---|---|---:|---:|---:|---|
| astropy__astropy-8707 | completed | 465 | 202,335 | 267.555 | resolved |
| astropy__astropy-8872 | no_patch | 0 | 118,158 | 353.989 | 无补丁 |
| django__django-10097 | completed | 658 | 133,773 | 420.925 | harness error：Docker registry EOF |
| django__django-10554 | timed_out | 0 | 64,060 | 34,227.211 | 无补丁 |
| django__django-10880 | timed_out | 0 | 0 | 36,035.482 | 无补丁；provider/宿主异常 |
| django__django-10914 | timed_out | 685 | 65,951 | 9,713.775 | resolved |
| django__django-10973 | completed | 2,438 | 68,192 | 148.148 | unresolved |
| django__django-10999 | failed | 0 | 0 | 5.646 | 仓库 Git TLS 握手失败 |
| django__django-11066 | completed | 827 | 103,969 | 427.600 | harness error：镜像构建/获取失败 |
| django__django-11087 | failed | 0 | 0 | 5.542 | 仓库 Git TLS 握手失败 |

本地 verifier 为 0/10，但官方确认其中 2 个补丁 resolved。这说明本地 verifier 的“环境不可用/命令超时”仍会产生假阴性，不能作为官方 resolved 的替代。

## 失败分析

### Agent 质量问题

`django__django-10973` 的补丁删除了 `import os`，随后又在 `subprocess.run(..., env={**os.environ, ...})` 中使用 `os`，形成直接的 NameError 风险；同时把 `subprocess.check_call()` 改为 `subprocess.run()` 会改变非零返回码的异常语义。官方 5 个 FAIL_TO_PASS 测试全部失败。这个案例说明 actor 在大段删除后没有执行最低成本的静态检查，也没有约束“只改必要行为”。

`astropy__astropy-8707` 虽然本地 verifier 判失败，但补丁准确修改 `Card.fromstring()` 的 bytes 输入处理，官方 FAIL_TO_PASS 和 PASS_TO_PASS 全部通过。这说明 actor 的首次编辑质量可以达到 resolved，问题在本地验证环境和过早阻止后续修正。

### 基础设施问题

- 两个实例在浅克隆指定 commit 时遇到 GitHub Schannel TLS 握手失败。
- 三个实例在宿主挂起后被 wall-clock 超时误杀，外层计时远超 600 秒才恢复处理。
- 官方校验的两个 Django 镜像因 Docker Hub EOF 或镜像构建失败未被评分。
- SWE-bench 4.1.0 在最终 `make_run_report` 阶段再次构造测试规格，并访问无关仓库的 `environment.yml`；TLS EOF 导致总报告未生成，但逐实例报告完整保留。

## 可实施的代码改进

1. **把超时从单一 wall clock 改为带心跳的执行 watchdog。** 子进程每 5 秒写单调时钟心跳；父进程只有在“活跃运行时间”超过预算或心跳持续丢失后终止。检测系统睡眠/恢复时，将暂停区间单独记为 `suspended_seconds`，不计入模型与 actor 有效耗时。
2. **为 workspace 准备增加本地 bare mirror 和有限重试。** 按 repo 建立只读镜像，优先从本地 commit 创建 worktree；网络 fetch 使用 3 次指数退避，并把 TLS/registry 错误标为 `infra_error`，不启动模型、不消耗实验样本的有效尝试次数。
3. **在首次编辑与最小验证之间加入确定性的静态门。** 对修改过的 Python 文件运行 `python -m compileall -q <files>`，并用 AST 收集新增未定义名称；这会立即抓到 `django-10973` 的 `os` 未导入问题，成本远低于再调用模型。
4. **增加 diff 风险约束。** 若补丁删除导入、替换异常语义 API（如 `check_call` → `run`）或删除超过新增行数的 2 倍，自动读取目标函数与相关测试并要求 actor 生成更小补丁；修正额度应在静态门失败时保留一次，而不是被此前无关编辑消耗。
5. **拆分 verifier 结果。** 输出 `code_failed`、`environment_failed`、`timeout`、`passed` 四态；仅 `code_failed` 消耗 actor 的一次修正机会。环境失败时保存补丁并允许官方 harness 继续评分，避免 resolved 补丁被本地假阴性淘汰。
6. **给官方 harness 的镜像准备阶段加独立超时与缓存。** 预拉取本批次 eval image，失败实例单独重试；容器创建、镜像 pull/build、测试执行分别设置 watchdog。汇总直接读取已落盘 `report.json`，避免 `make_run_report` 再次联网构造所有 test spec。
7. **降低 token 长尾。** `astropy-8707` 消耗 202,335 token，显著高于中位数。诊断阶段只注入问题、目标测试、最多两个相关符号窗口和轻量记忆 ID；工具输出超过阈值写入外部记忆库，后续仅按 ID 精确取回。连续两次读取未缩小候选文件时，强制进入最小编辑或结束。

## 本地证据

- 原始总报告：`evals/results/swebench-smoke/new10-finalverify-20260906-v3/evaluation_report.json`
- 逐实例 ledger：`evals/results/swebench-smoke/new10-finalverify-20260906-v3/results.jsonl`
- 官方输入补丁：`evals/results/swebench-smoke/new10-finalverify-20260906-v3/predictions.jsonl`
- 机器可读汇总：`evals/results/swebench-smoke/new10-finalverify-20260906-v3/experiment_summary.json`
- 官方逐实例报告：`.swe-official/logs/run_evaluation/new10v3official/tacoagent__single-agent__deepseek-v4-pro/<instance>/report.json`

