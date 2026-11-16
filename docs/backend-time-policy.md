# 后端时间语义盘点与迁移方案

日期：2026-11-16。工作包 G，交付性质为源码盘点、固定时钟行为验证和设计；没有改变生产时间读写，没有修改历史数据。本轮未读取运行数据库。下表描述的是可从写入路径证明的来源，不推断历史每一行的实际来源。

## 当前字段与时间来源

| 字段/数据 | 当前来源与存储 | 比较/输出/展示 |
| --- | --- | --- |
| JWT `iat`、`exp` | `core/security.py::create_access_token` 的本地 naive `datetime.now()`，加配置秒数后 `.timestamp()`，存为 Unix 整数秒 | jose 按绝对时间验证；不入库，前端拿 `expires_in`。本地时间按本地规则转 timestamp，不是直接把本地墙上时间当UTC |
| `refresh_tokens.expires_at` | `application/auth.py::issue_tokens` 的 `_utcnow()` 实际调用本地 `datetime.now()`，加 refresh 时长后写 `DateTime(timezone=True)` | SQLite往返得到无offset的naive；refresh先 `_normalize_dt` 再与本地naive now比较；不向前端输出时间，只发HttpOnly cookie及相对max_age |
| `refresh_tokens.revoked_at`、`last_used_at` | auth刷新/撤销时的本地naive | revoked非空即失效；last_used用于记录。aware输入会被转换为宿主本地naive；naive保持原样 |
| `refresh_tokens.created_at`、`users.created_at/updated_at` | 模型 `func.now()` / onupdate，初始迁移 `CURRENT_TIMESTAMP` | SQLite服务器函数给UTC墙上时间，无显式offset；SQLite取回naive，不因ORM声明timezone=True自动恢复时区 |
| `task_executions.started_at/created_at` | 模型server_default `func.now()` | 任务历史/列表DTO输出datetime，默认值走SQLite UTC；写入时有显式值需按对应调用路径判定 |
| `task_executions.finished_at` | 执行器 `task_executor.py` 多阶段 `datetime.now()` | 与默认started_at可能不同来源；`repositories/task_executions.py` 清理cutoff用 `datetime.utcnow()`，不可据字段类型认定同一行时间一致 |
| `drive_accounts.last_checked_at` | `application/drive_accounts.py::probe_drive_account` 本地naive `datetime.now()` | `normalize_api_datetime`将naive一律附UTC再转Asia/Shanghai，存在本地写入与UTC解释不一致；`profile_updated_at`输出复用last_checked_at，并非另一存储字段 |
| `drive_accounts.created_at/updated_at` | SQLite `func.now()` UTC墙上时间 | 同一normalizer按UTC解释再输出Asia/Shanghai，这一路与默认来源相符，不能与last_checked_at一起机械改偏移 |
| `share_preview_batch_cache.checked_at/expires_at` | `application/share_preview_batch_cache.py` 经ClockPort.now()取得本地naive，加TTL；checked_at模型也有UTC server_default兜底 | 应用按当前now比较expiry；缓存默认及显式路径可能混源，属于可重建缓存，迁移时优先清空重算而不是猜来源 |
| `tmdb_media_cache.fetched_at/expires_at/last_accessed_at/refresh_started_at`、`credits_fetched_at/credits_expires_at/credits_refresh_started_at` | `application/tmdb_cache.py::_utcnow`实际本地now；SQLite DateTime | 应用过期比较以同类now；`TMDBCacheManageView.vue`对无offset字符串补Z后按浏览器时区显示，存在来源与展示解释风险 |
| `telegram_bot_states.last_polled_at/last_processed_at` | `application/telegram_bot/session_store.py`本地naive | ORM保存；监控/状态DTO消费，不能直接当UTC。`telegram_bot_sessions.created_at/updated_at`则走DB默认 |
| `audit_logs.created_at` | SQLite `func.now()` | 审计展示通用日期格式；清理策略的业务时区与绝对cutoff必须分开，以后统一UTC比较 |
| 其他公共 `created_at/updated_at` | `audit_log_scheduler_settings`、`drive_account_probe_scheduler_settings`、`invalid_share_links`、`magic_regex_rules`、`notification_settings`、`resource_search_sources`、`system_settings`、`tasks`、`task_lead_ins`、`task_scheduler_settings`、`task_templates`、`tmdb_cache_scheduler_settings`、`tmdb_settings`的模型均为func.now/onupdate | DB默认是UTC墙上时间；显式导入、旧版本写入、直接SQL或恢复可能覆盖默认，历史迁移仍要按来源证据分类 |
| 运行星期 `extra.runweek`、TMDB播出日期、用户日历日期 | `application/drama_schedule.py::resolve_run_schedule`默认本地now或调用方显式now，按isoweekday；播出日是业务日期 | 手动执行保留绕过星期限制的口径。`utils/dramaSchedule.ts`、`DramaCalendarView.vue`大量使用浏览器本地日期构造；不能将播出日期换成UTC日期再算星期 |
| 定时设置 `timezone` | 各scheduler配置显式字符串，默认Asia/Shanghai；前端 `utils/cron.ts`也默认此值 | cron需按该zone计算；Dashboard明确用Asia/Shanghai，通用`utils/datetime.ts`则用浏览器本地时区，展示策略目前并不完全统一 |
| 进程TTL/锁/准入/失败冷却 | `SystemClock.monotonic()` / `time.monotonic()` | 只算持续时长，不序列化为绝对日期；重启不延续，保持现有单进程约束 |

来源证据：`backend/app/core/security.py`、`application/auth.py`、`application/drama_schedule.py`、`core/runtime/clock.py`、`infrastructure/persistence/models/*.py`、`alembic/versions/112649d93a0e_init.py`及后续CURRENT_TIMESTAMP迁移；前端相关位置已逐项注明。`SystemClock.now_utc()`当前返回naive UTC，不能把它的名字等同于aware UTC。

Python明确区分aware与naive，naive本身不携带UTC或本地语义；SQLite也没有专用datetime类型。[Python datetime说明](https://docs.python.org/3/library/datetime.html#aware-and-naive-objects)、[SQLite时间函数](https://www.sqlite.org/lang_datefunc.html)。

## 固定时钟证据及尚未修复的风险

`regression_tests/test_auth_time_policy.py`直接调用现有token创建和refresh认证动作：模拟UTC−5、UTC、UTC+8的同一绝对时刻，分别验证JWT的iat对应同一个Unix时刻、exp−iat等于配置7200秒；aware refresh expiry在过期前1秒可用、恰好过期及过期后1秒均拒绝。模拟通过注入局部datetime及时钟实现，不改变宿主TZ、不依赖当前日期、不输出生成token。

另用独立SQLite文件真实往返证明：输入2030-01-01T20:30:00+08:00，读回2030-01-01 20:30:00且offset丢失。现有 `_normalize_dt`无法知道它的原始时区。如果颁发时宿主UTC+8、恢复后宿主UTC，则同一个旧naive值可能晚8小时失效；反向变化可能提前失效。该测试刻画旧语义，并未宣称UTC迁移已经完成。固定offset测试不覆盖夏令时跨界，跨DST的naive加法也必须在未来认证改造中增加专门用例。

当前结论：未证实“JWT固定偏移8小时”；已确认SQLite令牌时间丢失offset和若干明确写入/输出解释不一致的风险。当前测试13 passed，不以扫描datetime.now出现次数替代行为证据。

## 目标契约与实施顺序

先治理认证时间，涉及 `refresh_tokens.expires_at/revoked_at/last_used_at/created_at` 与JWT iat/exp创建；执行历史作为第二批。认证期望是UTC aware应用时钟，Unix秒创建JWT，显式UTC编码持久化边界。SQLite方案选UTC Unix整数微秒列并带时间语义版本，避免仅将ORM类型改为timezone=True后仍丢offset；具体schema与Alembic变更需单独评审。API绝对时刻输出带Z或显式offset，前端转换为用户明确选择的展示时区。DB默认和应用显式写必须走同一契约。

运行星期、cron、播出日期和用户日历沿用明确业务时区；默认Asia/Shanghai须写在配置/DTO约定，不跟宿主或浏览器设置隐式漂移。日期字符串继续表达业务日期，禁止把2026-11-16直接当UTC零点转换后改变日期。新增时间代码应区分instant/date/duration，复用ClockPort便于注入；业务函数接明确now参数，不通过全局改TZ测试。现有代码尚未批量执行此目标。

认证改造验收需覆盖：UTC与不同本地zone的相同epoch、秒/微秒精度、恰好过期、撤销、刷新轮换、事务失败回滚、进程重启/跨时区恢复，以及DST跳跃/重复小时。不能通过增加8小时宽限修复不确定来源令牌。

## 历史迁移及恢复设计（未执行）

1. 建立只读盘点清单：表、字段、记录区间、写入程序版本、可证实时区、备份来源、解析成功/未知数量。已知SQLite默认值按UTC转换；已知本地应用写入按当时部署zone转换；未知值标为unknown，不根据“当前容器Asia/Shanghai”猜所有旧值。
2. 先停止认证写入或进入维护窗口，以SQLite backup API做一致性快照并运行integrity_check；记录schema revision、程序版本、时区说明和校验摘要，复制快照到隔离目录完成实际恢复验证。备份失败即阻断本次数据迁移，不沿用现有启动热备“失败仍继续”策略。
3. 认证历史优先不猜测：无法证实来源的refresh token全部撤销/要求重新登录；已知来源可转换，保留撤销与过期语义。用户created_at无法确认来源则保留旧列和unknown标记，不把其认作认证有效期依据。
4. 采用新列/迁移账本，记录row id、字段、源语义版本、目标版本、原值摘要、转换结果及batch id；每批短事务，条件更新源值未变且版本仍旧。重跑跳过已迁移记录，冲突报告后停批，不反复加减时差；不在日志保存原始令牌/hash或凭据。
5. 双读按版本解释，禁止用是否有tzinfo猜旧值；切换到新格式单写。转换前做dry-run计数与抽样预期（不展示秘密），转换后检查单调关系、到期边界、用户数与记录数；新增接口响应同时更新schema、前端消费与golden。
6. 演练断电中断、迁移重复执行、未知来源、混合版本、跨时区恢复、WAL备份、坏时间、DST；恢复后验证旧程序与旧schema配套、新程序与新schema配套，不能仅降级代码读取新列。
7. 回滚以停止写入、恢复同一检查点的库与程序为边界；迁移后新增写入需先保全，不能无说明覆盖。凭据外部刷新及已发生的网盘写入不可通过时间迁移重放。认证旧token可失效重登，任务历史不得据猜测批量改写。

该方案尚未获具体迁移实现批准；没有执行ALTER/UPDATE历史时间，也未声称线上恢复演练完成。
