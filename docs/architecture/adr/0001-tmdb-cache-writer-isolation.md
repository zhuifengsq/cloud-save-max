# ADR 0001：TMDB 缓存写路径不搬进 infrastructure（显式例外）

- 状态：已接受（Accepted，2026-11-09）
- 决策者：架构组
- 相关：方案 `docs/architecture-evolution-plan.md` §10.3 决策 5、`scripts/check_layering.py` 的 documented exceptions
- 影响文件：`backend/app/application/tmdb_cache.py`

## 背景

S3/S4 的分层规则是「application 不得 import persistence」。绝大多数模块已按此搬迁（22 个仓储 + container 函数访问器），
但 `tmdb_cache` 的写路径有三处特殊约束，硬搬会破坏现网稳定性：

1. **写连接必须与读连接分离**。请求线程在等 TMDB 外部 HTTP 期间会占着连接池的读连接（池只有 5+10=15 个）。
   若写操作也走这个池，写者会和读者抢同一批连接，直接把「缓存过期刷新」变成「接口超时」。
   现状用独立写出引擎：`create_engine(engine.url, poolclass=NullPool, connect_args={"timeout": 0.5})`
   ——`backend/app/application/tmdb_cache.py:43-63`，并用 `WeakKeyDictionary[Engine, Engine]` 按主引擎复用。
2. **并发写必须限流**。`_write_slots = BoundedSemaphore(2)`（`:36`），拿不到槽 1 秒就抛 `PoolTimeout`，
   由调用方按「缓存写忙」降级，而不是排队堆积（`:57-58`）。
3. **写路径依赖 SQLite 方言的 upsert 语义**：`from sqlalchemy.dialects.sqlite import insert`（`:12`）+
   `UPDATE ... WHERE` 领刷新标记（`_lock_timeout = timedelta(minutes=10)`，`:31`），不是 ORM 对象的 CRUD。

## 决策

`app/application/tmdb_cache.py` **保留在 application 层**，对 persistence 的 4 条 import
（`session` / `models.task` / `models.tmdb_media_cache` / `models.tmdb_setting`）
以「documented exception」形式登记在分层门禁里，**不计入违规预算**。

登记内容（门禁输出可见）：

~~~text
app.application.tmdb_cache -> app.infrastructure.persistence.session              line 19
app.application.tmdb_cache -> app.infrastructure.persistence.models.task          line 20
app.application.tmdb_cache -> app.infrastructure.persistence.models.tmdb_media_cache line 21
app.application.tmdb_cache -> app.infrastructure.persistence.models.tmdb_setting  line 22
~~~

## 后果

- 门禁仍然 `violations=0`：例外是「白名单 + 逐条可见」，不是放宽 `ALLOWED` 规则；新增任何一条 tmdb→persistence 边都会
  让门禁变红。
- `application/tmdb_cache.py` 的依赖关系不能靠读 import 块一眼判断，必须在 ADR 与方案 §10.3 里查。
- 若未来把它整体迁到 `infrastructure/persistence/tmdb_cache.py`，需要同时改造 **7 处生产 import 方**的 Session 注入方式：
  `api/routes/tmdb_cache.py:31`、`application/media_discovery.py:11`、`application/tmdb_cache_scheduler.py:7`、
  `application/drama_share_autoupdate.py:13`、`infrastructure/scheduling/task_scheduler.py:20`、
  `infrastructure/execution/task_executor.py:149`、`application/container.py:96`（装配点），外加 3 个测试模块。
  属于独立工程，收益是「目录好看」，不是用户可见收益 —— 因此不做。

## 备选方案（已否决）

| 方案 | 否决原因 |
| --- | --- |
| 整体搬到 `infrastructure/persistence/` | 需要把 `Session`/`Engine` 全部改为显式注入并重排 6 处调用方；期间「读事务卡写」的老问题会重现，风险 > 收益 |
| 改为纯 ORM 实现（去掉 Core upsert） | 会把「领标记」变成读-改-写两步，失去原子的 UPDATE 条件更新，必然出现重复刷新 |
| 直接放宽门禁的 `ALLOWED`（application→persistence） | 会让 60 条历史违规一次性「合法化」，等于取消分层规则 |

## 重新评估的触发条件

1. 数据库从 SQLite 换成 Postgres（写者隔离与 `timeout=0.5` 的前提变了）；
2. 出现独立的刷新进程/线程池（届时写路径应归 infrastructure 的后台作业，而不是请求侧缓存）；
3. 需要跨进程协调刷新（单进程假设被打破，见 ADR 0003）。
