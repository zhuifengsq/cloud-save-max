# 后端可维护性整改交付

范围：[本次审查](backend-maintainability-review-2026-11-16.md) 的五项整改。没有修改运行时依赖、数据库模型、迁移或前端接口，保持单进程单副本部署约束。未提交、未推送、未部署。

## 已实施的改动

### 搜索故障隔离

Quark4K 不再把上游网络异常包装为 RuntimeError。连接错误按单源不可用处理，全超时仍映射到 504，其他正常源的结果保留；真正的编程错误继续传播。混合分页失败优先报告非超时错误，避免完成顺序改变错误分类。

搜索插件基类移到 domain/search.py，原基础设施路径保留兼容导出；应用层不再通过装配器获取具体插件基类。

### 适配器资源生命周期

工厂缓存拥有底层适配器，每次调用返回独立借用句柄。淘汰、清缓存和退出会退休资源，最后一个调用方释放后关闭真实连接，避免关闭仍在执行的请求。调用期间有独立资源固定，借用句柄被并发关闭也不会打断已开始的方法。

补充 HTTP、任务执行和分享读取的资源作用域，以及账号管理器 close/context manager。流式验证显式持有、释放管理器，不跨生成器 yield 持有 ContextVar token。慢登录期间的缓存失效使用 generation 防止旧配置回填，退出后禁止重新插入缓存。

认证挑战异常携带借用句柄，认证会话独立 retain，避免请求结束后验证码流程丢失连接。具体连接持有者实现真实 close。详见 [资源所有权说明](adapter-resource-lifecycle.md)。

### 共享账号认证

Web 与 Telegram 共用 DriveAccountAuthUseCase，二维码、轮询、验证码和短信流程统一依赖认证 provider、账号 gateway 和会话存储。两端保留权限、审计事务、错误码及展示映射。

云端确认成功后缓存候选凭据，只有本地凭据与审计提交成功才消耗认证会话；数据库失败可重试，不重复提交验证码。会话方法串行执行，并在拿到执行权后再次检查会话是否存在。

会话淘汰和操作结束通过独立 state_lock、active_operations、retired 状态完成资源所有权交接，不等待其他会话的操作锁，避免容量淘汰时交叉死锁及退出时遗漏关闭。应用退出原子禁止新会话、清空已有会话；晚到的后台挑战不能重新保留连接。

### 职责拆分

| 原模块 | 原行数 | 当前行数 | 拆分后的职责 |
| --- | ---: | ---: | --- |
| application/tmdb_cache.py | 1214 | 459 | 保留应用编排；日期/语言规则进入 domain，HTTP 抓取、专用 writer/claim SQL、后台提交进入各自适配器。 |
| application/share_preview_batch.py | 1011 | 500 | 保留缓存与应用入口；JSON 项目规则、目录遍历、只读探测、并发流式验证独立。 |
| application/telegram_bot/actions.py | 1068 | 128 | 保留 facade，通过组合调用任务、账号、资源与设置 action；认证委托共享用例。 |

TMDB 原有独立 writer、短写事务、读事务释放和并发控制保留。后台刷新去重区分数据库 bind，限制排队数量；任务失败或提交拒绝会释放占位和 session。纯规则没有数据库或网络依赖。

拆分依据是职责和依赖，不是通过把代码搬入基类/mixin 或动态模块别名隐藏复杂度。原公共函数及插件导入路径保持兼容。

### 类型化装配

container 的仓储访问器使用明确 Protocol，替代 ModuleType；核心任务、搜索源和账号记录有明确字段。真实仓储模块通过 runtime_bindings 静态检查其接口实现。

RunTaskUseCase 的 store、engine、notifier 改为必需构造依赖，生产默认装配集中在 container.run_task_use_case。原来测试默认装配的用例改为调用生产装配入口，保留行为断言；全部依赖均显式给出时，执行用例无需解析全局实现。

严格类型范围扩到 57 个文件，负例覆盖错误字段、未知仓储方法、非法参数和缺失执行依赖。异构网盘 JSON 与部分历史 SQL 投影仍有显式 Any，这次没有声称全仓库已经完全类型化。

## 验证与审查

使用项目 backend/.venv，测试环境为 XXM_TESTING=1、DATABASE_URL=sqlite://、RUN_MIGRATIONS=0、SCHEDULER_ENABLED=0。

- 最终全量：`python -m pytest tests regression_tests -q --basetemp .tmp/maintenance-final-20261116-b`，**1163 passed、2 skipped、1 warning，85.27 秒**。warning 为既有 Starlette/anyio 弃用提示。
- Ruff：All checks passed。
- 严格类型检查：57 个文件通过；13 组非法调用负例被正确拒绝。
- 分层检查：336 modules、1029 internal_imports、109 deferred_imports、0 violations、0 cycles。
- TMDB 的 application→persistence 显式例外已删除。延迟导入指纹逐项检查后更新，预算由原配置 111 降为 109；本次最初实际扫描值是 110。没有提高违规或循环预算。
- 独立空 SQLite：升级到旧检查点、升级 head、降级旧检查点、再升级 head 成功；schema drift 为 0（26 个模型表）；migration preflight 为 head、pending=0。
- git diff --check 通过。

搜索网络故障、适配器关闭/并发借用、认证提交失败重试、跨请求挑战、容量淘汰死锁、操作退出清理竞争、晚到后台认证、线程池迭代流式验证等问题均有回归覆盖。实际修复的故障先观察到测试失败，再确认修复后通过。纯搬迁使用已有行为/事务回归验证前后兼容。

并行实施后进行独立复审，发现并修复了跨请求认证资源持有、短信等待者状态重检、流式 ContextVar、认证锁交叉等待和退出清理竞争。最终认证/生命周期独立复审未发现剩余重要问题。

## 使用边界

本地回归替换了真实网盘 IO，没有执行真实网盘认证、转存或生产负载验证。已有单进程单副本约束保留，不能直接增加 workers 或副本。新增持有连接的网盘适配器需要登记其资源字段；跨操作保留借用时需要 retain，并明确释放责任。
