# ADR 0002：任务准入保留在 6 个调用点，不收进 RunTaskUseCase

> 2026-11-13：继续保留入口准入及 409/429 时机；执行器装配、结果提交、审计与最终通知已迁入应用用例。原有“用例仅统一参数”的描述保留为当时决策背景，不再描述当前实现。见 [验证报告](../../backend-hardening-report.md)。

- 状态：已接受（Accepted，2026-11-09）
- 决策者：架构组
- 相关：方案 `docs/architecture-evolution-plan.md` §3.5、§10.3 决策 1
- 影响文件：`backend/app/application/use_cases/run_task.py`、`backend/app/infrastructure/scheduling/task_admission.py`

## 背景

方案 §3.5 原本的设计是「所有执行入口收敛到一个用例，用例内部负责准入（并发上限 4 / 同任务去重）」。动手时发现这条做不到：

`TaskAdmission.reserve()` 是**同步抛 HTTP 状态码**的：

~~~python
# backend/app/infrastructure/scheduling/task_admission.py:18-27
def reserve(self, task_id: int | None) -> TaskSlot:
    with self._lock:
        if task_id is not None and task_id in self._task_ids:
            raise ApiError("TASK_ALREADY_RUNNING", "该任务正在执行，请等待本次执行结束", 409)
        if self._active >= self._max_active:
            raise ApiError("TASK_CAPACITY_EXCEEDED", "正在执行的任务过多，请稍后再试", 429)
~~~

而 6 个调用点对「被拒绝」的呈现方式各不相同：

| 调用点 | 入口形态 | 现在被拒绝时的表现 |
| --- | --- | --- |
| `api/routes/tasks.py:172` | `with task_admission.reserve(task_id):` | HTTP 409 / 429 JSON（`{code,message,detail}`） |
| `api/routes/tasks_stream.py:156` | 先 `slot = reserve(task_id)` 再建 SSE | HTTP 409 / 429 JSON，**在流建立之前** |
| `api/routes/tasks_stream.py:256` | `slot = reserve(None)`（预览，不占任务去重） | 同上 |
| `infrastructure/scheduling/task_scheduler.py:236` | 调度线程内 `reserve(task_id)` | 记日志、跳过本轮，不影响其它任务 |
| `application/telegram_bot/handlers.py:2345` | TG 命令处理线程内 `reserve(task_id)` | 回一条中文提示消息 |
| `application/use_cases/run_task.py`（文档表） | 用例本身**不做准入**，只收执行参数 | — |

如果把 `reserve()` 放进用例，用例就必须吞掉 `ApiError` 再返回「失败结果对象」，于是：

- HTTP 触发方从 409/429 变成 **200 + `{status:"failed"}`**；
- SSE 触发方从「连接前 409/429」变成「200 + `done{failed}`」，前端 `http.ts` 的重试与 toast 策略依赖状态码，会静默走错分支；
- 既有回归测试 `regression_tests/test_task_admission.py`（5 项，其中 `test_slots_reject_duplicates_and_previews_when_full_then_release_after_failure`
  直接断言 HTTP 409/429，另有 2 项断言 `TASK_ALREADY_RUNNING` 错误码）必须以「保持 409/429」为前提 —— 而「不影响现有业务」是本轮重构的硬约束。

## 决策

**准入不进用例**（选 B）。用例只做「执行参数归一化」：

~~~python
RunPolicy(trigger="http" | "sse" | "scheduler" | "telegram" | "preview",
          share_check_always_notify: bool, persist_execution: bool)
~~~

6 处 `reserve()` 原位保留（含 `tasks_stream.py:256` 的 `reserve(None)`），用例内部由 `RunTaskUseCase.run()`
统一调用 `TaskExecutor`，并在 `settings.use_unified_run_use_case=False` 时回退 `_legacy_execute()` 逐字复刻旧实参。

## 后果

- ✅ 6 个执行入口收敛为 1 个（`TaskExecutor(...)` 直连从 6 处降到 0，只由用例注入），HTTP/SSE 契约零变化。
- ⚠️ 准入逻辑仍是 6 份调用点代码：新增执行入口时必须自己 `reserve()`，否则绕过并发上限。
  `regression_tests/test_run_task_use_case.py` 与真值表注释（`run_task.py:54-67`）用于提醒。
- ⚠️ `TaskAdmissionPort` 未接线（`domain/ports/task.py` 只有 `TaskRunUseCasePort`），准入仍是 infrastructure 的具体类。

## 备选方案（已否决）

| 方案 | 否决原因 |
| --- | --- |
| 用例内准入 + 返回结果对象，调用点再把结果映射成 409/429 | 可行但需要「拒绝原因 → 状态码」的映射表回到 6 个调用点，等于把逻辑换个地方重复；且 SSE 要在建流前拿到结果，得引入预检接口，复杂度更高 |
| 用例内准入 + 抛异常给路由 | 用例就成了 HTTP 感知层，违反 §3.1「application 不感知协议」 |
| 改成「排队 + 立即返回任务 id」 | 需要持久化队列（DB 表）与轮询/推送，属于产品行为变更，不属本轮 |

## 重新评估的触发条件

1. 前端愿意接受「200 + failed 结果对象」的准入拒绝语义（需一次前端契约变更，见 `docs/architecture/adr/0003` 之外的前端契约 golden）；
2. 执行模型改为「持久化队列 + worker」（届时准入 = 队列容量，不再是进程内计数器）；
3. 需要按网盘/账号维度的多级限流（`KeyedRateLimiter` 已有，但准入策略需要重新设计）。
