# 审计报告 · API / 领域分层（api-domain）

> 版本 v1 · 2026-11-08 · 审计人：api-domain-auditor（只读审计，未运行任何测试、未改动业务代码）
> 范围：`backend/app/api`、`backend/app/services`、`backend/app/schemas`、`backend/app/models`、`backend/app/extensions/runtime` 的分层与事务结构
> 配套：`docs/architecture-evolution-plan.md`（主方案）、`scripts/check_layering.py`（分层门禁）
> 去重声明：`docs/backend-optimization-baseline.md` 已完成的 P0 P1 P2 P3 P4 P5（eager loading、SSE 异步化、连接池、gzip、适配器 init 缓存加锁、metrics 鉴权、权限缓存、tasks 路由拆分）不在本报告重复；本报告只写尚未解决的结构性问题。

---

## 0. 审计口径（可复现）

| 口径 | 定义 | 采集方式 |
| --- | --- | --- |
| 分层 | 沿用 `scripts/check_layering.py:31-55` 的目标层：presentation(api/schemas/middlewares) → application(services) → domain → persistence(models/db)；infrastructure(extensions/thirdparty) 可依赖 application/persistence/domain；platform(core) 只依赖自身 | 脚本 + 自建 AST 扫描 |
| 越界 | 源层 import 了不允许的目标层模块（module-level；函数内延迟 import 单列统计） | 同上 |
| 事务点 | 文本级 db.commit()/flush()/rollback() 调用计数，并与 audit.write_audit_log、db.refresh 交叉核对 | grep |
| 循环 | 以模块为节点、module-level import 为边求强连通分量（Tarjan） | 自建脚本 |
| 规模 | 行数用 UTF-8 换行计数；端点数用 @router.* 装饰器计数 | 脚本 |

实测基数：`backend/app` 188 个模块、610 条 app 内 module-level import 语句、593 条去重后的模块对边；`api/routes` 28 个文件 4325 行、133 个端点；service 层 45 个文件。
方法学限制：全部结论来自静态阅读；运行时行为（线程交错、SQLite 锁竞争）只在代码证据足以支撑时下结论，推断项在 §10 单独标注。

---

## 1. 结论摘要

1. 分层并没有「没做」，而是「做了但没约束」：目录 api / services / extensions / models / core 已是分层雏形，但依赖边方向任意（§5），存在 1 个 27 模块的 import 环（`backend/app/db/base.py:7`），33 条延迟 import 是环被压住的化石证据。
2. 事务边界在表现层，且没有唯一提交者：路由层 89 处 db.commit()、62 处审计、33 处 refresh；service 层 81 处 commit、52 处 flush；两侧对「谁提交」的约定相反（§4 T1）。
3. 最贵的结构性问题不是「代码丑」，是「长事务跨分钟级网络 IO」：`backend/app/extensions/runtime/task_executor.py:95` 的 run_task 全程只 flush、由调用方 commit（`tasks_stream.py:176` / `task_scheduler.py:262`），网盘传输期间持有 SQLite 写事务（§4 T2）。
4. 执行入口实际是「3 活 + 1 死」：`api/routes/tasks.py:216-231`、`api/routes/tasks_stream.py:170-194`、`extensions/runtime/task_scheduler.py:242-265` 各写一遍编排；另有一份完整但无人调用的 `services/tasks.py:228-234 run_task()`（§2.4、§7）。这是最便宜的收口切入点。
5. 前端契约是手写镜像，最易回归的字段是 addition：后端 `schemas/task.py:23/:44` 只声明 dict[str, Any]，前端 `DramaTaskDrawer.vue` 直接读 84 处 / 23 个键（§6）。

| # | 问题 | 严重度 | 修复成本 | 代表证据 |
| --- | --- | --- | --- | --- |
| A1 | 长事务跨越网盘传输（写锁 + 不可重试副作用） | 高 | 中 | `extensions/runtime/task_executor.py:95`、`:198/:224/:245/:388`；`api/routes/tasks_stream.py:176` |
| A2 | 提交权分散、回滚路径缺失（89 commit / 3 rollback） | 高 | 中 | `api/routes/drive_accounts.py:126/:142/:158`；`services/drama_lead_in.py:1248` |
| A3 | 路由=应用服务（375 行单函数、路由内外部 IO） | 高 | 中高 | `api/routes/tasks_share.py:44-418` |
| A4 | 同一领域规则三份实现 | 中高 | 低中 | `tasks_share.py:193-359`、`drama_executor.py:436-465`、`drama_share_autoupdate.py:267-279` |
| A5 | 单请求双会话（隐式双事务） | 中高 | 中 | `services/share_preview_batch.py:543-553`；`tasks_stream.py:135/:171` |
| A6 | 服务反向依赖路由 | 中（面小、性质坏） | 低 | `services/telegram_bot/actions.py:13` |
| A7 | 27 模块 import 环（db.base ↔ models） | 中 | 低 | `backend/app/db/base.py:7` |
| A8 | 上帝模块（handlers 2365 行 / drama_lead_in 混 5 职责） | 中 | 高 | `services/telegram_bot/handlers.py:459`；`services/drama_lead_in.py:68-103/:715-760/:1389` |

---

## 2. Q1 路由层越界：清单与量化

### 2.1 七类越界（路由本该只做「鉴权 + 校验 + 调服务 + 装配 DTO」）

| # | 越界类型 | 计数 | 代表证据 |
| --- | --- | --- | --- |
| R1 | 路由内直接 db.commit() | 89 处 / 22 文件 | `api/routes/drive_accounts.py` 21 处；`tasks.py` 10；`tmdb_cache.py` 7 |
| R2 | 路由内写审计日志（通常与业务同事务） | 62 处 | `drive_accounts.py` 20 处；`tasks.py:164/:185/:192/:204/:227` |
| R3 | 路由内直接操作 ORM / 裸 SQL | 12 条 import / 8 文件；裸 select 8 处 | `tasks.py:123/:134-157/:161`；`drive_accounts.py:130/:348-350`；`task_templates.py` 3 处 |
| R4 | 路由内外部 IO（网盘/适配器登录） | 22 条 from app.extensions | `tasks_share.py:52-60/:77/:106-126/:130/:144`；`tasks_drive.py:74/:88/:112` |
| R5 | 路由内线程/并发编排 | 1 文件 4 处独立会话 | `tasks_stream.py:135/:171/:224/:234/:271` |
| R6 | 路由内触发调度器/运行时副作用 | 2 处 | `tasks.py:303/:324 task_scheduler_manager.reload()` |
| R7 | 路由内装配复杂 DTO（≥10 字段） | 至少 5 处 | `tasks_share.py:385-418`（23 字段）；`tasks_drive.py:112-140`；`tasks.py:99-115` |

补充统计：路由层 db.flush 仅 1 处（`setup.py:30`）、db.rollback 3 处（`setup.py:32`、`tasks_stream.py:196/:304`）、db.refresh 33 处——写多、回滚少、刷新多，是「每步都提交、失败靠异常兜底」的典型形状。

### 2.2 最严重的 8 个端点

| 端点 | 行数/证据 | 越界内容 |
| --- | --- | --- |
| POST /tasks/share（post_create_task_share） | `tasks.py:234-303` | 账号选择 → 适配器能力判断（:250-252 用 adapter_class.create_share_link is BaseCloudDriveAdapter.create_share_link 判「未实现」）→ 外部 IO → 异常映射 → 持久化 + 审计 + commit |
| POST /tasks/{id}/run | `tasks.py:216-231` | 准入 task_admission.reserve + 执行器 + 审计 + commit + refresh + 通知装配，全部在路由 |
| POST /tasks/share/preview | `tasks_share.py:44-418`（单函数 375 行） | 见 §2.3 |
| PATCH /drive-accounts/{id}/status | `drive_accounts.py:109-160` | 探测 + 直接改 ORM（:129-130）+ 三分支各自 commit（:126/:142/:158） |
| POST /drive-accounts/{id}/qrcode/poll | `drive_accounts.py:221-265` | 外部查询 → parse_config_json → 调 service 更新 → 再探测 → 直接改 session.payload（:248）→ commit（:250）→ 把 payload 塞进 ApiError(409) 的 detail（:251-265） |
| POST /drive-accounts/{id}/sms/submit | `drive_accounts.py:325-366` | 光鸭（guangya）分支在路由直接写 ORM（:348-350）并手工 json.dumps(profile_json) 9 键（:351-362） |
| GET /tasks/{id}/run/stream | `tasks_stream.py:229-332` | 路由内 with SessionLocal()、路由内起线程、路由内手工 new Task(...) 14 字段（:275-291）、手工拼 done/failed 事件 DTO |
| GET /drive/browse | `tasks_drive.py:34-141` | 路径归一/面包屑/逐段 IO/DTO 装配全在路由（:63-67、:88、:112） |

### 2.3 解剖 A：post_share_preview（`tasks_share.py:44-418`）

这 375 行一个函数里同时存在 5 种职责，且顺序是「外部 IO → 领域规则 → 再外部 IO → DTO」，中间任一环节失败都停留在半成品状态：

1. 应用服务：AdapterRegistry.detect_drive_type（:46）、DatabaseAccountManager(no_login=True)（:52-60）、AdapterFactory.create_adapter + ensure_initialized（:106-126）。
2. 外部 IO：extract_url / get_stoken / get_detail / get_fids / ls_dir(max_items=2000)（:60/:77/:130/:144）。
3. 领域规则：目录过滤/优先级/正则/magic rename（:193-285）、min_size（:288-300，且 :290 直接 from app.extensions.runtime.drama_executor import _parse_size —— 路由 import 执行器私有函数）、关键词/文件筛选（:302-334）、文件时间过滤（:336-359）、重命名冲突保留最大（:362-383）。
4. 缓存/设置读取：get_or_create_tmdb_setting + get_tmdb_runtime_config（:95）。
5. DTO 装配：手工构造 SharePreviewItemOut 23 个字段 → SharePreviewOut（:385-418）。

为什么这是结构问题而不是「函数太长」：同一套筛选语义在执行器里又写了一遍（`drama_executor.py:436-465`），在换链服务里第三遍（`drama_share_autoupdate.py:267-279`）。规则改一次要改三处，且三处的默认值/边界已出现不一致（路由侧 min_size 走 _parse_size，执行器侧读 self.task_data["addition"]）。

### 2.4 解剖 B：执行入口的三份活拷贝与一份死代码

| 拷贝 | 位置 | 编排内容 |
| --- | --- | --- |
| 1 同步路由 | `api/routes/tasks.py:216-231` | reserve → TaskExecutor(db).run_task → audit → commit → refresh → 通知 |
| 2 SSE worker | `api/routes/tasks_stream.py:170-194`（失败分支 :195-220） | 自建 SessionLocal → ExecutionLog → run_task → commit → refresh → 通知 → 手工 done DTO；失败时拼 12 键 dict |
| 3 调度器 | `extensions/runtime/task_scheduler.py:242-265` | 自建 SessionLocal → get → run_task → commit；重试逻辑 :266-273 |
| 死代码 | `services/tasks.py:228-234` | 与拷贝 1 等价的 run_task(db, task_id)，全仓库无调用方（调用方只 import create/delete/get/list/set_enabled/update） |

死代码的存在说明「下沉执行入口」曾被尝试但没接线；因此 S2 不需要从零设计，只需把这份实现补成唯一入口（§7、§8 S2）。

---

## 3. Q2 services 上帝模块

| 模块 | 行数 | 规模 | 混合的职责 | 证据 |
| --- | --- | --- | --- | --- |
| services/telegram_bot/handlers.py | 2365 | 76 个 def | Telegram 展示 + 编辑器状态机 + 直接 ORM + 事务 + 业务动作分发 | :459 单类；_show_*:503-788；_dispatch_*:1413-2145；状态机 :2146-2343；类内 4 处 with SessionLocal()、50 次 commit |
| services/drama_lead_in.py | 1398 | 47 个 def | 设置读写 + 网盘能力元数据 + 适配器扫描 IO + 令牌签发 + ORM upsert + 进程内锁 | 设置 :68-103；能力表 :152-178；扫描 :384-418/:467-508；令牌 :715-760；upsert+commit :1195-1306；锁字典 :1389 |
| services/share_preview_batch.py | 1060 | 20 个 def | 批量预览编排 + 缓存 + 线程池 + 自建会话写库 | :18 import SessionLocal；:31-40 TTLCache+Lock；:543-553 _short_tx 自建会话 |
| services/tmdb_cache.py | 998 | 38 个 def | TMDB 缓存 + 自建 NullPool 引擎 + 信号量 + 后台线程池 | :28-34 模块级执行器/锁/弱表；:41-61 自建引擎；:64-65 把 PoolTimeout 当常态 |
| extensions/runtime/drama_executor.py | 1203 | 12 个 def | 追剧执行 + 文件筛选规则 + 名称/集数解析 + 通知文本 | :436-465 筛选；:490/:1097/:1136 延迟 import；全文 0 commit |
| extensions/adapters/cloud189_adapter.py / baidu_adapter.py | 2303 / 2008 | — | 单文件承载 HTTP 协议 + 令牌 + 重试 + 业务目录语义 | 本报告不展开（属网盘链路审计） |

共性判据：上帝模块不是「行数大」，而是同一文件里同时出现 3 种以上「随变更原因不同」的代码。上表每个模块都满足：展示/协议、领域规则、持久化、并发机制至少各占一块，且彼此通过模块级可变状态耦合（_refresh_jobs、lead_in_lock、_executor、_permission_cache）。

---

## 4. Q3 事务边界

### 4.1 提交点分布（实测）

| 位置 | commit | flush | rollback | 说明 |
| --- | --- | --- | --- | --- |
| api/routes | 89 | 1 | 3 | 22 个文件；最大 drive_accounts.py 21 |
| services | 81 | 52 | 3 | 其中 telegram 70（handlers 50 + actions 20）、drama_lead_in.py 10、share_preview_batch.py 1 |
| extensions | 16 | 5 | 12 | task_scheduler.py 独占 11 commit / 11 rollback |
| core / middlewares | 0 | 0 | 0 | db/session.py:46-54 的 get_db 只在异常时 rollback，无 autocommit |

### 4.2 六类反模式

T1 提交权无约定，两种相反范式并存。
- 范式 A（路由提交、服务 flush）：services/drive_accounts.py:53/:76/:189 只 flush，routes/drive_accounts.py 21 次 commit。
- 范式 B（服务提交、路由纯调用）：services/drama_lead_in.py:1248 commit，而 api/routes/task_lead_in.py 全文零 commit。
- 后果：任何「在一个用例里组合两个 service」的调用方都无法确定提交时机；测试也无法用单事务回滚隔离。

T2 长事务跨越外部 IO（最高危）。
- extensions/runtime/task_executor.py:95 run_task 从入口到结束只有 flush()（:198/:224/:245/:388），没有 commit；commit 在 tasks_stream.py:176 / task_scheduler.py:262。
- :309-311 的注释「每轮独立落盘…下一轮网络请求不占用写锁」只覆盖自动换链循环，主转存循环仍是「一次事务包住整段网盘传输」。SQLite WAL 的单写者语义意味着这段时间写锁会阻塞其它写请求。

T3 单请求双会话（隐式双事务、写序不可控）。
- services/share_preview_batch.py:543-553：preview_share_batch(db, payload) 形参 db 只用于读，写入走 _short_tx 内部新建的 SessionLocal()（:549 commit、:552 rollback）。
- tasks_stream.py:135（prepare）与 :171（worker）同样是两个会话。跨会话可见性依赖提交顺序，代码里没有任何顺序约束。

T4 一个业务动作拆成多次提交，失败留下中间态。
- routes/drive_accounts.py:109-160：:126 commit 后 :128 抛 bad_request；:142、:158 各再提交一次。
- :221-265 扫码轮询里 :250 commit 后 :251-265 才构造错误响应。没有补偿动作。

T5 回滚路径形同虚设。
- 89 次 commit 对应 3 次显式 rollback（集中在 setup/tasks_stream）。get_db（db/session.py:46-54）只对「未提交」的脏会话做 rollback，对已 commit 的分支无效；main.py:111-113 的全局异常处理器只负责响应格式，不参与事务。因此「提交到一半失败」在结构上就是可能的。

T6 重试语义没有幂等后端。
- extensions/runtime/task_scheduler.py:266-273 只对 OperationalError 重试，注释 :268 自认 After the executor starts, its cloud writes may already have succeeded.，靠 execution_started 布尔与 database is locked 字符串判断规避。
- 即：外部副作用已成功、DB 已回滚 的窗口真实存在，且没有幂等键可判重。

### 4.3 目标事务语义（不需要新依赖）

1. 一个用例一个 Unit of Work：在 app/core/db/uow.py（新增模块，非新依赖）提供 unit_of_work() 上下文/依赖，commit 只在用例函数返回前发生一次；get_db（db/session.py:46-54）保留为兜底 rollback。
2. 仓储/服务一律不 commit：services/* 只 flush（现有 drive_accounts.py 已是这种形状，可直接作为范式），drama_lead_in.py 的 10 处 commit 由调用方接管。
3. 外部 IO 不持锁：执行器改为「阶段式提交」——进入网络 IO 前 commit 一次（把「已开始」事实落盘），IO 结束后在新事务里写结果；task_executor.py:309-311 的模式推广到主循环。这样 T2 解决，且天然获得「已开始」的恢复点。
4. 幂等键：以 TaskExecution.id（models/task_execution.py）作为外部副作用的判重依据，重试前先查该执行是否已有 terminal 状态——不需要新依赖，只需在 task_scheduler.py:266-273 的 except 分支加一次查询。
5. 审计归属：audit.write_audit_log 保持与业务同事务（现状已是），但调用点从 62 处收敛为「用例出口统一记一次」，避免同一动作多条审计或漏记。

---

## 5. Q4 依赖方向与循环

### 5.1 层间依赖边（去重后的模块对）

| 边 | 数量 | 允许？ | 典型 |
| --- | --- | --- | --- |
| api → services | 76 | 允许 | — |
| api → core | 63 | 允许 | — |
| api → schemas | 27 | 允许 | — |
| api → db | 26 | 存疑（presentation 直连 persistence） | routes/tasks_stream.py:135 with SessionLocal() |
| api → models | 12 | 违规 | 8 文件：tasks.py:22-23、drive_accounts.py:17、audit_logs.py:10-11、dashboard.py:9-10、system_settings.py:12、task_templates.py:12、tmdb_cache.py:13、tasks.py:79（函数内） |
| api → extensions | 22 | 违规（presentation→infrastructure） | tasks.py:18-26、tasks_share.py、tasks_drive.py |
| services → models | 56 | 存疑（application 直连 persistence） | 全层普遍 |
| services → extensions | 26 | 违规（application→infrastructure） | services/tasks.py:11-13、drama_lead_in.py:28-29、share_preview_batch.py:19-20 |
| services → api | 3 | 严重违规（反向） | services/telegram_bot/actions.py:13 |
| extensions → services | 23 | 违规（infrastructure→application） | task_scheduler.py:16-23 一次 import 8 个 services 模块 |
| services → schemas | 7 | 存疑（application 依赖 presentation DTO） | services/task_serialization.py |

判读：违规集中在「services 既是应用层又是持久化层」和「extensions 反向依赖 services」两处。前者是历史包袱（services 里同时有 repo 与用例），后者是调度器把编排写在了基础设施里（§2.4 拷贝 3）。

### 5.2 唯一的环：db/base.py:7 拖进 27 个模块

backend/app/db/base.py 末尾（第 7 行）有一句 import app.models，目的是让 Alembic/Pydantic 看到全部模型。于是形成：

app.db.base → app.models → app.models.* → app.db.base

Tarjan 实测：188 模块 / 610 条 import 语句 / 593 条唯一边 / 1 个强连通分量 / 27 个成员（app.db.base + app.models + 25 个 model 模块）。影响：

- 任何 model 模块的 import 都会把整个 model 包拉起来（启动期成本、测试隔离困难）；
- 分层门禁里这条环会让 26 个 model 模块的「违规」被重复计入，做预算时应把环与跨层违规分开计数（见 §8 判据）。

修法（无新依赖）：把「聚合模型」从 db/base.py 移到一个 composition root（app/models/__init__.py 已有聚合；Alembic env.py 直接 import app.models），db/base.py 只保留 Base。这是纯机械改动，但会牵动 alembic/env.py 与 scripts/check_layering.py 的基线，必须单独一步做。

### 5.3 延迟 import = 环的化石

函数内 import 共 33/610 条，集中在：services/resource_search.py 7、services/share_preview_batch.py 7、services/drama_share_autoupdate.py 5、api/routes/tasks_share.py 4、extensions/runtime/task_executor.py 4、extensions/runtime/drama_executor.py 3、api/routes/tasks.py 2（如 tasks.py:59-67 内 import app.services.magic_regex）。它们不是性能优化，是为了避免 import 期环。改造时应把「延迟 import 数」作为依赖健康度指标：目标 ≤5，且每一条都要有注释说明理由。

---

## 6. Q5 schema / 契约耦合

### 6.1 前端手写镜像（无 codegen，无契约测试）

| 前端 | 后端 | 重复内容 |
| --- | --- | --- |
| frontend/src/types/tasks.ts:48-57 TaskShareLink | backend/app/schemas/task.py:51-59 TaskShareLinkOut | 逐字段重复 |
| frontend/src/types/tasks.ts:59-93 TaskItem | backend/app/schemas/task.py:87-109 TaskOut | 逐字段重复（含嵌套 execution 形状） |
| frontend/src/types/tasks.ts:27-46 TaskExecutionItem | backend/app/schemas/task.py:62 TaskExecutionOut | 且前端 :37-45 额外声明 adapter_snapshot.generated_share_check 子结构，后端无对应 schema |
| frontend/src/types/tasks.ts:3-14 TaskExtraConfig | backend/app/schemas/task.py:23/:44 addition: dict[str, Any] | 前端有类型、后端零校验 |
| frontend/src/types/tasks.ts:108-181 SharePreview* / DriveBrowse* | backend/app/schemas/task_browse.py:34/:57/:118/:134 | 双写 |

补充：133 个端点中仅 84 个用 response_model= 声明（其余靠返回 dict 隐式序列化，如 tasks.py:208 DELETE 无 response_model），且有声明的端点 handler 内也多为手工构造 dict（tasks.py:99-115 先做 4 次跨服务装配再列表推导），schemas 里 from_attributes 使用数为 0——即 schema 只承担「响应校验」，不承担「ORM→DTO 映射」，映射逻辑散在路由与 services/task_serialization.py 两处。

### 6.2 addition 是契约黑洞

- 后端：schemas/task.py:23 新建任务 addition: dict[str, Any]、:44 更新 addition: dict | None；schemas/task_browse.py:19-26 又把同一批键以扁平字段再定义一次。
- 前端：frontend/src/components/tasks/DramaTaskDrawer.vue 读 addition.* 共 84 处、23 个唯一键：show_blocked, preferred_only, search_filter, filter_words, folder_mode, whitelist_mode, min_size, file_filter, file_filter_mode, file_min_date, dir_min_date, folder_filter, folder_exclude, folder_filter_mode, folder_exclude_mode, folder_priority, folder_priority_mode, search_exclude, whitelist_folders, search_filter_mode, search_exclude_mode, filter_rule_name, search_date_from。
- 后果：后端改键名/默认值不会报错，只会在前端静默失效；同一批键在 §2.3 的三份筛选实现里各解析一次，默认值已经分叉。

### 6.3 契约加固（不新增依赖）

1. 域名模型先行：把 23 个键收敛成 app/domain/task_config.py 的 Pydantic 模型（extra="forbid"），addition 在入口即 model_validate；老数据用 mode="before" 兼容缺失键。
2. 契约快照测试：对 8 个高风险端点（§2.2）录制现有 JSON 响应作为 golden（tests/contracts/*.json），重构前后对比——这是「保持 API 契约不变」唯一可执行的证据。
3. 前端类型生成：不允许加 codegen 依赖，用 python 脚本把 Pydantic model_json_schema() 导出为 frontend/src/types/generated.ts（纯脚本，无新依赖），并在 CI 里 diff 失败即报错。

---

## 7. Q6 top 8 模块「现状 → 目标分层」

### 7.1 目标包结构（用现有目录 + 新增少量子包，不新增依赖）

api/routes（presentation）→ app/application（用例，新增包）→ app/domain（纯规则，新增包）→ app/repositories（SQL/ORM，新增包）；extensions 只保留适配器/运行时能力，通过 app/ports（Protocol，新增）被用例调用。迁移期允许旧路径并存，新代码只准进新层。

| # | 模块（现状） | 行数 | 现状职责 | 目标层与目标文件 | 拆分动作（契约不变） |
| --- | --- | --- | --- | --- | --- |
| 1 | api/routes/tasks.py | 332 | 任务 CRUD + 执行 + 分享 + 调度器 | presentation；用例移到 app/application/tasks.py（TaskRunUseCase、CreateTaskShareUseCase） | 路由只留鉴权/校验/调 use case；:216-231 执行编排、:234-303 分享编排整体下沉 |
| 2 | api/routes/tasks_stream.py | 335 | SSE + 线程 + 会话 + DTO | presentation 薄壳；app/application/task_run_stream.py + app/runtime/workers.py | 会话与线程从路由移入 TaskRunHandle（§8 S3）；事件 DTO 用 task_serialization 唯一实现 |
| 3 | api/routes/tasks_share.py | 449 | 预览编排 + 领域筛选 + 外部 IO + DTO | app/application/share_preview.py（用例）+ app/domain/share_filters.py（规则）+ app/repositories/tasks.py | 375 行函数拆成「取数据 → 规则过滤 → 装配」三段；规则三份合流（§7.2 #3） |
| 4 | api/routes/drive_accounts.py | 414 | 账号 CRUD + 扫码/短信/签到 + 探测 | app/application/drive_accounts.py（use case）+ app/ports/drive_auth.py | 21 处 commit 收成 1 处/用例；适配器调用改走端口；:248 直接改 session.payload 改为用例方法 |
| 5 | api/routes/tasks_drive.py | 239 | 浏览/建目录/删除 + 路径归一 | app/application/drive_browse.py；路径归一/面包屑进 app/domain/paths.py | 路由只留参数校验；:34-141 全部下沉 |
| 6 | extensions/runtime/task_executor.py | 399 | 转存执行 + 分享变换 + 阶段落库 | 基础设施执行器，但不拥有事务；app/application/task_run.py 负责 begin/commit | 去掉「调用方 commit」的隐式契约（§4.3-3）；:95-399 按阶段拆私有方法 |
| 7 | extensions/runtime/task_scheduler.py | 463 | 定时触发 + 编排 + 重试 + 通知 | 只保留「触发」；编排调 TaskRunUseCase；重试策略进 app/application/retry.py | 删掉 §2.4 拷贝 3；:266-273 加幂等判重（§4.3-4） |
| 8 | services/telegram_bot/handlers.py | 2365 | Telegram 展示 + 状态机 + 业务动作 | app/api/telegram/（presentation 适配器）+ app/application/telegram_flows.py | 先拆「状态机 / 展示格式化 / 动作调用」三块；commit 收敛到用例；类内 SessionLocal 清零 |

第二批（本报告只标注，不展开）：services/drama_lead_in.py（1398）、extensions/runtime/drama_executor.py（1203）、services/share_preview_batch.py（1060）、services/tmdb_cache.py（998）、services/notifications/legacy_notify.py（798）。

### 7.2 每个模块的目标要点（按依赖顺序）

1. tasks.py → presentation 薄壳。目标签名 async def run_task(task_id, user, uow) -> TaskExecutionOut，内部 await use_case.run(task_id, actor=user)。判据：文件 ≤120 行、0 个 db.commit、0 个 from app.extensions。
2. tasks_stream.py → SSE 通道 + Worker 句柄。prepare/worker 移入 app/application/task_run_stream.py，路由只做 StreamingResponse(event_stream(handle))；判据：路由内 SessionLocal 为 0、手工拼接 DTO 为 0。
3. tasks_share.py → 三段式。SharePreviewUseCase（编排）+ ShareFilterRule（纯函数，输入 list[FileItem] + addition，输出 list[FileItem]）+ 装配函数（task_serialization）。三份筛选实现（:193-359、drama_executor.py:436-465、drama_share_autoupdate.py:267-279）合并为 app/domain/share_filters.py，用「同一输入 → 同一输出」的差分测试固定行为。
4. drive_accounts.py → 用例 + 端口。DriveAuthPort（Protocol：generate_qrcode/query_qrcode_status/sms_send/sms_verify/sign_in）定义在 app/ports/drive_auth.py；路由与用例都只依赖端口，AliyunAdapter/GuangyaAdapter 由 composition root 注入。
5. tasks_drive.py → 只读浏览用例。BrowseUseCase.browse(account, path) 返回 DTO；路径归一 re.sub(r"/+", "/")（:63-67）移入 app/domain/paths.py 并加单测。
6. task_executor.py → 无事务执行器。入口改为 run(task, *, on_phase) 回调，由用例决定何时 commit；flush 保留（同事务内可见）。
7. task_scheduler.py → 触发器。_run_task 变为 trigger(task_id) → TaskRunUseCase；失败重试读 TaskExecution 状态判定是否已产生副作用。
8. handlers.py → 三个文件 + 状态机表。目标：telegram/state_machine.py（纯函数转移表）、telegram/views.py（格式化）、telegram/actions.py（调用例）；判据：单文件 ≤600 行、类内 0 commit。

---

## 8. Q7 渐进拆分顺序与验证判据

原则：先立门禁（零业务风险）→ 先删死代码 → 先统一事务所有权 → 再搬代码。每步都可独立回滚，且每步都有「数得出来」的判据。

| 步骤 | 改动 | 预估 | 风险 | 验证判据（命令级） | 回滚 |
| --- | --- | --- | --- | --- | --- |
| S0 | 门禁：scripts/check_layering.py 进 CI；新增两条只读断言（见 8.1） | 1-2 天 | 无 | 违规数 ≤ 基线且每条新违规失败；路由层 db.commit() 计数 ≤ 89 | 删 CI 步骤 |
| S1 | 破环：db/base.py:7 的 import app.models 移到 alembic/env.py / composition root | 0.5 天 | 低（启动期） | check_layering.py SCC=0；测试基线通过（Lead 侧） | 还原一行 |
| S2 | 统一执行入口：启用 services/tasks.py:228 的 run_task 为唯一实现，三处调用改指向；补幂等判重 | 3-5 天 | 中（执行链路） | TaskExecutor( 实例化点 == 1；同一任务并发两次只产生 1 条 terminal execution | 开关 + 保留旧函数 |
| S3 | 事务所有权：新增 app/core/db/uow.py；drive_accounts 作为试点把 21 commit 收成 1 | 5-7 天 | 中 | routes/drive_accounts.py commit 21→1、审计条数不变；端点响应 JSON 与 golden 一致 | 单文件回退 |
| S4 | 规则合流：app/domain/share_filters.py + 三处改调用 | 4-6 天 | 中（行为等价需验证） | 差分测试：随机 200 组输入，新旧实现输出逐项相等 | 旧函数保留别名 |
| S5 | 用例化：tasks_share 375 行函数拆三段、tasks 执行编排下沉 | 6-8 天 | 中高 | 路由行数 ≤120/文件；tasks_share.py 0 个 from app.extensions；契约快照全绿 | 单端点回退 |
| S6 | 前端契约：model_json_schema() 导出 TS + addition 强类型 | 3-4 天 | 中 | frontend/src/types/generated.ts 与后端 schema diff 为空；pnpm build 通过 | 生成物可回退 |

### 8.1 两条 S0 门禁（不写业务代码即可加）

1. 依赖方向断言（加进 scripts/check_layering.py）：app.api 不得 import app.extensions / app.models；app.services 不得 import app.api；当前违规分别 22 / 12 / 3 条，只降不升。
2. 事务预算断言：api/routes 内 db.commit() 计数 89、with SessionLocal() 计数 4 作为预算，CI 超预算即失败（脚本读文件计数即可，无需新依赖）。

关键依赖顺序：S2 必须在 S3 之前（否则「谁提交」在统一入口与用例之间来回改），S4 必须在 S5 之前（否则拆分时无法确认行为等价），S6 最后（契约快照依赖 S5 的端点稳定）。

---

## 9. 依赖约束下的取舍

| 想做的事 | 常规方案（被排除） | 为什么不能做 | 现有依赖下的替代 |
| --- | --- | --- | --- |
| 跨进程缓存 / 分布式锁 | Redis + Redis 锁 | 不允许新增依赖；单进程假设 | cachetools.TTLCache + threading.Lock（已有 services/share_preview_batch.py:31-40 先例）；明确标注：多 worker 化不在本期范围 |
| 后台任务队列 | Celery / RQ / Dramatiq | 同上 | concurrent.futures.ThreadPoolExecutor + APScheduler BackgroundScheduler（已有：services/tmdb_cache.py:28、extensions/runtime/task_scheduler.py） |
| 依赖注入容器 | dependency-injector | 不允许新增依赖 | FastAPI Depends + composition root（main.py / 新的 app/bootstrap.py）手工装配 |
| 分层门禁 | import-linter | 不允许新增依赖 | scripts/check_layering.py（纯 stdlib ast，已存在，285 行） |
| 结构化日志 | structlog | 不允许新增依赖 | 标准 logging + extra=（core/logging.py 已有；P3 的 core/instrumentation.py 埋点保留） |
| 配置管理 | 新配置库 | 已有 | pydantic_settings.BaseSettings（core/settings.py:55-145），已满足 |
| 契约代码生成 | openapi-typescript / orval | 不允许新增前端依赖 | Pydantic model_json_schema() + 自写导出脚本（纯 stdlib） |
| 迁移事务编排 | 分布式事务 / Saga 框架 | 无依赖、无必要 | 「阶段式提交 + TaskExecution 幂等键」的手写 Saga-lite（§4.3） |
| 多进程/多 worker | gunicorn 多 worker + 共享缓存 | 超出本期；会破坏进程内锁/缓存假设 | 保持单进程（uvicorn 单进程 + 线程），把「单进程」写进 ADR |

代价说明：这些替代方案都建立在「单进程 + 单写库」假设上。task_admission（extensions/runtime/task_admission.py:64）、_permission_cache（core/deps.py:26）、lead_in_lock（services/drama_lead_in.py:1389）都是进程内的；一旦多 worker，语义立即失效。这是有意接受的技术债，不是遗漏。

---

## 10. 不确定性与未覆盖

1. 运行时验证缺失：本报告未跑 pytest（按分工由 Lead 执行）。§4 的事务风险基于代码路径推断，未在 SQLite 上复现「网盘 IO 期间写锁阻塞」的实测数据；建议 Lead 用现有基线（docs/backend-optimization-baseline.md 的 bench 脚本）加一条并发写测试确认。
2. services db:Session 220 等主方案数字未逐条复算：我复核了 89/81/16/62/33、133 端点、610 import 语句、593 唯一边、1 个 27 模块 SCC；其余（锁 25/18、注册表 19、裸 except 253、准入 5、测试 404）沿用主方案口径，未独立验证。
3. 执行副本口径差异：我严格定位「3 活 + 1 死」（§2.4）；主方案写 6 处，若含 share_preview_batch.py:801 线程池路径与 telegram 侧触发则可对齐，建议文档统一口径后再对外。
4. db/base.py 破环的连带影响未验证：改动会波及 alembic/env.py、app/models/__init__.py 的加载顺序，以及 check_layering.py 基线与若干测试的 import 假设，需在 S1 单独验证。
5. 行为等价性：§8 S4 的「三份筛选合流」是纯行为等价改造，但三处实现已出现默认值分叉（§2.3），「等价」应以「现有用户数据下的输出一致」为准，需要 Lead 提供一份真实 addition 样本集。
