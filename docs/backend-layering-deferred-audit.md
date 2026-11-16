# 延迟导入来源复核

日期：2026-11-16。仅扫描 Git 中的 Python 源码，不读取运行数据库或凭据。

后续E/F及适配器解环已完成：本文件的116条与相对循环段保留为C包历史盘点。当前基线为110绝对延迟语句、121完整延迟指纹、11相对引用；相对依赖已进入分层与循环图，原5模块循环已消除。唯一新增延迟装配为container→share_preview_reader；移除旧执行器装配2项、旧通知延迟导入2项、预览旧延迟导入3项、complete_listing反向导入具体适配器3项。独立审计后登记，违规/循环预算仍0。见 [当前总报告](backend-quality-report.md)。

历史基线提交 `ffd0314`：251 模块、90 条延迟导入。实施前 `HEAD`：294 模块、116 条延迟导入。
以来源模块、目标模块及重复次数对照；行号仅用于定位，代码移动不会自动成为新批准。全量逐项清单如下。

净增 26 的来源是新增 28 条、移除/改向 2 条，并非原 90 条之外恰好只增加 26 条：

- `container` 中新增 15 条为既有分享、引流、进度纠正、分阶段执行与批量通知的装配。依据开发规范 §1.1 及既有 [后端改造报告](backend-hardening-report.md) 的 D 包；装配例外只豁免依赖方向，不豁免本次延迟导入清单。
- `tasks -> resource_search`、`generated_share_check -> tx`、`lead_in.materials -> container` 各一条分别来自已有搜索动作、显式读事务结束与引流依赖装配，均位于允许方向；并不以这种导入方式证明路由或应用职责已完成收口。
- `correct_saved_progress -> container` 一条、`run_task -> container/drama_share_autoupdate/shareurl_update_logs` 三条来自纠正用例和现役执行编排。F1 的遗留清理不在 C 内实施，不能由门禁自动搬迁这些调用。
- `drama_plan/progress -> episode_number` 两条替代 `drama_executor -> episode_number` 两条，属于已拆分计划与进度模块的来源迁移，净增零。
- `task_run_stages` 的四条为通知配置过滤、TMDB 资料更新、应用错误及通知设置仓储依赖；基础设施允许这些方向，仍受通知时机与事务边界的独立行为测试约束。

上述为本次任务书允许的现状复核与登记，均为实施前已有代码；不新增新的分层例外，不证明外部 IO 已完成联调。

| 来源 | 目标 | 当前行 | 相对 90 条基线 | 职责与依据 |
| --- | --- | --- | --- | --- |
| `app.api.routes.tasks` | `app.application.magic_regex` | 59 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.api.routes.tasks` | `app.application.resource_search` | 70 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.api.routes.tasks_share` | `app.application.share_preview_batch` | 68 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 33 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 40 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 47 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 54 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 61 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 68 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 75 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 82 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 89 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 96 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 103 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 110 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 117 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 124 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 131 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 138 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 145 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 152 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 159 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 166 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 173 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.core.runtime.clock` | 185 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.session` | 195 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 206 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.adapter_registry` | 216 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.gateway` | 227 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.factory` | 236 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.adapters.base_adapter` | 242 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.adapters.aliyun_adapter` | 248 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.adapters.drive_auth` | 254 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.account_manager` | 260 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.scheduling.task_scheduler` | 266 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.scheduling.task_admission` | 272 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.task_executor` | 278 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.execution_log` | 284 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.magic_rename` | 290 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.http.douban_client` | 296 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.http.tmdb_client` | 302 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.http.tmdb_client` | 308 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.search.base` | 314 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.drama_executor` | 320 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.task_share` | 326 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.saved_progress` | 331 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.saved_progress` | 336 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.application.use_cases.correct_saved_progress` | 341 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.task_share` | 346 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.application.use_cases.create_task_share` | 351 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 356 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.persistence.repositories` | 361 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.application.use_cases.task_lead_in` | 366 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.drive.lead_in_provider` | 371 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.task_run_stages` | 376 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.task_run_stages` | 381 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.infrastructure.execution.task_run_notifier` | 386 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.application.use_cases.run_task` | 391 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.container` | `app.application.notifications.task_batch` | 396 | 新增 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.drama_schedule` | `app.application.container` | 240 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_schedule` | `app.application.tmdb_cache` | 241 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_schedule` | `app.application.tmdb_settings` | 242 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_schedule` | `app.application.tmdb_settings` | 290 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_share_autoupdate` | `app.application.share_preview_batch` | 169 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_share_autoupdate` | `app.application.system_settings` | 191 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_share_autoupdate` | `app.application.resource_search` | 202 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_share_autoupdate` | `app.application.share_preview_batch` | 331 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.drama_share_autoupdate` | `app.application.share_preview_batch` | 366 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.factories` | `app.infrastructure.execution.task_executor` | 19 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |
| `app.application.generated_share_check` | `app.core.runtime.tx` | 124 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.lead_in.materials` | `app.application.container` | 213 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.notifications.legacy_notify` | `app.application.notifications.queue` | 813 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.notifications.legacy_notify` | `app.core.settings` | 841 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.resource_search` | `app.application.container` | 29 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.resource_search` | `app.application.container` | 101 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.resource_search` | `app.application.container` | 190 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.resource_search` | `app.application.container` | 222 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.resource_search` | `app.application.api_search_sources` | 278 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.resource_search` | `app.application.search_plugins` | 279 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview` | `app.application.tmdb_settings` | 81 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview` | `app.application.magic_regex` | 141 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview` | `app.application.container` | 278 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview_batch` | `app.application.container` | 250 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview_batch` | `app.application.container` | 366 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview_batch` | `app.application.dto.task_browse` | 798 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview_batch` | `app.application.system_settings` | 799 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview_batch` | `app.application.dto.task_browse` | 961 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.share_preview_batch` | `app.application.container` | 967 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.tmdb_view` | `app.application.drama_schedule` | 121 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.tmdb_view` | `app.application.tmdb_cache` | 122 | 保留 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.use_cases.correct_saved_progress` | `app.application.container` | 9 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.use_cases.run_task` | `app.application.container` | 77 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.use_cases.run_task` | `app.application.drama_share_autoupdate` | 117 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.application.use_cases.run_task` | `app.application.shareurl_update_logs` | 118 | 新增 | 既有应用动作/工具按需加载；允许层方向，不代表职责已经收口 |
| `app.infrastructure.drive.adapters` | `app.infrastructure.drive.factory` | 39 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.drama_executor` | `app.application.episode_number` | 814 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.drama_plan` | `app.application.episode_number` | 117 | 新增 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.drama_progress` | `app.application.episode_number` | 29 | 新增 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_executor` | `app.application.magic_regex` | 150 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_executor` | `app.application.tmdb_cache` | 169 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_executor` | `app.application.task_share_links` | 241 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_executor` | `app.application.task_executions` | 351 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_run_stages` | `app.core.errors` | 26 | 新增 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_run_stages` | `app.application.tmdb_cache` | 34 | 新增 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_run_stages` | `app.infrastructure.persistence.models.notification_setting` | 40 | 新增 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.execution.task_run_stages` | `app.application.notifications.settings` | 41 | 新增 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.task_executions` | `app.infrastructure.persistence.models.task` | 86 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.tasks` | `app.infrastructure.persistence.models.task_execution` | 28 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.tasks` | `app.infrastructure.persistence.models.task_execution` | 56 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.tasks` | `app.infrastructure.persistence.models.task_execution` | 110 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.tasks` | `app.infrastructure.persistence.models.tmdb_media_cache` | 197 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.users` | `app.infrastructure.persistence.models.user` | 60 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.users` | `app.infrastructure.persistence.models.permission` | 73 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.users` | `app.infrastructure.persistence.models.role` | 74 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.infrastructure.persistence.repositories.users` | `app.infrastructure.persistence.models.user` | 75 | 保留 | 基础设施按需加载执行/通知/调度依赖；保留单进程约束 |
| `app.main` | `app.application.task_executions` | 80 | 保留 | 装配点延迟加载实现；开发规范 §1.1、架构演进 §10.3 决策4 |

历史条目删除/改向（含同一导入的目标解析变化）：

- `app.infrastructure.execution.drama_executor -> app.application.episode_number` × 2

当前条目增加（含装配与目标解析变化）：

- `app.api.routes.tasks -> app.application.resource_search` × 1
- `app.application.container -> app.application.notifications.task_batch` × 1
- `app.application.container -> app.application.use_cases.correct_saved_progress` × 1
- `app.application.container -> app.application.use_cases.create_task_share` × 1
- `app.application.container -> app.application.use_cases.run_task` × 1
- `app.application.container -> app.application.use_cases.task_lead_in` × 1
- `app.application.container -> app.infrastructure.drive.lead_in_provider` × 1
- `app.application.container -> app.infrastructure.drive.saved_progress` × 2
- `app.application.container -> app.infrastructure.drive.task_share` × 2
- `app.application.container -> app.infrastructure.execution.task_run_notifier` × 1
- `app.application.container -> app.infrastructure.execution.task_run_stages` × 2
- `app.application.container -> app.infrastructure.persistence.repositories` × 2
- `app.application.generated_share_check -> app.core.runtime.tx` × 1
- `app.application.lead_in.materials -> app.application.container` × 1
- `app.application.use_cases.correct_saved_progress -> app.application.container` × 1
- `app.application.use_cases.run_task -> app.application.container` × 1
- `app.application.use_cases.run_task -> app.application.drama_share_autoupdate` × 1
- `app.application.use_cases.run_task -> app.application.shareurl_update_logs` × 1
- `app.infrastructure.execution.drama_plan -> app.application.episode_number` × 1
- `app.infrastructure.execution.drama_progress -> app.application.episode_number` × 1
- `app.infrastructure.execution.task_run_stages -> app.application.notifications.settings` × 1
- `app.infrastructure.execution.task_run_stages -> app.application.tmdb_cache` × 1
- `app.infrastructure.execution.task_run_stages -> app.core.errors` × 1
- `app.infrastructure.execution.task_run_stages -> app.infrastructure.persistence.models.notification_setting` × 1

这次按已记录现状建立 116 条预算与来源/目标多重清单；不放行分层违规或循环。
后续新增来源/目标即使总数未增也失败；行号变化不影响批准身份。C 仅登记既有装配/按需加载，并未把延迟导入当成职责重构完成。

## 复审补充：完整导入指纹与相对依赖边界

仅记录 `from 包 import 名称` 的包名会把同包下的不同子模块折叠为一个批准项。现新增 `approved_deferred_imports`：130 条完整语句指纹（116 条绝对 app 延迟导入、14 条相对延迟导入），包括来源模块、原始导入路径和全部 `alias.name`，按名称排序，不包含 `asname`。完整 130 条机器清单位于 `backend/.layering-baseline.json`；增加名称、同包换子模块、同次数替换均需显式批准，改本地别名或移动行号无需重新登记。

绝对 `from app... import 子模块` 的现有模块目标会进入分层和循环图；原始语句计数与语句指纹保持分离，不用多个名称扩大旧 116 条预算。仅含数字的旧基线仍兼容；本项目基线必须同时保留完整批准清单。

相对导入不进入既有绝对依赖图/116 数字预算。为防改成相对导入绕过门禁，另用 `approved_relative_imports` 冻结**所有作用域**的相对导入，共 14 条（当前均为函数内），新增顶层或延迟相对导入均保守拒绝。以下为本次新增登记的 14 条历史引用，均服务已有完整目录读取能力；不代表本次新增业务依赖：

| 来源（均位于 `app.infrastructure.drive.adapters`） | 规范化导入 | 行 |
| --- | --- | --- |
| `aliyun_adapter` | `from .complete_listing import complete_listing` | 719 |
| `baidu_adapter` | `from .complete_listing import complete_listing` | 1109 |
| `base_adapter` | `from .complete_listing import failure,validate_result` | 30 |
| `cloud115_adapter` | `from .complete_listing import complete_listing` | 383 |
| `cloud139_adapter` | `from .complete_listing import complete_listing` | 606 |
| `cloud189_adapter` | `from .complete_listing import complete_listing` | 1698 |
| `complete_listing` | `from .aliyun_adapter import ADRIVE_V3_FILE_LIST` | 223 |
| `complete_listing` | `from .xunlei_adapter import API_BASE,FILE_LIST_FILTERS` | 235 |
| `complete_listing` | `from .guangya_adapter import _is_success` | 287 |
| `guangya_adapter` | `from .complete_listing import complete_listing` | 1086 |
| `pan123_adapter` | `from .complete_listing import complete_listing` | 727 |
| `quark_adapter` | `from .complete_listing import complete_listing` | 280 |
| `uc_adapter` | `from .complete_listing import complete_listing` | 263 |
| `xunlei_adapter` | `from .complete_listing import complete_listing` | 632 |

探索性将这些相对导入解析进循环图后，发现一个历史 5 模块强连通分量：`aliyun_adapter`、`base_adapter`、`complete_listing`、`guangya_adapter`、`xunlei_adapter`。具体往返包括 `complete_listing → aliyun_adapter → complete_listing`、`complete_listing → guangya_adapter → complete_listing`、`complete_listing → xunlei_adapter → complete_listing`，适配器还经 `base_adapter → complete_listing` 相连。

本轮未提高循环预算、未重构这些适配器；原图 `cycles=0` 只覆盖绝对引用，不是对所有相对/动态依赖无环的承诺。后续应单独抽离完整目录读取所需常量和状态判断，再扩展相对循环分析；当前保守拒绝所有未批准相对引用，避免新增同类依赖悄然进入。
