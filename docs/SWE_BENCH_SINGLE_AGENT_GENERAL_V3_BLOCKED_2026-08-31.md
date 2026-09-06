# Single-agent general-v3 评测尝试（2026-08-31）

## 结论

本轮计划执行 10 个实例，但没有得到可用于衡量 Agent 质量的结果。

- 已记录：5 个实例
- 真正进入并完成 Agent：0
- 生成补丁：0
- 官方验证：0
- 剩余未启动：5

前三个实例的模型调用均返回 HTTP 402 `Insufficient Balance`。随后两个实例因旧 partial-clone 缓存缺少完整 Git tree 而无法 checkout。确认模型服务持续拒绝请求后主动停止运行，避免向剩余实例重复同一基础设施错误。

这些结果不能计作 TacoAgent 未通过案例，也不能与上一轮 10% resolved rate 比较。

## 工程检查

通用工作流改进后的测试结果为 `118 passed, 1 skipped, 11 subtests passed`，`git diff --check` 通过。

评测适配层的本地缓存检查也已修正：缓存必须包含目标提交的完整 tree，不能只存在 commit 引用。

## 恢复条件

1. 恢复当前模型服务余额，或配置另一个可用的 OpenAI 兼容服务。
2. 使用全新 run ID 从头运行全部 10 个实例，不复用本轮质量统计。
3. 生成结束后，将非空补丁交给官方 Docker harness。

原始记录：`evals/results/swebench-smoke/single-agent-general-v3b-10-20260831/`
