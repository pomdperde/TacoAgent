# 未完成任务恢复与续作记录（2026-09-06）

## 恢复摘要

根据仓库中时间最新的实验记录
`SWEBENCH_NEW10_RESULTS_2026-09-06.md`、对应评测产物和当前工作区改动，上一个任务是：

1. 完成 SWE-bench Verified 新 10 项实验并分析失败；
2. 修复单实例 600 秒硬超时后 verifier 容器仍可能继续运行的问题；
3. 在 correction 后增加一次只读复验，避免提交未经最终验证的补丁；
4. 验证上述实现并记录结果。

新 10 项实验本身已经完成：10 个实例中 9 个生成非空补丁，官方原始解析得到 6 个
resolved；对 SWE-bench 4.1.0 空参数测试 ID 兼容处理后为 7 个 resolved。未闭环部分是第
2、3 项实现的收尾验证和续作记录。

## 已确认的实现

- `evals/swebench/runner.py` 使用可终止的实例 worker 执行硬墙钟超时；超时后终止
  worker，并按实例工作区前缀清理遗留的 `taco-verifier-*` Docker 容器。
- `src/tacoagent/graph/single_agent_planner_node.py` 将可能阻塞的 planner provider 调用放入
  可终止子进程，并在硬期限后先 terminate、仍存活则 kill。
- `src/tacoagent/graph/actor_node.py` 将执行阶段明确拆分为 diagnosis、first edit、minimal
  verification、correction、final verification；correction 后只允许一次只读复验。
- actor 的 final verification 明确禁止编辑，并要求重跑同一个聚焦失败命令；未经通过的
  correction 不会被误记为已验证。

## 本次续作执行结果

### 聚焦回归

执行：

```text
python -m pytest tests/test_planner_deadline.py tests/test_swebench_run_evaluation.py tests/test_swebench_smoke.py tests/test_graph_nodes.py tests/test_graph_workflow.py -q
```

结果：`31 passed in 10.23s`。

测试结束时 LangSmith 尝试上传 trace，但当前网络策略拒绝连接。上传失败不影响测试退出码，
最终退出码为 0。

### 全量回归

为避免测试 trace 上传干扰，关闭 LangSmith tracing 后执行：

```text
$env:LANGSMITH_TRACING='false'
$env:LANGCHAIN_TRACING_V2='false'
python -m pytest -q
```

结果：`147 passed, 1 skipped, 11 subtests passed in 19.41s`，退出码 0。

唯一跳过项是
`tests/test_file_read_tool.py::FileReadToolTests::test_rejects_symlink_that_leaves_workspace`，
原因是当前 Windows 环境不能创建符号链接，不属于本次改动回归。

## 结论与剩余工作

本次恢复范围内的实现验证已经闭环，没有发现需要继续修改代码的失败。下一项独立工作应是
运行新的 SWE-bench 样本或针对 `astropy-14365`、`astropy-14598` 做定向重跑，以验证策略
改动是否提升真实 resolved 率。该操作会调用外部模型并运行 Docker，具有明显时间与 token
成本，因此没有在缺少明确新评测范围的情况下自动启动。

