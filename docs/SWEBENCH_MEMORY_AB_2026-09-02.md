# SWE-bench 记忆机制 A/B 实验记录

日期：2026-09-02

## 实验配置

- 数据集：SWE-bench Verified
- 实例：`astropy__astropy-13033`
- 模型：`deepseek-v4-pro`
- 工作流：single-agent
- A：`legacy-uniform`
- B：`node-trimmed`
- 固定参数：`max-attempts=1`，planner/actor/verifier 预算为 60/150/60 秒，实例硬超时 300 秒。

## 首轮运行结果

| 运行 | 状态 | Agent 耗时 | Provider usage | 是否可纳入配对统计 |
|---|---|---:|---:|---|
| legacy-uniform | planner API 超时 | 156.106 秒 | 0（响应未返回） | 否 |
| node-trimmed（首次） | GitHub TLS 握手失败 | 10.486 秒 | 0（未进入 Agent） | 否 |
| node-trimmed（重试） | 进入 planner/actor/verifier | actor 约 6,311.5 秒 | input 118,401；output 10,419；total 128,820 | token 可用，耗时无效 |

裁剪组重试共记录 14 次模型调用。planner 首次调用为 1,902 input tokens、650 output tokens。完整事件保存在对应运行目录。

## 结论

本轮没有形成可用于计算“前后降幅”的有效配对样本。旧组没有返回 usage；裁剪组虽然返回了 usage，但兼容端点的在途请求突破阶段预算，且外层进程未在 300 秒可靠终止。因此不能将 156.106 秒与 6,311.5 秒解释为记忆机制的性能差异。

评测能力已经补齐：运行时可在同一版本切换两种记忆 profile；planner、actor、verifier 与 context compressor 会产生标准 `model_usage` 事件；SWE-bench 的实例结果和汇总报告会记录 provider-reported token。

## 下一轮有效实验要求

1. 使用能可靠遵守 request timeout 的模型端点，或在独立容器级别实施强制终止。
2. 固定至少 5 个实例，并交错运行 A/B，降低服务负载随时间变化造成的偏差。
3. 仅配对比较两组均完成相同阶段的实例；分别报告 input/output/total tokens、墙钟中位数与 P95。
4. 同时报告补丁生成率或官方 resolved rate，防止通过提前失败获得虚假的低 token、低耗时。

## 第二轮重跑（修复 runner 后）

修复 multiprocessing 队列读取顺序后，以相同实例和相同配置重新执行。旧组在硬上限正常终止，裁剪组在上限内完成一次完整工作流。

| 指标 | legacy-uniform | node-trimmed | 变化 |
|---|---:|---:|---:|
| Agent 墙钟时间 | 240.332 秒（超时） | 206.986 秒（正常结束） | -13.87% |
| 模型调用次数 | 5 | 10 | +5 |
| Input tokens（原始总量） | 44,721 | 61,664 | +37.89% |
| Output tokens（原始总量） | 15,946 | 13,246 | -16.93% |
| Total tokens（原始总量） | 60,667 | 74,910 | +23.48% |
| 工具调用 | 7 | 8 | +1 |

原始总量不能直接解释为裁剪增加 token：裁剪组完成了 verifier 和 final，旧组在 actor 阶段被硬超时。按两组共同拥有的前 5 次模型调用比较，旧组 input 为 44,721、裁剪组为 41,032，下降 8.25%；total 分别为 60,667 和 53,283，下降 12.17%。

前 3 次可比 actor 调用的 input 分别为 `5,266 → 2,775`（-47.30%）、`7,476 → 5,505`（-26.37%）、`14,101 → 10,574`（-25.01%）。之后两组工具轨迹已明显分叉，不再是严格相同的 prompt。

本轮结论：节点裁剪降低了共同执行前缀的 token，并让该实例在相同硬预算内走完工作流；但样本只有 1 个，且模型输出存在随机性，不能把该数值外推为整个 SWE-bench 的平均收益。需要至少 5 个实例、多次交错重复并报告置信区间。
