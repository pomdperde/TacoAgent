# Verifier 可靠性改进记录（2026-08-30）

## 背景

默认 10 分钟 SWE-bench 实验中，5 个实例没有任何一个通过 Verifier。事件日志显示失败被混合成了三类：测试环境不可用、模型没有输出最终 JSON、以及实例硬超时前没有留下最终验证窗口。另有 `_fix_sep.py` 临时脚本被误判为生产代码 diff。

## 已实现改进

### 1. 验证结果四态分类

`VerificationResult` 新增：

```python
status: str    # passed | failed | blocked | timed_out
category: str
```

分类规则：

- 命令退出成功：`passed / command_passed`
- 命令超时：`timed_out / command_timeout`
- `ModuleNotFoundError`、缺少依赖、扩展未构建等：`blocked / environment_unavailable`
- 测试真正执行后非零退出：`failed / command_failed`

Verifier 不再把缺少 `hypothesis`、`erfa`、`setuptools_scm` 或 Astropy 扩展未构建等同于测试断言失败。

### 2. 先执行验证命令，再由模型审查

旧顺序：

```text
模型审查 → 执行验证命令 → Python 合并结果
```

新顺序：

```text
执行验证命令 → 分类结果 → 注入 verifier_input → 模型检查工作区并审查
```

模型现在能看到真实 stdout、stderr、状态和错误类别，可在最终 reason 中区分补丁失败与环境阻塞。

### 3. 强制最终 JSON 审查

如果 Verifier 在最多 4 轮只读工具调用后仍未返回有效 JSON，会再调用一次不绑定工具的模型，明确要求只返回最终评估 JSON。这样避免工具循环耗尽后产生：

```text
Verifier returned no valid assessment
```

若最后调用仍异常，保留安全失败，不会错误判定通过。

### 4. 更严格的通过条件

通过条件现在要求：

- 模型明确返回 `passed=true`；
- 没有真实失败命令；
- 没有环境阻塞或命令超时；
- 至少存在一个可执行且通过的验证命令，或者计划本身没有命令；
- SWE-bench 模式下必须通过 production diff gate。

同时返回：

```python
verification_status = "passed" | "failed" | "blocked"
verifier_summary = reason
```

注意：环境阻塞现在会被准确记录为 `blocked`，但仍不会被错误算作通过。正式通过仍需要可执行验证环境或官方 harness。

### 5. 实例级 deadline 和 Verifier 保留时间

工作流启动时写入：

```python
run_deadline_monotonic = monotonic() + total_time_budget_seconds
verifier_reserve_seconds = verifier_time_budget_seconds
```

Actor 实际可用时间取以下最小值：

```text
Actor 节点预算
全局剩余时间 - Verifier 保留时间
```

因此第二、第三轮 Actor 不会每次重新获得完整 300 秒并侵占最终验证窗口。SWE-bench runner 会把进程级 `timeout_seconds` 同步为工作流的总预算。

### 6. 排除临时修复脚本

production diff gate 现在排除文件名以这些前缀开始的文件：

```text
_fix_
debug_
tmp_
```

因此仅创建 `_fix_sep.py` 不再满足生产代码补丁要求。

## 尚未覆盖

本次没有自动安装 SWE-bench 项目依赖，也没有自动启动官方 Docker harness。原因是依赖安装和 Docker 执行属于独立、可能昂贵且影响外部环境的评测阶段。Verifier 现在会准确报告环境阻塞；正式 resolved rate 仍应由官方 SWE-bench Docker harness 给出。

## 测试

新增测试覆盖：

- 缺少 Python 模块被分类为 `blocked`；
- 真实 pytest 断言失败被分类为 `failed`；
- `_fix_*.py` 不计为生产代码 diff；
- 原有 Planner、Actor、Verifier 和工作流测试保持通过。

最终结果：

```text
116 passed, 1 skipped, 11 subtests passed
git diff --check: passed
```

## 后续建议

1. 在 Docker 可用后，对已有候选 patch 使用官方 SWE-bench harness 评分。
2. 将大段 stderr 截断后再注入模型，保留错误类型、失败测试名和堆栈尾部。
3. 为 `blocked` 增加可选的预构建环境阶段，不占用 Agent 的 600 秒修复预算。
4. 记录首次 production diff、首次测试和最终 Verifier 的实际时间，用于进一步动态调度。
