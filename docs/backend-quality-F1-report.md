# F1：遗留执行兼容入口清理

日期：2026-11-16。已本地实施，未提交或推送。

## 调用证据与范围

实施前搜索 `RunTaskUseCase(`、`.run(`、`executor_cls`、`use_unified_run_use_case`、`_legacy_execute`，覆盖 backend/app、测试、scripts 和 docs。生产 HTTP/SSE、scheduler、Telegram、preview 均使用 execute；只有 test_run_task_use_case 调用 run。run 关闭旧开关时调用 _legacy_execute，开关并不控制现役 execute。仓库没有发现明确的外部兼容承诺；本地扫描不能证明仓库外不存在调用方。

删除 run、_legacy_execute、executor_cls 注入缝、专属开关参数测试与 settings 字段；删除全仓无调用的 factories.py 和 container.task_executor_cls。保持现役 store/engine/notifier 装配。保留策略真值表、输入归一化、非法触发方、不可变性、准入职责和现役装配测试；没有删除整个原测试文件，也没有把历史代码搬到测试目录自测。

开发规范示例改 execute，入口不再二次提交；架构 README、ADR 0002、旧验收报告同步退役状态。历史演进设计保留原始事实并在文首和相关回滚表更新退役说明。其他有效通知/锁开关保持原语义；旧执行兼容代码只能通过回滚 F1 恢复。

## 验证

本包为删除未使用代码的行为保持重构，先建立 execute 五类触发方行为基线，不编写“私有方法必须不存在”的镜像断言。既有真实 SQLite 分阶段提交测试保留；现役运行星期/手动越过日期策略、准入、preview 不持久化执行/不发送运行通知、调度汇总一次发送分别由现役测试覆盖。

统一环境：XXM_TESTING=1，DATABASE_URL=sqlite://，RUN_MIGRATIONS=0，SCHEDULER_ENABLED=0，项目 .venv Python。从 backend 运行：

```text
python -m pytest regression_tests/test_run_task_use_case.py regression_tests/test_task_run_phases.py regression_tests/test_drama_lead_in_trigger.py -q --basetemp .tmp/f1-baseline
30 passed in 2.87s

python -m pytest regression_tests/test_run_task_use_case.py regression_tests/test_task_run_phases.py regression_tests/test_drama_lead_in_trigger.py regression_tests/test_ports_conformance.py -q --basetemp .tmp/f1-green
36 passed in 3.38s
```

F1/F2组合末次验收还加入 test_drama_run_schedule、test_task_run_conflicts、test_notification_delivery、test_notify_queue：`--basetemp .tmp/f-acceptance`，输出 **109 passed in 5.43s**。独立工具 Ruff通过、mypy strict通过（当时11文件，含同期 E 新端口）。完整后端回归及最终分层门禁由主报告统一记录，局部结果不冒充远端CI通过。

最后安全日志调整后，同一组合用 `--basetemp .tmp/f-final` 再验：**109 passed in 4.86s**，Ruff `All checks passed!`。

未访问运行库或真实网盘，真实服务未联调。无模型、schema、错误或SSE契约改动，不改网盘写入重试、通知口径或事务所有权。

## 回滚

主代理集成补验 `test_execute_trigger_behavior.py`：真实execute/store/engine/drama链路与独立SQLite，仅假网盘IO；固定非运行日，覆盖五触发方×分享valid/invalid共10例。实际断言转存/跳过、preview零执行记录和通知精确次数/内容。`--basetemp .tmp/quality-trigger-green2` 输出10 passed in3.75s，最终全量亦通过。没有用策略布尔或通知次数上限替代业务验证。

按本包恢复 run_task、旧开关、factories与container导出及对应测试/文档。不要整文件恢复 settings/container/development-guide 而覆盖 A/C/E；按差异块回滚。执行用例提交点数量不变，不新增旁路会话。
