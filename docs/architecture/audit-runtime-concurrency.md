# 运行时与并发专题审计：线程模型、锁、SQLite 写竞争、幂等与扩展阻塞点

> 范围：cloud-save-max 后端（Python 3.13 / FastAPI 0.115 / SQLAlchemy 2.0 sync / SQLite WAL / APScheduler / threading）
> 方法：静态代码阅读 + 调用点全仓 grep；未运行压测、未跑 pytest（基线由 Lead 负责）
> 不重复：docs/backend-optimization-baseline.md 已完成的 P0–P5（eager loading、SSE 异步化、QueuePool、gzip、适配器 init TTL 去重与缓存加锁、metrics 鉴权、权限缓存、tasks 路由拆分）
> 依赖约束：不得增删依赖；跨进程能力一律标注为「本期不做」，替代方案见第 12 节

## 1. 结论摘要（按处置优先级）

| # | 结论 | 一句话依据 |
|---|---|---|
| C1 | 系统有 **4 套互不兼容**的 SQLite 锁重试实现，而 **HTTP 写路径一次重试都没有** | `backend/app/db/session.py:42-43` 只有判定函数；重试散落在 `task_scheduler.py:266-273`、`telegram_bot_manager.py:19-29`、`tmdb_cache.py:51` |
| C2 | 执行记录**只在任务末尾**一次性落库，进程中断即完全无记录，也没有 running 态可观测 | `backend/app/extensions/runtime/task_executor.py:373-388`；SSE 异常路径伪造 `id=0`：`backend/app/api/routes/tasks_stream.py:195-220` |
| C3 | 三个同步长任务端点直接把分钟级网络 IO 放在 anyio 工作线程上，且 DB 事务覆盖全程 | `backend/app/api/routes/tasks.py:216-231`、`backend/app/api/routes/tasks_share.py:435-448`、`backend/app/api/routes/media_discovery.py:128-169` |
| C4 | 连接池上限 15，潜在同时用库线程 60+，没有统一并发闸门 | `backend/app/db/session.py:18-22`；线程清单见第 2 节 |
| C5 | 全局锁把进程串行化：通知锁内含无超时 join、适配器工厂 RLock 内含网络构造、限速锁内 sleep | `notifications/legacy_notify.py:742-795`、`adapter_factory.py:149-168`、`base_adapter.py:66-76` |
| C6 | 线程创建无统一上限，也没有 single-flight：准入限制只作用于少数入口，插件/预览/SSE 各自起线程 | `task_admission.py:52-61`、`search_plugins.py:217-235`、`share_preview_batch.py:963` |
| C7 | 追剧调度是「单 job 内串行 + coalesce + misfire_grace=60s」，失败模式是**静默漏跑**而非变慢 | `backend/app/extensions/runtime/task_scheduler.py:101-204, 207-285` |
| C8 | 部署是单进程硬编码，多 worker 会重复迁移/重复调度/重复 bot 轮询/每进程各登录一遍网盘 | `Dockerfile:53-55`、`backend/app/main.py:66-92` |

## 2. 线程模型全景

### 2.1 线程来源与上界

| 来源 | 位置 | 数量上界 | 在做什么 | 是否可饿死请求线程 |
|---|---|---|---|---|
| anyio 默认线程池（同步端点） | FastAPI/Starlette 默认 40 tokens | 40 | 执行所有 `def` 路由 | 是：长任务端点长期占用 token |
| 任务执行线程（每任务一条 daemon Thread） | `task_admission.py:52-61` | 并发 4（仅受 reserve 约束的入口） | 网盘转存全链路（分钟级） | 间接：占满连接池后再挤占请求线程的 DB 等待 |
| APScheduler BackgroundScheduler | `task_scheduler.py:38-45` | 默认 10（本项目 4 个 job） | 追剧任务串行、缓存刷新、账号探测、审计清理 | 否（独立线程），但 job 内串行会拖垮定时精度 |
| Telegram 轮询线程 | `telegram_bot_manager.py:44, 98-114` | 1 | 长轮询 + **同步内联处理**（含搜索） | 否，但会阻塞 bot 自身 |
| 搜索插件线程 | `search_plugins.py:232`（裸 Thread）、`:248`（池 ≤5） | 8 个 permit + 每请求 ≤5 | 第三方站点搜索（超时 8–30s） | 是：从请求线程同步等待 `finished.wait` |
| 业务固定池 | `media_discovery.py:19`(6)、`tmdb_cache.py:28`(4)、`backend/app/extensions/search_plugins/quark4k_plugin.py:83`(2)、`backend/app/extensions/search_plugins/quarksoo_plugin.py:163`(len(candidates))、`share_preview_batch.py:963`(≤20)、`backend/app/services/api_search_sources.py:230`(≤5) | 合计可 >40 | 预览、TMDB、搜索 | 是：预览/验证在请求线程内 wait |
| 通知渠道线程 | `notifications/legacy_notify.py:793` | 每渠道 1，全局串行 | 推送 | 是：持全局锁 join，最长 30s/渠道 |

### 2.2 关键论断

- **同步长任务端点 = 线程池黑洞**：`backend/app/api/routes/tasks.py:216-231` 是 `def post_run_task`，函数体内 `with task_admission.reserve(task_id)` 到 `db.commit()` 之间是完整一次网盘转存（`task_executor.py:95-107` 一路同步 requests）。期间：①占用 1 个 anyio token；②`Depends(get_db)` 的 Session 从进入函数到 `db.commit()` 全程持读事务（SQLite 下即使只读也钉住 WAL 快照）；③`audit.write_audit_log`（`tasks.py:221`）与执行记录同事务。10 个用户各点一次「运行」，40 个 token 立刻失效 10 个，且这 10 个连接长期占用 QueuePool。
- **SSE 验证流复用请求线程的 Session**：`tasks_share.py:435-448` 用同步生成器 `event_generator()` 捕获 `db`；Starlette 对同步迭代器逐次 `iterate_in_threadpool`，**每次 next() 可能落在不同线程**，却复用同一个 Session/连接，且连接直到流结束才归还。P5 只把 `tasks_stream.py` 改成 async + 队列，这条路径没动。
- **图片代理是最便宜的 DoS 面**：`backend/app/api/routes/media_discovery.py:128-169` 同步 def、无鉴权、未命中缓存即同步 DNS+下载；`proxy_image_cache.py:272-300` 在分片锁内做下载（`:286-291`），64 个分片意味着不同 URL 落同分片时互相串行。前端海报墙一次 20+ 图 = 20+ token。
- **追剧任务实际并发恒为 1**：`task_scheduler.py:207-285` 在**一个 job 内**用 `for task_id in ...` 串行跑；`task_admission.reserve`（`:235`）在这条路径上没有并发意义（见 `:248-254` 的 `with SessionLocal() as db` 内层循环）。

### 2.3 线程的可观测性缺口

- 全仓只有一处给线程起了名字：通知渠道线程 `name=mode.__name__`（`notifications/legacy_notify.py:793`）。任务线程（`task_admission.py:58`）、搜索插件线程（`search_plugins.py:232`）、bot 线程（`telegram_bot_manager.py:44`）都是匿名线程。
- 没有线程/池占用指标：`core/instrumentation.py` 只埋 SQL 与外部 HTTP，`task_admission._active`（`task_admission.py:18-27`）是现成的活跃计数却没有导出到 metrics。
- 后果：线上卡顿只能靠进程外工具（py-spy/thread dump）定位；无法回答「是池满了还是锁等了」。
- 低成本修复：4 处 `Thread(...)` 补 `name=` 前缀；metrics 增加「活跃任务数 / 池等待数 / 连接池 checkedout」，全部可由现有对象读出，无新依赖。

## 3. 连接池与线程规模错配

- 池：`backend/app/db/session.py:18-22`，`pool_size=5`、`max_overflow=10`、`pool_timeout=30`、`pool_recycle=3600`；PRAGMA 在 `:28-37`（WAL、`busy_timeout=30000`）。
- 潜在同时用库线程：40（anyio）+ 4（任务）+ 10（调度）+ 1（Telegram）+ 8（搜索）+ 5+6+4+2+20（业务池）→ **峰值远超 15**。
- 全仓只有一处做了池隔离：`backend/app/services/tmdb_cache.py:51` 用 `NullPool` + `timeout=0.5` 的独立 writer engine，避免 TMDB 长写拖住主池。这是**正确样板**，但只覆盖了这一处。
- 后果：短请求线程在 `pool_timeout=30` 内排队，表现为「偶发 30s 卡顿 + 500」；P3 已加慢请求日志（`middlewares/timing.py`），但 metrics 键基数问题（见 7.3）让聚合失效。

### 3.1 数值模型（为什么 15 个连接一定不够）

稳态下连接占用 ≈ 长任务数 + 后台 job 数 + 短请求并发数：

| 占用方 | 数量 | 依据 |
|---|---|---|
| 长任务（4 并发上限） | 4 | `task_admission.py:64`，HTTP 路径事务覆盖全程 |
| 调度器 job（追剧/缓存/探测/清理） | 1–3 | 各自 `with SessionLocal()`（`task_scheduler.py:248, 279, 373`） |
| 短请求 | 取决于并发，每个请求在 commit/返回前占 1 个 | `session.py:46-54` |

→ 可用连接 ≈ `15 − (4 + 3)` = **8**。短请求并发超过 8 即进入 `pool_timeout=30` 的排队；排队期间 anyio token 也被占着，于是「连接等待」被放大成「线程池饥饿」。这是 R01/R02/R03 三条风险共享的同一条因果链。

## 4. 锁清单（25 个锁对象）

| # | 位置 | 保护对象 | 作用域 | 风险 |
|---|---|---|---|---|
| L1 | `task_admission.py:18` | 活跃任务计数/ID 集合 | 进程 | 单进程假设；多 worker 直接失效 |
| L2 | `adapter_factory.py:65` RLock | 适配器实例缓存 | 进程 | **锁内含构造（网络登录）**（`:149-168`），一个慢登录阻塞所有网盘的适配器获取 |
| L3 | `base_adapter.py:54`（_rate_limit_lock） | 单实例请求节流 | 每适配器实例 | **持锁 time.sleep**（`:66-76`），同账号多线程请求被强制串行，每请求额外 50–100ms |
| L4 | `base_adapter.py:59`（_init_lock RLock） | init 结果与时间戳 | 每实例 | 持锁做登录（`:168-194`）；P2-a 的 TTL 去重已缓解，但重建实例后失效 |
| L5 | `aliyun_adapter.py:223, 238-276` _token_lock | access_token 刷新 | 每实例 | **锁内 30s HTTP 且无锁内二次校验**（`:238-276`），N 线程排队各自刷新 → 放大上游压力；对照 `xunlei_adapter.py:273-294` 是正确的双检 |
| L6 | `notifications/legacy_notify.py:20` _send_lock | 全局推送配置 + 渠道发送 | 进程 | **锁内起线程并 t.join() 无超时**（`:742-795`），任渠道挂死 = 全进程通知停摆；同时读改写全局 push_config（`:743-750`） |
| L7 | `search_plugins.py:33` BoundedSemaphore(8) | 插件并发 | 进程 | 超时不杀线程（`:233`），permit 直到插件真正返回才释放 → 挂死即永久漏 1/8 容量 |
| L8 | `deps.py:27` 权限缓存锁 | 权限缓存 dict | 进程 | TTL 300s + LRU 上限已有（`:85-89`），但 invalidate 只在同进程生效 |
| L9 | `drama_lead_in.py:60, 1389-1397` | 引流幂等锁 | 进程 | **key 维度无界**，锁对象永不删除，条目随（网盘,账号,目录,素材）无限增长 |
| L10 | `proxy_image_cache.py:31` 64 分片锁 | 单 URL 下载 | 进程 | 锁内做网络下载（`:286-291`），分片碰撞导致无关 URL 串行 |
| L11 | `wps_document_search.py:14-24` | LRU(16)+fetch 去重+代际 | 进程 | 设计正确（双缓存 + 代际号），可作样板 |
| L12 | `drive_account_auth_sessions.py:21` | 交互式登录会话 | 进程 | 会话内**持有活动 adapter 对象**（含凭证），过期仅在按 id 读取时清理（`:56-58`），无定期清理 |
| L13 | `share_preview_batch.py:35,40` | 预览/验证缓存 | 进程 | 锁保护读改写，但 `_update_validate_cache_ttl` 在**锁外整体替换对象**（`:55-60`）→ 读者可能拿到旧表 |
| L14 | `resource_search.py:21-22` | 搜索缓存 | 进程 | 有锁，正确；对照 7.3 的反例 |
| L15 | `legacy_notify.py:776` | 结果收集 | 进程 | 局部锁，正常 |
| 其余 | 各适配器实例内 _lock（115/123/UC/天翼/移动/光雅等） | 各自 token/会话 | 每实例 | 抽样 5 个均为「单一锁 + 锁内网络」，与 L5 同型，未逐一确认是否双检 |

**锁序与死锁**：全仓未发现「A 锁内取 B 锁」的交叉获取，但 L2→L4 的同向嵌套意味着一次慢登录会让所有网盘的适配器获取排队——这是**性能死锁**而非逻辑死锁，且它只在并发下暴露。

## 5. 缓存与进程内状态清单

| 状态 | 位置 | TTL/上限 | 失效钩子 | 线程安全 | 多 worker |
|---|---|---|---|---|---|
| 适配器实例 | `adapter_factory.py:62` | 无淘汰、无上限 | clear_cache() 存在（`:170-174`）但**全仓无调用点** | RLock 覆盖 | 每进程各一份 |
| 准入计数 | `task_admission.py:64` | 无 | 上下文退出 | Lock | 失效（4×N 并发） |
| 权限缓存 | `deps.py:26` | TTL 300s + 上限 | invalidate_permission_cache | Lock | 失效（最长 300s 脏读） |
| metrics 计数器 | `core/metrics.py:19` | **无上限、无锁** | 无 | 非线程安全 | 失效 |
| 登录会话 | `drive_account_auth_sessions.py:22` | 600s，仅读时清理 | 删除接口 | Lock | 失效（用户可能打到另一进程） |
| 引流锁 | `drama_lead_in.py:60` | 永不删除 | 无 | 建锁时 guard | 失效 |
| TMDB 池 | `tmdb_cache.py:28` | 固定 4 | — | 池安全 | 每进程 4 |
| 豆瓣匹配缓存 | `services/media_discovery.py:18` | 5000 / 12h | 无 | **无锁**，被 `:19` 的 6 线程并发写 | 每进程 |
| 预览批缓存 | `share_preview_batch.py:31` | 2000 / 300s | cache_clear | 有锁 | 每进程 |
| 验证缓存 | `share_preview_batch.py:39` | 2000 / 300s | 同上 | 有锁但替换在锁外 | 每进程 |
| 搜索缓存 | `resource_search.py:21` | 500 / TTL | 有 | 有锁 | 每进程 |
| WPS 文档 | `wps_document_search.py:14-24` | LRU 16 ×2 | 代际号 | 有锁 | 每进程 |
| Telegram offset | DB 设置表 | — | — | — | 冲突（多进程抢同一 offset） |
| 通知配置 | `legacy_notify.py:743-750` | — | 每次 send 后回滚快照 | 靠 L6 全局锁 | 每进程 |

> `media_discovery.py:18` 的 TTLCache 是全仓唯一**无锁共享缓存**；cachetools 的 TTLCache 修改时可能扩容 dict，并发写有 `RuntimeError: dictionary changed size during iteration` / 条目丢失风险。对照 `resource_search.py:21-22` 的正确写法。

## 6. SQLite 写竞争与重试

### 6.1 现状：4 套不兼容实现

| 位置 | 判定 | 退避 | 覆盖范围 | 问题 |
|---|---|---|---|---|
| `db/session.py:42-43` | is_sqlite_locked_error | 无 | 仅判定函数 | **无人统一调用** |
| `task_scheduler.py:266-273` | 内联 "database is locked" in str(exc) | sleep(0.2*attempt)，3 次 | 仅「执行尚未开始」的追剧任务 | execution_started 之后遇锁直接放弃整轮 → 丢一次追剧 |
| `task_scheduler.py:317-320, 379-383, 434-439` | 私有 _is_sqlite_locked | 同上 | 账号探测 / 签到 | 与上一处重复定义，口径漂移 |
| `telegram_bot_manager.py:19-29` | 异常文本 | base_delay=0.2 指数 | bot 状态写 | 与前三者独立 |
| `tmdb_cache.py:51` | timeout=0.5 + NullPool | 忙即放弃 | TMDB 写 | 用快速失败规避而非重试 |

**HTTP 写路径零重试**：`get_db`（`session.py:46-54`）只做 rollback/close，把 OperationalError 原样抛给 `http_error_handler`（`core/exception_handlers.py:33-38`）→ 500。这解释了为什么「12 线程×30 写 0 失败」的基线在**叠加长任务**后会退化：基线只测短写，长读事务把 WAL 钉住后，写者拿不到 checkpoint 机会，`busy_timeout` 30s 之后照样失败。

### 6.2 长事务清单（谁长持写锁/读快照）

| 位置 | 事务起点 | 终点 | 期间在做什么 |
|---|---|---|---|
| `tasks.py:216-231` | Depends(get_db) | `:222 db.commit()` | 完整网盘转存（分钟级），**读快照钉住 WAL** |
| `task_executor.py:164-174, 224, 245, 337` | 调用方 Session | 调用方 commit | flush 之后到 commit 之间夹网络请求（drama 分支 `:216-229`） |
| `task_executor.py:309-313` | 自动换链每轮 db.commit() | 下一轮 | 已做对：注释「每轮独立落盘，下一轮网络请求不占用写锁」；但 persist_execution=False 时退化为 flush |
| `task_scheduler.py:279-285` | 新 Session | send_runtime 后 | **持有 Session 同步推送通知**（持连接做网络 IO） |
| `tasks_share.py:442-446` | 生成器持有 db | 流结束 | 每条链接的验证网络 IO 都在这一个事务里 |

### 6.3 WAL 与超时叠加（为什么长事务会让写入「挂死」而不是报错）

- WAL 模式下写者追加到 `-wal`，checkpoint 要求「没有活跃读者持有旧快照」。`tasks.py:218-222` 的请求事务持续数分钟 → checkpoint 无法 reset，`data/app.db-wal` 单调增长，直到该事务提交。
- `busy_timeout=30000`（`session.py:34`）与 `pool_timeout=30`（`session.py:21`）是**串联**的：一个请求最坏 30s 等连接 + 30s 等写锁 = 60s，超过 nginx/前端超时，用户侧看到的是「请求挂死/转圈」，日志里只有一条迟到的 500。
- 观测建议：把 `data/app.db-wal` 大小作为一级告警指标（改造前先埋），这是验证 R02 是否真实的唯一低成本手段。

## 7. 幂等 / 去重 / single-flight

| 场景 | 现状 | 缺口 |
|---|---|---|
| 任务并发执行 | `task_admission.py:18-27` 按 task_id 去重 + 总量 4 | 只覆盖 6 个调用点（`tasks.py:218`、`tasks_stream.py:155/255`、`task_scheduler.py:235`、`services/tasks.py:229`、`handlers.py:2345`）；`telegram_bot/actions.py:478` 直接调 run_task 不 reserve，靠上层兜底，新增入口易漏 |
| 预览/验证并发 | 无去重：同一批 URL 被两个用户同时点 = 两倍上游请求 | `share_preview_batch.py:543-798` 无 single-flight；样板是 `wps_document_search.py:23` 的 _fetch_lock |
| 适配器登录 | P2-a 已做 TTL 去重 + 缓存加锁 | 残余：_instance_cache 无淘汰、clear_cache() 无调用点（`adapter_factory.py:62,170-174`）→ 改密码后旧实例常驻 |
| 阿里云 token 刷新 | `aliyun_adapter.py:238-276` 锁内无二次校验 | 线程排队串行刷新（thundering herd） |
| 引流行为 | `drama_lead_in.py:1389-1397` 进程内锁 | 无跨进程；锁对象泄漏 |
| 外部限速 | `base_adapter.py:66-76` 持锁 sleep | 单实例串行；多实例叠加会**超频**上游（每实例独立限速） |
| Telegram 更新 | `telegram_bot_manager.py:110-114` 处理后存 offset | 处理中崩溃 → 重启重复投递；handler 需幂等（部分有、部分无） |

### 7.1 retry_utils 的覆盖矩阵（重试是点状能力，不是平台能力）

| 链路 | 是否重试 | 证据 |
|---|---|---|
| 网盘 HTTP 调用 | 是（调用方主动用 `retry_call`） | `retry_utils.py:170-205`，退避 1→2→4s + 20% 抖动，仅 `is_transient_error` 命中才继续 |
| DB commit/flush | **否** | 第 6 节，HTTP 路径零重试 |
| 通知发送 | 否 | `sender.py:78-81` 直接吞异常 |
| 插件搜索 | 否 | `search_plugins.py:217-235` 只有超时，没有重试 |
| Telegram API | 否（仅状态写有 commit 重试） | `telegram_bot_manager.py:19-29` |
| 调度器追剧（锁冲突） | 部分 | `task_scheduler.py:266-273`，且仅限「尚未开始执行」 |

结论：外部调用的重试覆盖不全不是最大问题；**「业务写入没有重试」才是**——它直接决定 R04 的记录留存率。

## 8. 同步阻塞链路

| 链路 | 位置 | 阻塞时长上界 | 备注 |
|---|---|---|---|
| 单任务转存 | `tasks.py:220` → `task_executor.py:95` | 分钟级（4 轮换链 × 各请求 30s） | 请求线程直跑 |
| 预览批验证 | `share_preview_batch.py:796` 循环尾部 sleep(uniform(0.06,0.22)) | 20 条 ≈ 4.4s 纯 sleep + 每条网络 | 逐 URL 串行，且 sleep 在**服务端** |
| 搜索插件 | `search_plugins.py:233` finished.wait(timeout) | ≤30s | 从请求线程同步等待 |
| 图片代理 | `media_discovery.py:128-169` | 未命中 = DNS+下载 | 无鉴权、无并发上限 |
| 通知 | `legacy_notify.py:795` join() | 渠道超时（client.py sendMessage 30s / answerCallback 20s） | 全局串行 |
| Telegram 搜索 | `handlers.py:1109-1155` | 插件超时 | 在**轮询线程内**执行，期间「取消」按钮无法响应 |
| 调度器追剧 | `task_scheduler.py:248-254` | 全部任务之和 | 单 job 串行 |

### 8.1 一次 /run 请求的完整链路（每一跳都可能排队）

```
浏览器 → nginx(5115) → uvicorn(单进程, Dockerfile:53-55)
  → TimingMiddleware          # timing.py:23 计时, :26 生成 metrics 键
  → post_run_task (同步 def)   # tasks.py:217 占 1 个 anyio token
  → task_admission.reserve     # task_admission.py:18 进程内计数, 上限 4
  → Depends(get_db) 取连接     # session.py:46, 池 15, 排满等 30s
  → TaskExecutor.run_task      # task_executor.py:95 同步 requests
     ├─ adapter._throttle_request   # base_adapter.py:66 锁内 sleep 50–100ms
     ├─ 自动换链 ≤4 轮              # task_executor.py:278-280 每轮 sleep 2s
     └─ db.flush()×N → db.commit()  # 写锁, 无重试; 每轮 commit(:309-313)
  → audit.write_audit_log      # tasks.py:221 与执行记录同事务
  → send_runtime               # tasks.py:228 → 全局锁 + join (legacy_notify.py:742-795)
```

每一跳都有独立的等待上界：连接 30s、写锁 30s、限速 0.1s×请求数、换链 2s×3、通知 30s×渠道数。最坏情况相加已远超一次 HTTP 请求的合理时延，这也是为什么「偶发超时」难以复现——它需要多个等待同时叠加。

## 9. 异常吞噬与丢记录（按「静默程度」排序）

| # | 位置 | 吞掉什么 | 后果 |
|---|---|---|---|
| E1 | `tasks_stream.py:195-220` | SSE 任务失败 → 伪造 id=0 执行记录 | 前端看到「失败」但 DB 无记录，事后无法复盘 |
| E2 | `task_executor.py:373-388` | 只有走到函数末尾才 add(TaskExecution) | 进程被杀 / 未捕获异常 = 完全无记录；无 running 态 |
| E3 | `exception_handlers.py:33-38` | 统一 500，只 logger.error | 与前两条叠加：用户报「跑了但没记录」时无法定位 |
| E4 | `notifications/sender.py:78-81` | send_runtime 把所有异常吞成 [] | 「未配置渠道」与「推送失败」不可区分，成功率指标失真 |
| E5 | `task_scheduler.py:449-459` | 审计清理失败只 db.rollback()，**无日志** | 磁盘只增不减，无人知晓 |
| E6 | `task_executor.py:337-350` | 异常 logger.exception 后 except Exception: pass | 有日志但无结构化状态，执行记录写 failed 还是丢失取决于位置 |
| E7 | `task_scheduler.py:266-273` | execution_started 后遇锁直接放弃本轮 | 追剧静默漏跑，日志只有一行 warning |
| E8 | `share_preview_batch.py:50-51` | 读 TTL 配置失败回落 300 | 低危 |
| E9 | `adapter_factory.py:149-168` | 构造异常返回 None | 上层需逐处判空，否则 AttributeError 变 500 而非「账号不可用」 |

## 10. 多 worker 扩展阻塞点

| 阻塞点 | 位置 | 多 worker 后果 |
|---|---|---|
| 启动迁移 | `main.py:47-58, 68` | N 个进程同时 alembic upgrade head → 竞争/半迁移 |
| 调度器 | `main.py:85-87` + `task_scheduler.py:38-45` | N 份追剧调度，同一任务重复执行（admission 仅进程内，无法互斥） |
| Telegram bot | `main.py:85-87` + `telegram_bot_manager.py:98-114` | N 个进程抢同一 offset，重复处理更新 |
| 权限缓存失效 | `deps.py:44-49` | 只在当前进程失效，其他进程最长 300s 脏读 |
| 准入/登录会话/引流锁 | `task_admission.py:64`、`drive_account_auth_sessions.py:22`、`drama_lead_in.py:60` | 并发上限与幂等全部失效 |
| 适配器实例 | `adapter_factory.py:62` | 每进程各登录一遍网盘（上游风控风险） |
| DB 路径 | `core/settings.py:106-112` 硬编码相对路径 | 多容器/多 CWD 时指向不同文件，或同在 NFS 上 SQLite 直接损坏 |

结论：**本期明确单进程**；这些不是「bug 要修」，而是必须在文档与启动日志中显式声明的架构约束。

### 10.1 若未来要多 worker：最小改造清单（本期不做，仅预留）

1. 迁移：`main.py:47-58` 的 `command.upgrade` 加进程间互斥（SQLite 租约表或文件锁），或改为独立 init job。
2. 调度器：`task_scheduler.py:38-45` 改为「DB 租约 + 条件更新」抢主，只有拿到租约的进程注册 job。
3. Telegram：`telegram_bot_manager.py:110-114` 的 offset 保存改为 DB 条件更新（`WHERE last_update_id < ?`）。
4. 缓存失效：`deps.py:44-49` 改为「DB 版本号 + 短 TTL 轮询」。
5. 准入/引流锁：`task_admission.py:18-27` 改为 DB 行级租约（`UPDATE ... WHERE lease_until < now`）。

这与第 12 节的取舍一致：**不引入 Redis，也不假装单进程状态能跨进程**。

## 11. 风险总表（风险 → 证据 → 触发条件 → 影响 → 处理策略）

| ID | 风险 | 证据 | 触发条件 | 影响 | 处理策略（现有依赖） |
|---|---|---|---|---|---|
| R01 | 长任务端点耗尽 anyio 线程池 | `tasks.py:216-231` | ≥10 个并发「立即运行」 | 全站同步端点排队，SSE/普通查询也变慢 | 端点改为「admission.reserve → 起后台线程 → 立即返回 execution_id」；任务线程内自建 Session |
| R02 | 请求事务覆盖长任务，钉住 WAL | `tasks.py:218-222` | 同上 | db-wal 单调膨胀；checkpoint 饥饿；写者 busy_timeout 后失败 | 任务执行改用 SessionLocal() 短事务：开始 insert running，结束 update |
| R03 | 写路径零重试 | `session.py:46-54`、`exception_handlers.py:33-38` | 长任务并发 + 同时写 | 500 且用户侧无补偿 | 统一 retry_sqlite_locked 装饰器（基于 `session.py:42` 判定），覆盖 commit/flush；指数退避 + 抖动，上限 5 次 |
| R04 | 执行记录只在末尾落库 | `task_executor.py:373-388` | 进程重启/异常/OOM | 无记录，无法审计追责 | 两段式：进入即 insert status=running 并 commit，结束 update |
| R05 | SSE 失败伪造 id=0 | `tasks_stream.py:195-220` | 流内任务异常 | 前端有结果、DB 无记录 | 与 R04 同批：先落 running 再执行，失败走同一 update |
| R06 | 全局通知锁内 join 无超时 | `legacy_notify.py:742-795` | 某渠道网络挂起 | 全进程通知停摆，任务完成回调被拖住 | queue.Queue + 单消费者线程；join(timeout=) 兜底，超时记失败 |
| R07 | 通知失败被吞成空列表 | `sender.py:78-81` | 渠道异常/配置错误 | 成功率指标失真 | 返回结构化结果并写日志/metrics；吞异常改为降级 + 记录 |
| R08 | 调度器串行 + coalesce 漏跑追剧 | `task_scheduler.py:101-204, 207-285` | 单轮任务总时长 > cron 间隔且错过 > 60s | 追剧静默漏跑 | 按任务拆 job；漏跑写 skipped 执行记录 |
| R09 | 适配器工厂全局 RLock 内含登录 | `adapter_factory.py:65,149-168` | 某网盘登录慢/超时 | 所有网盘适配器获取排队（性能死锁） | 锁只保护缓存读写；构造移到锁外（双检 + 占位标记），失败移除占位 |
| R10 | 实例缓存无淘汰、clear_cache 无调用点 | `adapter_factory.py:62,170-174` | 长期运行 + 改配置/改密码 | 旧实例常驻、凭证不刷新 | 按 key 精确失效，在账号更新/删除处调用 |
| R11 | 限速锁内 sleep | `base_adapter.py:66-76` | 同 adapter 多线程请求 | 每请求额外 50–100ms，锁竞争放大 | 「计算 sleep → 锁外 sleep → 重取时间戳」令牌桶；锁内只做 CAS |
| R12 | 阿里云 token 刷新无锁内双检 | `aliyun_adapter.py:238-276` | token 过期瞬间并发 | N 次刷新打上游，触发风控 | 抄 `xunlei_adapter.py:273-294` 的 pre-check + in-lock 双检 |
| R13 | 搜索 permit 泄漏 | `search_plugins.py:217-235` | 插件超时但线程未返回 | 容量永久减少，最终「并发已满」 | 固定 8 线程池 + 队列，future.result(timeout) 丢弃结果但不漏 permit |
| R14 | 无统一线程上限 | 第 2.1 节清单 | 多用户并发预览 + 搜索 + 任务 | 线程爆炸，内存与连接双重压力 | 唯一 ThreadPoolExecutor 网关（io_heavy 12 / cpu_light 4）；Thread(...).start() 收敛到 4 个已知位置 |
| R15 | 预览/验证无 single-flight | `share_preview_batch.py:543-798` | 两用户同时预览同批 URL | 上游请求翻倍、配额浪费 | URL 哈希 in-flight dict（key→Event），样板 `wps_document_search.py:23` |
| R16 | 预览服务端 sleep | `share_preview_batch.py:796` | 每次批量预览 | 20 条白等 4.4s，占请求线程 | 自适应节流或移入任务线程 |
| R17 | SSE 验证流持 Session | `tasks_share.py:435-448` | 多条链接流式验证 | 连接长期不归还 + 跨线程复用 Session | 生成器内每批自建 SessionLocal()，或 async 生成器 + run_in_threadpool |
| R18 | 图片代理同步 + 无鉴权 | `media_discovery.py:128-169` | 海报墙/爬虫 | 线程被图片下载占满；被外部刷 | P3 鉴权依赖 + 未命中转后台预热；加并发上限 |
| R19 | TTLCache 无锁并发 | `services/media_discovery.py:18-19` | 6 线程并发写同 key 集合 | 条目丢失/迭代异常 | 加 threading.Lock（照 `resource_search.py:21-22`） |
| R20 | metrics 键无界 + 无锁 | `middlewares/timing.py:26`、`core/metrics.py:19` | 路径含 id（/api/tasks/123/run） | 内存随请求数增长；并发写丢更新 | 键改用路由模板（scope["route"].path）+ 上限淘汰 + 加锁 |
| R21 | 审计清理静默失败 | `task_scheduler.py:449-459` | DB 忙/表锁 | 磁盘只增不减 | 补 logger.warning + 失败计数进 metrics；清理走独立 NullPool engine |
| R22 | 单进程假设未声明 | `Dockerfile:53-55`、`main.py:47-58,85-87` | 有人加 --workers | 重复迁移/调度/bot，缓存失效 | settings 增加并发断言（>1 启动失败并提示）；启动日志打印「单进程模式」 |

## 12. 依赖约束下的取舍

**被排除的方案（不引依赖 / 不改技术栈）**

| 需求 | 常见解法 | 本项目取舍 | 替代实现 |
|---|---|---|---|
| 跨进程锁 | Redis SET NX / 文件锁 | 排除 | 单进程 threading.Lock；文档显式声明限制；未来若必须多进程，用 SQLite 租约表 + 条件 UPDATE（无新依赖） |
| 分布式缓存 | Redis | 排除 | cachetools + threading.Lock；失效靠 DB 版本号轮询（复用 settings 表） |
| 任务队列 | Celery/RQ/Dramatiq | 排除 | queue.Queue + 单消费者线程（标准库），持久化用现有 TaskExecution 表 |
| 结构化日志 | structlog | 排除 | 现有 logging dictConfig（`core/logging.py:29-72`）加 JSON formatter |
| DI 容器 | dependency-injector | 排除 | 现有 Depends + 模块级单例，补清单文档 |
| 异步化网盘 IO | httpx/asyncio 全量重写 | 排除（风险过高） | 保持同步，把长链路移出请求线程（后台线程 + 状态轮询/SSE） |
| 多 worker | gunicorn -w N | 本期排除 | 单进程 + 明确容量模型；扩容优先调后台线程池与连接池 |
| 换库 | PostgreSQL | 排除 | SQLite WAL + 统一重试 + 短事务 + 写者隔离池 |

**代价**：①单实例吞吐受限于单进程；②所有幂等/限流都在进程内，重启即重置；③无法水平扩展。因此方案必须把「单进程」写入架构约束，并用 R01/R03/R04 的改造把单进程的可用性做扎实。

## 13. 分批改造顺序与验收

**P0（1–2 天，低风险高收益）**
1. 统一 SQLite 锁重试：新增 db/retry.py（基于 `session.py:42` 判定），覆盖 db.commit() 的所有调用点（至少 `tasks.py:222`、`task_scheduler.py:262`、audit 调用方）。验收：注入 OperationalError 的测试下 5 次内成功。
2. 执行记录两段式（R04/R05）：任务开始 insert running，结束 update。验收：kill -9 后 DB 中存在 running 记录（有 started_at）。
3. 长任务端点后台化（R01/R02）：post_run_task 改「reserve → 起线程 → 返回 execution_id」。验收：10 并发 /run 下 /api/health P95 不劣化。

**P1（3–5 天）**
4. 通知队列化（R06/R07）：queue.Queue + 单消费者；join(timeout)；结果结构化。验收：注入挂起渠道，其他通知不被阻塞。
5. 适配器工厂锁瘦身（R09/R10/R12）与限速锁外 sleep（R11）。验收：并发获取 10 个不同网盘适配器，最慢登录不阻塞其他。
6. 线程网关（R14）+ 预览 single-flight（R15/R16）。验收：并发 20 预览 + 8 搜索，进程线程数有界。

**P2（1 周内）**
7. 调度器按任务拆 job（R08）+ 漏跑记录。
8. SSE 验证流解耦（R17）、图片代理并发/鉴权（R18）、metrics 键降基数（R20）、TTLCache 加锁（R19）、审计清理可观测（R21）。
9. 单进程断言与文档（R22）。

**观测指标（改造前后各测一次）**
- /api/health P50/P95，在「4 个长任务 + 40 并发短请求」下；
- data/app.db-wal 文件大小随时间曲线（验证 R02）；
- pool_timeout 命中次数（SQLAlchemy pool 事件）；
- 任务 running 态存活时长分布 vs 记录缺口数（验证 R04）；
- 通知投递成功率（区分「未配置」与「失败」）。

### 13.1 验收清单（对齐改造项）

| 改造项 | 观测方式 | 通过标准 |
|---|---|---|
| R03 统一锁重试 | 人为并发写 + 注入 `OperationalError` | 5 次内成功，最终失败前有 5 条退避日志 |
| R04 两段式执行记录 | 任务中途 `kill -9` | 重启后 DB 存在 `status=running` 且 `started_at` 非空 |
| R01/R02 后台化 | 4 长任务 + 40 短请求压测 | `/api/health` P95 ≤ 基线 1.2 倍；`db-wal` 不再单调增长 |
| R06 通知队列 | 注入一个挂起渠道 | 其他渠道投递延迟 < 1s，挂起渠道超时记失败 |
| R09 工厂锁瘦身 | 10 个不同网盘并发取适配器 | 最慢登录不阻塞其它账号（P95 差异 < 2s） |
| R14 线程网关 | 20 预览 + 8 搜索并发 | 进程线程数封顶（gate 容量 + 固定线程） |
| R22 单进程声明 | 设 `WEB_CONCURRENCY=2` 启动 | 启动即失败并打印明确原因 |

## 14. 待补测量（本审计的边界）

1. 未测真实网盘延迟：任务时长上界只能按适配器 timeout（普遍 30s）与换链轮次（`task_executor.py:28-29`：4 轮）估算，实际需线上样本。
2. 未压测连接池排队：15 vs 60 线程的实际排队时长需在基线环境补「4 长任务 + 40 短请求」观测。
3. 适配器仅抽样 5 个（quark/aliyun/xunlei/baidu/cloud189）确认锁与重试形态，其余 5 个未逐一核对，可能更差。
4. 未核实 audit.write_audit_log 是否使用独立 Session（若独立，R03 影响面比估计略小）。
