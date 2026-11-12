# 架构分层与门禁一页纸

> 适用对象：所有要改 `backend/app/` 的人。主方案见 [架构演进方案](../architecture-evolution-plan.md)（§3.1 分层规则、§3.6 事务模型、§9 阶段计划），
> 现状 → 目标目录的逐模块映射见其 [附录 A](../architecture-evolution-plan.md#附录-a现状--目标目录映射可直接当搬迁清单)，本文不复述。
>
> **动手改代码前先读 [开发规范](../development-guide.md)**：新增功能 7 步动线、事务与提交规则、并发组件选型、新网盘接入、契约维护、DoD 清单。本文只管「结构在哪」，规范管「怎么干活」。
>
> 当前状态：**S0–S5 全部完成（2026-11-09）**。S0–S3：路由层 0 处裸 `db.commit()`、22 个仓储经
> `app/application/container.py` 装配、提交点按四类归属登记（95 处 / 11 文件）、6 个端口（KeyedLock / SingleFlight / Clock /
> TaskRunUseCase / DriveGateway / RateLimiter+TokenManager）已进生产路径、schema 漂移门禁已进 CI。S4：查询链路 presenter 下沉 +
> 响应契约 golden、扫码会话回收、账号快照缓存 + 按账户限速 + 令牌刷新 single-flight、网盘网关端口实现与装配（薄语义层）、
> 物理搬目录 5 批（`services/`→`application/`、`extensions/adapters/`→`infrastructure/drive/`、
> `extensions/search_plugins/`→`infrastructure/search/`）。S5：持久化搬迁（`models/`+`db/`→`infrastructure/persistence/`）、
> **分层违规 115 → 0、import 环 0**、115 个旧路径垫片全部删除。
> 非阻塞小项① `application/queries` 显式拆分已完成（方案 §10.18，2026-11-09）：读模型是 frozen dataclass 值对象、
> presenter 变纯函数、列表查询条数与任务数无关（回归测试计数锁定）。
> 非阻塞小项② 前端契约 golden 已完成（方案 §10.19，2026-11-09）：后端模型字段/顺序、`frontend/src/types/tasks.ts` 字段集合、
> SSE 逐字节线格式、错误体全部落盘为 `backend/regression_tests/golden/frontend_contract.json`，两侧任一方改动不更新 golden 即失败。
> 当前基线：回归 **570 passed / 2 skipped**、分层违规 **0**、deferred 90、252 模块，
> `backend/.layering-baseline.json` = `{"violations":0,"deferred_imports":90,"cycles":0}`。
> 非阻塞小项③ 3 篇 ADR 已完成（方案 §10.20，2026-11-09）：[0001 TMDB 写路径例外](adr/0001-tmdb-cache-writer-isolation.md)、
> [0002 准入保留在调用点](adr/0002-task-admission-stays-at-call-sites.md)、
> [0003 单进程假设](adr/0003-single-process-deployment-assumption.md) —— 写的是「为什么不能那样改、什么条件下可以改」。
> 2026-11-09 事故复盘：TMDB 缓存刷新 500（仓储下沉遗留未定义名 `lookup`，同批共 6 处未定义名/漏 import）已全部修复，
> 新增两个护栏：[静态扫描 `test_no_undefined_globals.py`](../../backend/regression_tests/test_no_undefined_globals.py)
> 与 [刷新端点 `test_tmdb_cache_refresh_route.py`](../../backend/regression_tests/test_tmdb_cache_refresh_route.py)，方案 §10.22。
> 2026-11-10 加固：生成分享后立刻用同一 Cookie 自检（`BaiduAdapter._verify_created_share`），
> 只有「分享页明确标记失效」才报错，读不到一律放行（fail-open），避免把网络抖动变成用户可见失败；新增 9 项护栏，方案 §10.29。
> 2026-11-10 缺陷修复：「已删除的分享仍显示有效」——百度 `share/verify` 对死链也返回 errno=0，且 `get_detail` 把分享页的
> `errno=-7` 丢掉后按「空目录=成功」返回，网关又把空列表当有效。修复：适配器透传分享页 errno（`_shared_page_error`）、`save_file` 拒绝死链、
> 网关「成功但空目录」判不可用（`probe_share`）。新增 6 项护栏，方案 §10.28。
> 2026-11-10 需求：「追剧任务不到运行日期不能点执行」。判定抽成唯一事实源 `app/application/drama_schedule.py`（纯函数 + 读模型字段），
> auto 模式只读本地 TMDB 缓存不发请求，读不到按每天放行（fail-open）。方案 §10.30。
> **同日口径修正（§10.30.8）**：用户澄清「希望不到设定的日期也能点击执行，并且执行」→ 手动执行**无视运行星期**
> （按钮永远可点、路由不 409、执行器不 SkipTask，`RunPolicy.manual_run=True`）；运行星期只约束 APScheduler 定时轮询。
> 第一版的 409 拦截与按钮禁用已全部撤除，`run_schedule` 字段保留为展示信息。新增 13 项护栏，回归 553。
> **2026-11-12 缺陷修复（§10.31）**：百度「分享文件夹等待审核中」（`errno=-7`）以前被当成失败——检测显示「检测失败」、
> 执行判 failed 并推 ❌ 通知。现在检测层新增 `pending` 状态（徽标「审核中」，不入失败列表）、执行层命中即 `SkipTask`（记 skipped、不通知、
> 不触发换链），且「目录直转」降级分支不再吞掉该信号。新增 7 项护栏，回归 560。
> **2026-11-12 需求（§10.32）**：追剧任务列表加「分类」筛选（国剧/韩剧/日剧/美剧/港台剧/综艺/动漫/电影/其他剧/未分类）。
> 口径唯一事实源是 `backend/app/application/media_category.py`（纯函数），数据来自列表本就批量加载的 TMDB payload
> （零新增查询、零网络请求）；前端 `DramaTaskView.vue` 只按 `media_category` 本地过滤，且只显示实际出现过的分类。
> 新增 10 项护栏 + 契约 golden 同步（`TaskOut`/`TaskItem` 两侧同字段），回归 570。
> 未完成（非阻塞）：`domain/` 实体化（当前 `app/domain/` 只有 8 个 Protocol，无实体/值对象）。

## 1. 四层 + 横切层（唯一契约）

| 层 | 目标包 | 一句话职责 | 禁止 |
| --- | --- | --- | --- |
| presentation | `app/api/` + `schemas/` + `middlewares/` | 协议适配、鉴权、DTO 装配、错误码映射 | SQL/事务、直接 new 执行器/适配器 |
| application | `app/application/`（新代码） | 用例编排、**唯一事务边界**、端口调用 | ORM 查询、HTTP client、具体适配器 |
| domain | `app/domain/`（本次新增） | 实体/值对象/端口协议，纯 Python | FastAPI、SQLAlchemy、requests、任何 IO |
| infrastructure | `app/infrastructure/` | ORM、仓储、适配器、调度、通知、缓存/锁实现 | 反向 import `app/api` |
| platform | `app/core/` | 配置、日志、指标、错误类型、权限、时钟 | 依赖业务层（端口除外） |
| composition root | `app/main.py` | 唯一装配点 | 业务逻辑 |

依赖方向由 `scripts/check_layering.py` 编码为 `ALLOWED` 字典并作为 CI 门禁；规则文本以方案 §3.1 为准。

## 2. 现有目录映射（只记「现在在哪」，搬迁按附录 A）

- `app/api/`（33 模块）、`app/schemas/`（22）、`app/middlewares/` → presentation
- `app/application/`（66 模块，含 `use_cases/`、`queries/`、`dto/`、`container.py`）→ application
- `app/domain/`（8 模块，全在 `ports/`，仅 Protocol）→ domain
- `app/infrastructure/persistence/`（54）/`drive/`（19）/`execution/`（6）/`scheduling/`（3）/`notifications/`（2）/`search/`（7）/`http/`（3）→ infrastructure
- `app/core/` → platform；`app/main.py` → composition root
- **旧路径 `app/services/`、`app/extensions/`、`app/thirdparty/`、`app/models/`、`app/db/` 已全部删除**（S5 物理搬迁完成，无 `_compat` 垫片、无兼容 re-export）；
  看到旧路径的 import 一律视为过期代码，改成新路径。

## 3. 新代码写在哪里（S3 起生效）

1. **端口协议**只写 `app/domain/ports/`（`typing.Protocol`，零实现、零 IO）；实现留在 infrastructure。
2. **事务**只写 `app/infrastructure/persistence/unit_of_work.py` 的 `transaction(db)` / `UnitOfWork`（实现在 `app/infrastructure/persistence/session.py`）；
   presentation 与新 application 代码禁止 `db.commit()`。现有路由的 89 处 commit **已全部收口为 0**；
   服务/扩展层的 95 处提交点已逐文件登记归属与预算（`regression_tests/test_commit_ownership.py`），新增必须显式登记。
3. domain 只允许 import domain + 标准库；infrastructure 可以 import domain 端口，但不得反向 import api。
4. 跨层只走端口：触发方依赖 `TaskRunUseCasePort`（已接线，`RunTaskUseCase` 是唯一实现），
   不再 new 具体执行器或适配器（方案 §3.4/§3.5）。`ClockPort` 已由 `container.clock()` 提供；网盘操作走
   `container.drive_gateway(adapter)` 拿 `DriveGatewayPort`（实现 `app/infrastructure/drive/gateway.py`，业务文案留在
   application，见方案 §10.14）；`NotifierPort` / `CachePort` 尚未接线（通知入队语义与进程内缓存口径需专项，见方案 §10.11）。

## 4. 门禁与检查命令（仓库根目录，用 backend/.venv 的 python）

| 用途 | 命令 | 通过标准 |
| --- | --- | --- |
| 分层门禁 | `python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json` | 违规数 ≤ 预算（当前 **0**；90 deferred；0 cycle；251 模块） |
| schema 漂移 | `python scripts/check_schema_drift.py --ignore-file scripts/schema_drift_allowlist.txt` | `drift=0`（白名单只含 10 张已确认遗留表；`model_only` 永不豁免） |
| 迁移预检 | `python scripts/migration_preflight.py` | 报告 current/head/pending、库体积、WAL、freelist、最近备份 |
| 备份+校验 | `python scripts/backup_db.py --keep 5` | integrity_check/quick_check 均为 ok |
| 测试 | `cd backend && python -m pytest tests regression_tests -q` | 当前基线 553 passed, 2 skipped |

CI 侧已有三个 workflow：`layering-gate`（分层预算）、`db-schema-gate`（空库跑完整迁移链→drift→preflight）、
`docker-publish`（镜像构建与 `regression_tests`）。`backend/tests` 的 299 项目前只在本地跑。

预算规则：**只降不升**。新增一处违规 CI 立即红；每完成一个模块迁移就把 `.layering-baseline.json` 调低。
豁免只允许「临时 + 有 issue」，不允许改脚本的 `ALLOWED` 放宽。

## 5. 回退开关（方案 §9，已落地在 `app/core/settings.py`）

`use_unified_run_use_case` / `use_notify_queue` / `use_keyed_locks` / `use_account_snapshot_cache` /
`use_keyed_rate_limiter` / `use_token_refresh_single_flight` 默认 True（新路径），
出现回归时按开关回退到旧实现；开关只作为「切换期保险」，不得长期保留两套逻辑。

## 6. 迁移期纪律

- 逻辑分层先行：**先在新包写新代码，再逐个模块搬**，不做一次性大搬迁（方案 §3.2）。
- **端口实现只放 infrastructure**：`app/domain/ports/` 只有协议；`app/application/container.py` 是唯一装配点（函数内延迟 import）。
  `app.application` / `app.infrastructure` / `app.domain` 前缀已登记进 `scripts/check_layering.py` 的 `LAYER_OF_PREFIX`（不再是盲区）；
  旧前缀 `app.services` / `app.extensions` / `app.thirdparty` 的映射保留只为让历史分支的 import 报「模块不存在」而不是被当盲区放行。
- 一次 PR 只搬一个模块，且同步下调预算；搬完的模块不得再被旧代码 import（兼容层已随 S5 删除，不再新建 `_compat`）。
- 数据面约束（备份/回滚/schema 双真相）见 [audit-data-observability.md](audit-data-observability.md) §2/§3/§7。
- 已知限制：实库有 10 张遗留表（plugin_*/sync_*），已登记在 `scripts/schema_drift_allowlist.txt`；
  清理前新增表进白名单必须在 PR 说明清理计划。空库 `alembic upgrade head` 后 `drift=0` 是本轮已实测的结果。
