# 数据与可观测性专题审计（cloud-save-max）

> 审计对象：`cloud-save-max`（Python 3.13 + FastAPI 0.115 + SQLAlchemy 2.0 sync + SQLite WAL + Alembic + APScheduler + Vue3）
> 审计角色：data-platform-auditor（只读审计，未修改任何源码/测试/配置）
> 基线：HEAD `3d3e004`，测试 404 passed / 8.45s（`backend`，XXM_TESTING=1，忽略 2 个 weibo 文件），实库 `backend/data/app.db` 5,013,504 B / 37 表
> 与既有报告的关系：不重复 `docs/backend-optimization-baseline.md` 的 P0~P5（eager loading、SSE 异步化、连接池参数、gzip、适配器 init 锁、metrics 鉴权、权限缓存、tasks 路由拆分），本文只写**仍未解决的结构性问题**。
> 每条结论均带 `file:line`；行号以当前 HEAD 为准。

---

## 0. 结论速览（按风险排序）

| # | 结论 | 风险 | 关键证据 |
|---|------|------|----------|
| C1 | 启动即自动 `alembic upgrade head`，无锁、无备份、无预检、无 try/except；失败直接拖垮启动或留下半迁移库 | P0 | `backend/app/main.py:47-58`、:68 |
| C2 | `database_url` 仅在 XXM_TESTING=1 时读环境变量，否则硬编码 `sqlite:///./data/app.db`，迁移可打到真实库（已发生一次） | P0 | `backend/app/core/settings.py:106-112`；`docs/backend-optimization-round2-report.md:43` |
| C3 | SQLite 从未开启 `PRAGMA foreign_keys`，ORM 声明的 `ondelete=CASCADE` 全部失效（实测 pragma=0） | P0 | `backend/app/db/session.py:30-36`；`backend/app/models/task_execution.py:15` |
| C4 | schema 三真相分裂：ORM 26 表 vs 实库 37 表（10 张无模型遗留表），迁移里有 7 处静默跳过守卫，测试用 `create_all`，无漂移检测 | P0 | `backend/alembic/versions/20260625_sync_tasks.py:25`、`20261106_drama_lead_in.py:20-48` |
| C5 | JSON 即 Text，全仓 145 处裸 `json.loads/dumps`/40 文件；序列化层 4 处无 try/except，一条脏数据就让任务列表 500 | P1 | `backend/app/services/task_serialization.py:31,56,86-87` |
| C6 | `run_log` 无上限累积后整段返回；列表接口无分页、每任务只回 3 条执行记录（硬编码） | P1 | `backend/app/extensions/runtime/execution_log.py:40-41`；`task_serialization.py:88` |
| C7 | 可观测性只覆盖 HTTP 请求线程：后台任务/调度器/DB 池/锁等待零指标；APScheduler 无 listener；5xx 日志无 request id | P1 | `backend/app/core/metrics.py:19`；`backend/app/extensions/runtime/task_scheduler.py:449-459` |
| C8 | 测试 404 passed 但 0 个 HTTP 层测试、0 个迁移测试；`backend/tests` 未入 git，CI 只跑 `regression_tests` | P1 | `.gitignore:18`；`.github/workflows/docker-publish.yml:30` |
| C9 | 备份/回滚体系缺失：无脚本、README 无 RPO/RTO、唯一 .bak 是手工快照；secrets.json 每次启动重写 | P0 | `README.md:113-137`；`backend/app/core/settings.py:114-142` |
| C10 | 数据只增不减：`auto_vacuum=0` 且全仓无 `VACUUM`，freelist 已占 19%；219 条 refresh_tokens 中 203 条已撤销且无清理 | P2 | `backend/app/db/session.py:30-36`；实测 `refresh_tokens(219/203 revoked)` |
| C11 | `/health` 未鉴权、DB 挂仍返回 200，每次请求起 2 个 `git` 子进程；Dockerfile 无 HEALTHCHECK/VOLUME | P2 | `backend/app/api/routes/health.py:13-40,46-59`；`Dockerfile:76-77` |
| C12 | 前端契约建立在自由 JSON 与裁剪列表上，重构时最易无声破坏（详见 §6） | P1 | `frontend/src/types/tasks.ts:3-14,59-93` |

---

## 1. 范围与方法

- 读过的面：`backend/app/models/**`（24 个模型类全部）、`backend/alembic/**`（48 个迁移 + env.py）、`backend/app/core/{metrics,instrumentation,logging,errors,exception_handlers}.py`、`backend/app/db/session.py`、`backend/app/api/routes/{tasks,tasks_stream,metrics,health,setup}.py`、`backend/app/extensions/runtime/**`、`backend/tests`、`backend/regression_tests`、`Dockerfile/default/README.md`、`frontend/src/api/**` 与 `frontend/src/types/**`、`.github/workflows/**`。
- 只读手段：`read/grep` + `pwsh` 只读查询（sqlite3 `mode=ro` URI、`PRAGMA`、`git ls-files`、`wc`）；测试基线跑一次全量 `pytest tests regression_tests`（结果见上）。
- 未做：未在克隆库上执行 `alembic upgrade`（会写库，越权）、未修改任何文件、未触发任何调度任务。

---

## 2. 领域模型边界与 JSON 长字段

### 2.1 模型 / 表盘点（实库实测）

实库 37 张表（含 `alembic_version`）：ORM 覆盖 26 张（24 个模型类 +`user_roles`/`role_permissions` 两张关联表），**10 张表无任何 ORM 模型**：

| 表 | 行数 | 状态 |
|----|------|------|
| `plugin_configs` | 13 | 有真实数据，模型已删（仅 `__pycache__` 残留 `plugin_config`） |
| `plugin_definitions` | 13 | 有真实数据，模型已删 |
| `sync_tasks`/`sync_executions`/`sync_execution_files`/`sync_file_snapshots`/`sync_plugin_configs`/`sync_plugin_definitions`/`sync_task_drama_links`/`sync_task_locks` | 均 0 | 功能下线未清表 |

风险：这些表既不被 ORM 管理也不被迁移清理（迁移只 drop 了部分，如 `backend/alembic/versions/20261028_drop_openlist_settings.py:25-27`）。`plugin_configs`/`plugin_definitions` 仍含 26 行真实数据，说明"删模型≠删数据"，后续若恢复插件功能会读不到模型定义。

### 2.2 JSON 字段：全部是 Text，无 schema、无校验、无长度上限

全仓 `json.loads/json.dumps` **145 处 / 40 文件**。模型侧一律 `Text`（`backend/app/models/task.py:27-28`、`task_execution.py:19-23`、`tmdb_media_cache.py:28-29`、`drive_account.py:17-19` 等），没有使用 SQLAlchemy `JSON` 类型，也没有 CHECK。

| 字段 | 写入方 | 读取方 | 防护 |
|------|--------|--------|------|
| `tasks.addition_json` | `backend/app/services/tasks.py:136-137`、`api/routes/tasks_stream.py:288-289` | `task_serialization.py:86`、`task_executor.py:70-71` | 无 |
| `tasks.extra_json` | 同上 | `task_serialization.py:56,87` | 无 |
| `task_executions.adapter_snapshot` | `task_executor.py:191-192,381-382` | `task_serialization.py:31` | 无 |
| `tmdb_media_cache.payload_json` | `services/tmdb_cache.py` 等 | `api/routes/tmdb_view.py:119` | 无 |
| `drive_accounts.config_json/profile_json` | 适配器配置写入 | `adapter_registry.py:90` 等 | 无 |
| `system_settings.value` | 设置页 | `system_settings.py:96` 等 | 无 |

唯一做容错的是 `backend/app/services/task_share_links.py:10-22` 的 `_load_extra`（失败退 `{}`）。其余热门读取点（`task_executor.py:70-71`、`dashboard_drama.py:20`、`drama_share_autoupdate.py:27,38`、`generated_share_check.py:24`、`search_plugins.py:124`、`api_search_sources.py:32`、`telegram_bot/{actions.py:74,92,handlers.py:272,session_store.py:31}`）全部裸 loads。**后果**：手工 SQL、旧版本写入、半截事务都能留下脏 JSON；`GET /tasks`（`backend/app/api/routes/tasks.py:99-115`）在序列化时抛 `JSONDecodeError`，整页 500，而错误处理器只固定返回 `INTERNAL_ERROR`（`core/exception_handlers.py:33-38`），前端无法定位到具体任务。

**现状实测（只读）**：`json_valid()` 检查 tasks/task_executions/tmdb_media_cache/drive_accounts 全部通过（0 条脏行），所以现在没有线上故障；但这是"还没坏"，不是"不会坏"。

建议（不引入 JSON1 依赖）：
1. 在 `backend/app/services/json_fields.py` 增加 `safe_json_loads(raw, default)`，替换序列化层 4 个热点（`task_serialization.py:31,56,86,87`），读侧降级为默认值 + `logger.warning` 带 task_id；
2. 写侧在 `task_serialization`/`tasks.py` 统一走一个 `dump_extra()`，先过 Pydantic 模型（`schemas/task.py:11-24` 已有 dict 类型，可收紧成 `TaskExtraConfig`）；
3. 启动自检增加一条只读 `SELECT ... WHERE json_valid(col)=0` 告警日志，不做自动修复。

### 2.3 长字段生命周期：只增不减，且列表接口原样输出

- `ExecutionLog.lines` 无上限累积，`render()` 全量 join 后写入 `run_log`（`backend/app/extensions/runtime/execution_log.py:9-41`，`task_executor.py:191-192/252-253/365-366/381-382`）。一次失败任务若有大量文件/重试日志，`run_log` 可达数百 KB~MB 级；实测当前 max 2,372 B（执行记录仅 8 条，尚未放大）。
- `adapter_snapshot` 由 `json.dumps` 全量落库（同上），实测 max 311 B；`tree_summary` max 928 B。
- `tmdb_media_cache.payload_json` 实测 **max 942,120 B / 行，30 行合计 3,003,790 B**——单行≈1 MB，已接近 SQLite 单页链开销的红线；每次 TMDB 刷新都整行重写。
- 保留策略只有"每任务最近 20 条"（`core/settings.py:78`，`services/task_executions.py:16-22`），且 prune 只有两个触发点：启动时全库（`main.py:77-79`）与单任务执行结束（`task_executor.py:391-393`）。**长期不重启、不执行的任务不会清理**；`refresh_tokens` 更是完全没有清理（实测 219 行，其中 203 条 `revoked_at` 非空）。
- `GET /tasks` 无 `limit/offset`（`api/routes/tasks.py:99-115`），一次性返回全部任务 + 每任务 3 条执行（含 `run_log`/`adapter_snapshot`，`schemas/task.py:62-72`）；任务数增长后响应线性膨胀。

建议：写入侧截断 `run_log`（保留尾部 64 KB + `...[truncated N bytes]` 标记，纯应用层，不改 schema）；列表接口保留 3 条契约但把 `run_log` 换成"最近 200 行"摘要（新增字段 `run_log_excerpt`，不动旧字段）；把 prune 挂到 APScheduler 每日任务（现有 BackgroundScheduler 即可，无需新组件）。

### 2.4 约束缺失（SQLite 特性带来的"假约束"）

- 外键：连接事件只设了 4 个 PRAGMA（`backend/app/db/session.py:30-36`），**没有 `foreign_keys=ON`**；实测 `PRAGMA foreign_keys=0`。于是 `task_executions.task_id`（`models/task_execution.py:15`）、`task_lead_in_records.task_id`（`task_lead_in.py:21`）、`refresh_tokens.user_id` 等 `ondelete=CASCADE` 全部是装饰。当前删除任务依赖 ORM 关系 `cascade="all, delete-orphan"`（`models/task.py:35`）生效，但任何 raw delete / 批量 SQL 都会留下孤儿。实测当前孤儿为 0（任务 7 行、执行 8 行），因此**开启该 PRAGMA 是低成本、可立即做的**，但需先跑一遍孤儿检查（已做，0）。
- 长度：SQLite 不强制 `VARCHAR(n)`。`AuditLog.detail String(1024)`（`models/audit_log.py:25`）、`Task.savepath String(255)`（`models/task.py`）只在 Pydantic/前端层校验（`schemas/audit_log.py:21` max_length=1024）。任何绕过 API 的写入都会超长。
- 无 CHECK、无 `updated_at` 自动更新、无软删除：`TaskExecution` 无 `updated_at`，任务删除即物理删除（含历史），审计价值随之外键丢失。

---

## 3. Alembic 迁移与启动自动 upgrade 风险

### 3.1 现状

- 迁移 48 个文件，单 head `20261106_drama_lead_in`（与 `alembic_version` 实测一致）；13 处 `op.execute` raw SQL（如 `alembic/versions/20260629_sync_tasks_uid.py:39-56` 用 UPDATE 回填无模型表）。
- `env.py`：`target_metadata=Base.metadata`（`backend/alembic/env.py:18`）、`get_url()=settings.database_url`（:21-23）、online 用 `engine_from_config(poolclass=NullPool)` + `compare_type=True`（:39-48）。**没有 `render_as_batch`、`compare_server_default`、`version_table` 定制、`include_object`**——SQLite 下 ALTER 只能靠迁移里手写 `batch_alter_table`（13 处，如 `20260707_remove_shareurl_ban.py:23`）。
- 启动流程：`lifespan` 第一步 `_run_db_migrations_if_needed()`（`backend/app/main.py:68`），函数体 `RUN_MIGRATIONS` 默认 `"1"`、构造 `alembic.Config`、`command.upgrade(cfg, "head")`（:47-58），随后 `setup_logging(force=True)`。无锁、无备份、无预检、无 try/except。

### 3.2 五类具体风险

**R1 无保护自动迁移。** 迁移脚本本身包含多条 DDL + raw UPDATE（`20260629_sync_tasks_uid.py:39-56`），SQLite 的 pysqlite 驱动对 DDL 事务支持有限，中途异常可能留下"部分对象 + 未更新版本号"的库；`main.py:47-58` 没有 try/except，异常会穿透 lifespan 直接让进程启动失败（这算"好事"），但没有任何"失败前先备份"的兜底。反方向：若迁移在 `setup_logging` 之前失败，连日志格式都没配好。

**R2 DB URL 硬编码 → 迁移可能打真实库。** `core/settings.py:106-112`：仅 `XXM_TESTING=1` 时读 `DATABASE_URL`，否则恒为 `sqlite:///./data/app.db`。CI 的迁移冒烟依赖这个开关（`.github/workflows/docker-publish.yml:31-39`），而 round2 报告已记录一次真实事故：漏设 `XXM_TESTING=1` 把新增迁移应用到了本地默认库（`docs/backend-optimization-round2-report.md:43`）。同类操作只要发生一次在"生产 data 目录"上，就是不可逆的结构变更。

**R3 降级（downgrade）普遍不可逆。** 大量迁移的 downgrade 只重建空表/空列，数据不回来：
- `20260707_remove_shareurl_ban.py:16-28`：down_revision 字符串与 docstring 不符；升级 `batch_op.drop_column`，降级只 `add_column`（无数据回填）。
- `20261028_drop_openlist_settings.py:25-42`、`20261028_drop_task_savepath_snapshots.py`：upgrade `drop_table`，downgrade 仅 `create_table` 空表。
- `20261105_credits_in_media_cache.py:45-47`：先建 `tmdb_credits_cache`（`20261105_tmdb_credits_cache.py`）再 drop，历史链自相矛盾；`20261106_drama_lead_in.py:59` 同样 drop 表。
- 结论：**"回滚代码"≠"回滚数据"**。任何依赖 downgrade 的预案都不可信，必须"升级前备份 + 恢复备份"作为唯一回滚路径。

**R4 三真相分裂 + 静默跳过。** ORM 26 表 / 实库 37 表 / 测试 `create_all`。7 个迁移在表不存在时直接 `return`（`20260625_sync_tasks.py:25`、20260627/28/29、20260701/02/03），历史库与新建库走不同分支；`20261106_drama_lead_in.py:20-48` 用 inspector 守卫建表。这类"防御式迁移"会让缺表问题在开发/CI 全绿、生产才炸。测试全部 `Base.metadata.create_all`（`backend/tests/test_task_execution_prune.py:17-19` 等 14 处），永远验证不了迁移产物。

**R5 CI 覆盖不足。** 迁移冒烟只做"空库从 `20261105_credits_in_media_cache` 升到 head，再降一步再升"（`.github/workflows/docker-publish.yml:31-39`）。它覆盖不了：存量库（有数据、有历史版本）、从 `112649d93a0e_init` 的完整链、迁移后的 schema 与 ORM 一致性、以及 downgrade 到非空数据时的行为。

### 3.3 建议方案（零新依赖，按 S0/S1）

1. **S0：启动前置守卫**。在 `main.py:47` 之前：`Path("data/app.db").exists()` → 用标准库 `sqlite3.connect(...).backup()` 复制到 `data/backups/app-YYYYmmdd-HHMMSS.db`（WAL 下必须用 backup API，不能 cp 单文件）→ `PRAGMA quick_check` → 打印 `alembic current` 与 `db size/freelist` → 失败则 `raise SystemExit(1)`。
2. **S0：迁移失败不启动**。`_run_db_migrations_if_needed` 包 try/except，异常写 `logger.error` 后 `raise`（而不是继续起服务），并把 `RUN_MIGRATIONS=0` 文档化到 README。
3. **S1：迁移矩阵**。CI 增两步：完整链 `upgrade head`→`downgrade base`→`upgrade head`；以及"带最小 fixture 的存量库"升级后 `alembic check`/metadata 对比。
4. **S2：漂移检测脚本**（`scripts/check_schema_drift.py`，只读取 `create_all` 到临时库 vs 迁移库的表/列差集），挂 CI，允许白名单（10 张遗留表）。

---

## 4. 可观测性缺口

### 4.1 已有能力（不要重复建设）

HTTP 线程内：`TimingMiddleware` 生成 `x-request-id`、绑定 `RequestScope`、统计 SQL/外部 HTTP 次数与耗时、慢请求 WARNING（`backend/app/middlewares/timing.py:31-51,56-57,62-87`），响应头 `x-response-time-ms/x-sql-count/...`（:99-107），SSE 不计慢请求（:109-111）；`/metrics` 返回进程内 JSON 快照（`core/metrics.py:19,51`，deque 512 采样、p95 近似 :44-47）；`logging.py:29-73` 统一 dictConfig（纯文本、仅 stdout）。

### 4.2 缺口清单

| 维度 | 现状 | 影响 | 最小改造 |
|------|------|------|----------|
| 后台任务 | `task_scheduler.py` 的 `run_drama_tasks`/`run_tmdb_cache_refresh`/`run_drive_account_probe`/`run_audit_log_cleanup`（:207-459）无计时、无成功/失败计数；`run_audit_log_cleanup` 连异常日志都没有（:449-459） | 任务失败只能翻日志；`send_runtime` 通知失败也静默（:279-285） | 复用 `metrics_store.record/incr`，在 4 个 job 外层加 `_job_duration_seconds`/`_job_failures_total` |
| 调度器自身 | 无 `add_listener`、无 jobstore、无 job 状态（grep 0 命中）；`max_instances=1/coalesce=True/misfire_grace_time=60`（:111-119）只保证不重入 | 漏跑/未触发/跳过的 job 不可见 | `scheduler.add_listener(EVENT_JOB_MISSED/EVENT_JOB_ERROR)`，把计数写进 metrics |
| DB / 池 | 无 pool 使用率、无 `database is locked` 重试计数、无 WAL 大小/文件大小/freelist | 锁竞争只能靠 `task_admission` 409/429（`task_admission.py:64`）间接感知 | 启动时与每日任务里读 `PRAGMA page_count/freelist_count`；SQLAlchemy `event.listens_for(Engine,"checkout")` 计数 |
| 准入 | `TaskAdmission(max_active=4)`（`task_admission.py:64`）进程内，无当前占用量/排队量指标 | 429 突增无法解释 | 暴露 `len(active)` 为 gauge |
| 错误 | 异常处理器只 `logger.error` + 固定 `INTERNAL_ERROR`（`core/exception_handlers.py:33-38`）、无 request id/path/user；`write_audit_log` 只 `db.add` 无 rid（`services/audit.py:6-29`） | 5xx 与慢请求（有 rid，timing.py:62-87）无法关联 | 异常处理器读 `RequestScope.request_id` 拼进日志与响应头；audit 增加 `request_id` 列（需迁移） |
| 日志形态 | 纯文本 console、无轮转（`logging.py:41`；Dockerfile 日志经 supervisor 到 /dev/fd/1，`Dockerfile:53-61`） | 容器日志无限增长、无法按字段检索 | Formatter 改 `key=value`（不改依赖）；Docker 部署加 `--log-opt max-size` 文档 |
| 指标输出 | `/metrics` 是私有 JSON，无 Prometheus 文本；鉴权要求 token/权限（`api/routes/metrics.py:24-51`） | 无法接现成监控 | 保留现有 JSON 契约，另加 `text/plain` 分支（同一函数，`Accept` 协商） |
| 健康检查 | `/health` 无鉴权、DB 挂仍 200（`health.py:58-59`），每次 2 个 git 子进程（:13-40）；Dockerfile 无 HEALTHCHECK（`Dockerfile:76-77`） | 反向代理/编排不会摘流量 | 拆 `/livez`（进程）与 `/readyz`（DB+迁移版本）；`/health` 保持兼容，DB 挂返回 503 |

### 4.3 关键盲区一句话

现有指标全部挂在"HTTP 请求生命周期"上，而后台调度线程、任务执行器、DB 锁、备份/迁移这些**真正会静默失败的部分完全没有埋点**；`metrics_store` 全仓仅 9 处引用（timing 与 metrics 路由各若干），说明扩展成本很低、收益直接。

---

## 5. 测试护栏强度

### 5.1 数字本身

404 passed / 8.45s / 0 failed。构成：`backend/tests` 26 个文件，其中 5 个 `drama_lead_in*` 占约 199 个 `def test_`；`backend/regression_tests` 11 个文件。

### 5.2 强度缺口

1. **没有 HTTP 层测试**：`backend/tests` 与 `backend/regression_tests` 中 grep `TestClient|httpx|AsyncClient` **0 命中**；路由测试是直接调用函数（如 `backend/tests/test_task_share_route.py:10,35` 直接 `post_create_task_share(...)`，`db=Mock()`）。因此鉴权依赖、Pydantic 响应模型、状态码、middleware 都不在护栏内。
2. **测试与生产 schema 不同源**：全部 `create_engine("sqlite://") + Base.metadata.create_all`（14 处），没有一处跑 `alembic upgrade`。
3. **测试目录未入库**：`.gitignore:18` 忽略 `backend/tests`、`:22` 忽略 `docs`；`git ls-files` 中 `backend/tests` 为 0。当前 404 项只存在于工作区，clone/CI 都拿不到（CI 只跑被跟踪的 `regression_tests`，`.github/workflows/docker-publish.yml:30`）。
4. **无迁移/备份/JSON 契约测试**：迁移只靠 CI 空库冒烟（§3.2 R5）；`task_serialization.py` 的容错、`run_log` 截断、`TaskOut` 字段集合都没有断言。
5. 并发/锁：只有 `task_admission` 的单元逻辑（若有），没有"两线程同时 reserve"或"sqlite locked 重试"的集成测试。

### 5.3 低成本护栏（S0 即可）

- 把 `backend/tests` 从 `.gitignore` 移出、纳入 git；CI 从 `pytest regression_tests` 改为 `pytest tests regression_tests`（现成 404 passed，成本≈0）。
- 新增 `backend/tests/test_api_contract.py`：用 `httpx.ASGITransport` 建 1 个用例，断言 `GET /tasks` 200 且 `TaskOut` 字段集合 == 快照集合（防止重构改字段名）。
- 新增 `test_migrations.py`（标 slow）：临时库 `alembic upgrade head` → `inspect(engine).get_table_names()` 与 `Base.metadata` 对比，白名单遗留表。
- 新增 `test_json_tolerance.py`：向 `addition_json` 写 `"{bad`，断言列表接口仍 200。

---

## 6. 前端 API 契约（不可破坏清单）

### 6.1 通用约定（改动即破坏）

- `baseURL='/api'`（`frontend/src/api/http.ts:53`）、默认超时 50s（:13）、`withCredentials`。
- 错误体固定 `{code, message, detail}`（http.ts:7-11,137-139；`dramaLeadIn.ts:74-92` 也按此解析）。
- 401 → 自动 `/api/auth/refresh` 重放（http.ts:77-114）；重试仅对 GET 或显式 `X-Retryable: 1`（:16-19）；`X-Silent-Toast` 控制静默。
- SSE 用原生 fetch 读：行前缀 `data: `、结束哨兵字面量 `[DONE]`（`frontend/src/api/tasks.ts:299-303`）。
- `x-request-id` 由后端注入（timing.py:31-51），前端未强依赖但排障依赖。

### 6.2 TaskOut / TaskExecutionItem 字段（`backend/app/schemas/task.py:62-72,87-109` ↔ `frontend/src/types/tasks.ts:27-46,59-93`）

必保字段：`id, task_uid, task_type, taskname, shareurl, savepath, pattern, replace, ignore_extension, account_name, tmdb_id, tmdb_media_type, tmdb_status, tmdb_is_ended, drama_update_progress{...9 子字段}, enabled, addition, extra, created_at, updated_at, executions?, lead_in?`。

高风险点：
1. **`executions` 每任务只回 3 条是硬编码**（`task_serialization.py:88`），前端据此渲染"最近执行"；改成 5/10 或分页都会改变 UI 语义（也改变响应体积）。
2. `lead_in` 缺省 `{"state":"none"}`（`task_serialization.py:64-66`；类型 `types/dramaLeadIn.ts:144-147`）；改成 `null` 会让 `.state` 读取报错。
3. `extra` 是自由 JSON，但前端实际读 `extra.share_link`、`extra.saved_latest{episode,season,name,at}`、`extra.shareurl_update_logs`、`extra.auto_update_shareurl/auto_update_115_shareurl`（`types/tasks.ts:3-14,16-25`）；后端 `schemas/task.py:11-24` 仍是 `dict[str,Any]`，重构时若改成强类型，必须保留这些键名。
4. `executions[].adapter_snapshot.generated_share_check{share_id,saved_at,status,message,checked_at}` 由任务执行器写入、前端渲染（`types/tasks.ts:37-45`），字段大小写/空值语义需保持。
5. 409/429 语义：`TASK_ALREADY_RUNNING`/`TASK_CAPACITY_EXCEEDED`（`task_admission.py`）已被前端错误码展示依赖；`POST /tasks/{id}/run` 返回 409 时不可退化为 500。

### 6.3 高频端点（前端调用点以 `frontend/src/api/tasks.ts` 为准）

`GET /tasks:18`、`POST /tasks:38`、`PATCH /tasks/{id}:61`、`PATCH /tasks/{id}/status:67`、`POST /tasks/{id}/run:73`、`POST /tasks/{id}/share:79`、`DELETE /tasks/{id}:84`、`GET/PATCH /tasks/scheduler:90,100`、`GET /tasks/magic-regex:95`、`POST /tasks/share/preview:131`、`POST /tasks/share/preview-batch:146`（前端按 50 分批 + X-Retryable，服务端分批契约不可改）、`POST /tasks/share/validate:241`、`validate_stream:272`、`drive/browse:212`、`drive/delete:221`、`drive/mkdir:226`、`drama/stop-completed:231`；引流：`GET/PUT /system-settings/drama-lead-in`、`POST .../scan`（90s 超时）、`own-file/resolve|list`、`GET /tasks/{id}/lead-in/preview`、`POST .../confirm`（120s 超时）。

### 6.4 建议

后端加一个 `tests/test_api_contract.py` 对 `TaskOut`/`TaskExecutionOut` 做 JSON Schema 快照（Pydantic v2 自带 `model_json_schema()`，零新依赖），CI 与前端 `pnpm typecheck` 同步跑；任何字段改名/裁剪必须同 PR 改前端，否则 CI 红。

---

## 7. 数据备份 / 回滚策略

### 7.1 现状（薄弱点逐条）

- 无备份脚本、无定时备份、README 只写"挂载 data 目录"（`README.md:113-121,125-137`），全文无 RPO/RTO/恢复步骤。
- 唯一备份是手工文件 `backend/data/app.db.bak-before-weibo-rollback-20261102-230558`（1,511,424 B，2026-11-02），现库 5,013,504 B——**备份比库小 70%，且已是 5 天前**。
- WAL 模式：`app.db-wal` 当前 309,032 B（`db/session.py:30-36` 设 `journal_mode=WAL`）。直接复制 `app.db` 会丢 WAL 中未 checkpoint 的事务；备份必须用 `sqlite3.Connection.backup()` 或 `VACUUM INTO`（Python 3.13 自带 sqlite3 支持）。
- `secrets.json` 每次启动重写（`core/settings.py:114-142`：tmp+chmod 0600+replace）。若用旧库回滚而 secrets 已被新版本重写，**已签发的 refresh_tokens 全部失效**（219 条），用户需重新登录；反之若 secrets 丢失，已加密配置无法解密。备份策略必须把 `data/secrets.json` 与 `app.db` 作为同一个恢复单元。
- 容器：`Dockerfile:21-34` 有 `mkdir /app/backend/data`，但无 `VOLUME`（:76-77）、无 `HEALTHCHECK`；数据持久化完全依赖用户 `-v` 挂载。
- 空间：`auto_vacuum=0`、无 `VACUUM`；实测 freelist 233/1224 页（≈954 KB，19%），删数据不还盘。SQLite 的 `page_count` 与文件大小只增不减。

### 7.2 目标（务实版）

- **RPO ≤ 24h**：每日一次在线备份；**RPO=0（迁移前）**：任何 `alembic upgrade` 前强制备份。
- **RTO ≤ 10min**：回滚 = 停容器 → 替换 `app.db`+`secrets.json` → 启动（`RUN_MIGRATIONS=1` 自动对齐，或显式 `alembic upgrade`）。
- 保留：最近 7 份每日 + 最近 5 份迁移前快照；总量 < 100 MB 可接受（现库 5 MB）。

### 7.3 落地设计（零新依赖）

1. `backend/app/services/backup.py`：
   - `def backup_sqlite(src, dst)`: `sqlite3.connect(f"file:{src}?mode=ro", uri=True).backup(sqlite3.connect(dst))`，随后对 dst 执行 `PRAGMA quick_check` 并校验 `alembic_version` 存在；
   - 同时复制 `secrets.json`（若存在）；
   - 文件名带 `reason`：`app-20261108-2210-pre-migrate-20261106.db`。
2. 调度：复用现成 `BackgroundScheduler`，每日 03:00 跑 `backup_sqlite` + 清理超过保留数的旧文件（`task_scheduler.py` 已有 job 注册模式，:53-99）。
3. 迁移前钩子：`main.py:47-58` 在 `command.upgrade` 前调用 `backup_sqlite()`，失败则不迁移（fail fast）。
4. 备份后校验：`quick_check` + 表数量 + `alembic_version` 打印到启动日志；失败则拒绝启动。
5. 文档：README 增加"备份/恢复/回滚"一节，含 `docker compose` 挂载 `./data:/app/backend/data` 与恢复命令。

### 7.4 回滚 runbook（写进 docs/operations/backup-restore.md）

| 场景 | 动作 | 验证 |
|------|------|------|
| 迁移后启动失败 | 停容器 → 用最近 pre-migrate 备份替换 app.db（连 -wal/-shm 一起删）→ 启动并设 `RUN_MIGRATIONS=0` | `/health` 200、`alembic_version` 与备份一致 |
| 业务数据被误删 | 停写（停容器）→ 恢复最近备份 → 重放 WAL?（备份已 checkpoint，无需）→ 启动 | 行数对比（tasks/task_executions） |
| secrets 丢失/轮换 | 用同一恢复单元的 secrets.json；否则全部 refresh_tokens 失效，需重新登录 | 登录 + refresh 流程冒烟 |
| 升级代码但未升级库 | 代码回滚镜像 tag（镜像带 BUILD_SHA）→ 库不动（向后兼容需保证） | 冒烟 + `alembic current` |

### 7.5 数据卫生（与备份同等重要）

- 每周 `VACUUM`（低峰，单进程，需独占；SQLite `VACUUM` 会临时占用 2 倍空间，5 MB 库无压力）；或开启 `auto_vacuum=INCREMENTAL`（需 `VACUUM` 一次生效，且写入连接 PRAGMA）。
- 清理：`refresh_tokens`（实测 219 行/203 revoked，无任何清理代码）、过期的 `share_preview_batch_cache`（有 purge，`services/share_preview_batch_cache.py:65`，仅在写路径触发）、`audit_logs` 已有 30 天保留（`services/audit_log_scheduler.py:44`）。
- 敏感数据：`drive_accounts.cookie` 为明文 Text（`models/drive_account.py:17-19`），备份文件必须 `chmod 600` 且不得外传；`secrets.json` 已 0600（settings.py:114-142）。

---

## 8. 依赖约束下的取舍

| 需求 | 常规方案 | 本项目取舍（不换框架） | 理由 |
|------|----------|------------------------|------|
| 备份 | pg_dump/cron 容器 | 标准库 `sqlite3.backup` + APScheduler 每日 job | 零新依赖、单文件库、5 MB 数据 |
| 迁移安全 | Flyway/gh-ost 式审批 | 启动前置备份 + fail-fast + CI 迁移矩阵 | 单实例部署、SQLite 无法在线 DDL |
| 可观测性 | Prometheus + OTel | 扩展现有 `metrics_store` + key=value 日志 + `/metrics` 文本分支 | 避免引入采集端；指标量 < 20 个 |
| 结构化 JSON | JSONB + schema 校验 | 保持 Text，应用层 safe loads + Pydantic 写侧校验 | SQLite JSON1 可用但迁移成本高；先修 500 路径 |
| 外键 | 数据库强约束 | 开启 `PRAGMA foreign_keys=ON`（孤儿已为 0）+ 应用层校验 | 一行 PRAGMA；需回归 raw delete |
| 任务队列 | Celery/RQ + broker | 保持 BackgroundScheduler + 进程内 admission | 单实例、任务量小（7 任务） |
| 日志 | structlog/JSON | logging Formatter 输出 key=value | 不改依赖，先保证 request_id/task_id 可 grep |

---

## 9. 分阶段建议（与 S0-S5 对齐）

| 阶段 | 动作 | 验收 | 预估 |
|------|------|------|------|
| S0 | ① `PRAGMA foreign_keys=ON`（先跑孤儿检查）② 迁移前备份 + fail-fast ③ tests 入 git + CI 跑全量 ④ `/health` 增加 ready 语义 | 重启后 `PRAGMA foreign_keys`=1；kill 迁移可见备份文件；CI 404+ passed | 1 天 |
| S1 | ⑤ JSON safe loads（4 个序列化热点 + 顶层 10 个读取点）⑥ `run_log` 写入截断 ⑦ 后台 job 计时/失败指标 ⑧ 备份每日 job + 保留策略 | 脏 JSON 用例 200；run_log 最大 64 KB；`/metrics` 有 job 段 | 2-3 天 |
| S2 | ⑨ 漂移检测脚本 + CI ⑩ 契约快照测试 ⑪ `/readyz` 与 503 语义 ⑫ 审计 request_id（迁移） | CI 红能拦住字段改名；`alembic` 与 ORM 零差异（白名单除外） | 3-5 天 |

---

## 10. 最大不确定性 / 未验证项

1. **未在存量库副本上实际执行 `alembic upgrade`**（只读规则限制）。迁移耗时、锁窗口、是否会触发 `database is locked` 均未知；建议 Lead 在 `data/app.db` 的副本上跑一次 `alembic upgrade head` 计时。
2. **`plugin_configs`/`plugin_definitions` 的真实用途未确认**：26 行数据可能是未来功能预留，也可能是死数据；清表前需业务确认。
3. **前端是否存在 types 未声明但运行时读取的字段**（动态 `any` 访问）：本次只静态读 `frontend/src/api` 与 `types`，未跑前端构建；建议 `pnpm typecheck` 后再改 TaskOut。
4. **生产 Docker 数据卷的磁盘余量与历史备份**未检查（本机 data 目录不代表部署机）。
5. **`refresh_tokens` 无清理**在 219 行规模无影响，但若多端登录频繁，需评估表增长与索引（`models/refresh_token.py:16` token_hash 唯一索引）。
6. **C2 的实际暴露面**：容器内 `RUN_MIGRATIONS` 默认 "1"（`main.py:47-58`），部署时若 `DATABASE_URL` 不被 `settings.py:106-112` 读取而 data 卷未挂载，迁移会写进容器临时层——重启即丢。需要 Lead 确认部署脚本是否设置了 `XXM_TESTING`。

---

## 附录：本次审计的只读证据命令

- 表清单/行数/PRAGMA：`python -c` + `sqlite3.connect("file:data/app.db?mode=ro", uri=True)`（37 表、foreign_keys=0、freelist=233/1224、index_list）。
- JSON 有效性：`SELECT sum(CASE WHEN json_valid(col) THEN 0 ELSE 1 END), count(*)`（全部 0 脏行）。
- 测试基线：`$env:XXM_TESTING="1"; .venv\Scripts\python.exe -m pytest tests regression_tests -q --ignore=...` → 404 passed。
- 迁移/端点/异常统计：`grep -c` 于 `backend/alembic/versions`（48 文件）、`backend/app/api/routes`（133 装饰器/27 文件）、`backend/app`（473 处 `except Exception`）。
