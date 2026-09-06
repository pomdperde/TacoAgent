# ReAct SWE-bench Verified Smoke 评测总结

## 评测目标

使用 TacoAgent 的 ReAct 工作流处理 5 个 SWE-bench Verified 实例，观察仓库准备、工具轨迹、补丁生成能力和运行稳定性。本次是 smoke evaluation，不等同于完整 500 实例排行榜评测。

## 环境与配置

| 项目 | 配置 |
|---|---|
| 日期 | 2026-08-28 |
| 数据集 | `princeton-nlp/SWE-bench_Verified` test split |
| 样本策略 | 数据集顺序中的前 5 个实例 |
| 工作流 | `react` |
| 实际模型 | `deepseek-v4-pro` |
| 最大 ReAct 循环 | 10 |
| 工作区 | 每个实例独立浅取对应 `base_commit` |
| 网络 | Git HTTPS 通过本机代理 `127.0.0.1:17897` |
| Web 搜索 | ReAct 工具集中未暴露 WebSearchTool |
| Docker harness | 未运行；Docker daemon 不可用 |

runner 在执行时读取模型名称早于 `.env` 加载，因此产物中的 `model_name` 显示为 `unknown-model`。实际模型调用使用 `.env` 配置的 `deepseek-v4-pro`；该加载顺序现已修复。

## 汇总指标

| 指标 | 结果 |
|---|---:|
| 请求/完成实例 | 5 / 5 |
| 产生非空补丁 | 1 |
| 无补丁 | 4 |
| 补丁生成率 | 20% |
| 正常非空最终回答 | 0 / 5 |
| 总耗时 | 434.195 秒 |
| 平均耗时 | 86.839 秒/实例 |
| 工具调用总数 | 70 |
| 平均工具调用 | 14 次/实例 |
| 事件总数 | 225 |
| 官方 resolved rate | N/A（Docker 未运行） |

## 实例明细

| Instance ID | 状态 | 耗时（秒） | 工具调用 | 事件 | 补丁字符数 |
|---|---|---:|---:|---:|---:|
| `astropy__astropy-12907` | `completed` | 119.973 | 15 | 47 | 3,041 |
| `astropy__astropy-13033` | `no_patch` | 102.039 | 14 | 45 | 0 |
| `astropy__astropy-13236` | `no_patch` | 94.277 | 13 | 43 | 0 |
| `astropy__astropy-13398` | `no_patch` | 55.504 | 17 | 51 | 0 |
| `astropy__astropy-13453` | `no_patch` | 62.402 | 11 | 39 | 0 |

## 补丁质量观察

唯一非空补丁为 `astropy__astropy-12907` 新增的 `_verify_separable.py`。它构建了独立验证脚本，但没有修改 Astropy 生产代码，因此不能视为 issue 已解决。其余四例没有产生 Git diff。

所有实例都运行了完整 10 轮，最后一条 AI 消息和 `final_answer` 均为空。这表明模型持续请求工具直至达到循环上限，没有为总结或最终修复预留轮次。

因此：

- 可确认的补丁生成率是 20%。
- 不能把 `completed=1` 解读为 resolved=1；这里的 completed 只表示 runner 得到了非空 diff。
- 根据补丁人工检查，本批次没有可确认的有效生产修复。
- 在 Docker harness 执行前，官方 resolved rate 必须保持为 N/A，不能报告为 0% 或 20%。

## 工具轨迹观察

五例主要使用 workspace listing、regex search、file read 和 command run。只有第一例调用了文件创建工具，其余实例没有进入有效编辑阶段。部分 command run 失败，其中一次风险命令因未配置 inline approval handler 被拒绝。

暴露的问题：

1. 调查循环缺少截止策略，模型可以用完全部轮次继续读取或运行命令。
2. 没有独立 verifier 检查生产文件是否真的被修改。
3. ReAct 节点结束时把 `passed=True` 作为流程状态，容易和任务验收通过混淆。
4. benchmark 没有统一的非交互审批策略，风险命令可能在无人审批时失败。
5. 最终一轮没有强制禁止工具调用并要求输出总结。
6. 5 个连续样本全部来自 Astropy，能够测试同一大型仓库，但代表性不足，不能外推到完整 Verified 数据集。

## LangSmith 登记

正确的专用数据集已创建：

- 名称：`TacoAgent SWE-bench Verified smoke react 2026-08-28 v1`
- Dataset ID：`b435ff6a-bfe0-49a6-bceb-aeef34a522aa`
- 样例数：5

每条样例包含 instance ID、仓库、base commit、issue 描述、模型补丁、状态、耗时、工具调用数和事件数，并标记 `officially_graded=False`。

本次实时 trace 因 runner 在导入 LangChain 后才加载 `.env` 而返回 401；专用数据集使用显式加载 `.env` 的同步脚本成功上传。runner 的加载顺序已修复，后续运行应能正常初始化 tracing client。

曾误用通用登记脚本创建数据集 `6bccf585-37e2-4cfb-8345-2c848e201ff2`。它不是本次正确 SWE-bench 数据集，且尚未删除；删除远程数据需要明确授权。

## 产物位置

- `evals/results/swebench-smoke/react-5-proxy-20260828/summary.json`
- `evals/results/swebench-smoke/react-5-proxy-20260828/results.jsonl`
- `evals/results/swebench-smoke/react-5-proxy-20260828/predictions.jsonl`
- `evals/results/swebench-smoke/react-5-proxy-20260828/events/*.jsonl`
- `evals/results/swebench-smoke/react-5-proxy-20260828/workspaces/*`

## 改进建议

按优先级建议：

1. 为 ReAct 增加轮次预算：前 40% 调查，中间 40% 编辑与测试，最后 20% 禁止继续调查并收敛结果。
2. 最后一轮取消工具绑定或明确要求只输出 final answer。
3. 若到指定轮次仍无 diff，注入“必须修改生产代码或解释阻塞”的指令。
4. benchmark 默认使用非交互审批策略，并记录所有被放行的风险命令。
5. 在导出补丁前增加只读验证：非空 diff、生产文件变化、测试结果和未提交运行时文件检查。
6. 将 ReAct 接到轻量 verifier；失败时允许一次带明确修复指令的续跑。
7. 下一批选择跨仓库的显式实例 ID，减少连续 Astropy 样本造成的偏差。
8. 启动 Docker 后使用官方 harness 对 `predictions.jsonl` 评分，再补充 resolved rate 与测试日志。

## 结论

代理配置解决了 GitHub 克隆阻塞，runner 能稳定完成 5 个实例并保存完整事件。但是当前 ReAct 在大型陌生仓库修复任务上表现出明显的“调查过度、编辑不足、无法收尾”问题。现阶段更适合作为轻量基线，不宜替代带 Planner/Verifier 反思闭环的默认 single-agent graph 工作流。

