# 后端开发规范：新模块接入 与 存量模块改造

> 适用范围：`backend/app/**`（第 7 节另含前端响应契约约束）。
> 配套文档：[架构演进方案](architecture-evolution-plan.md)、[架构总览](architecture/README.md)、[架构决策记录](architecture/adr/)。
> 维护原则：本文每一条「必须」都能被命令、测试或 CI 检查；写不进检查的规则不写进来。

修订日期：2026-11-13。配套 [后端改造任务书](backend-hardening-taskbook.md)，本轮代码已接线并完成本地验收；具体证据与联调限制见 [改造验证报告](backend-hardening-report.md)。

**现状与目标分开记录**：分层门禁检查导入方向，事务测试检查提交/回滚，golden 检查响应契约；三者通过仍不能证明业务职责充分解耦、故障可恢复或真实网盘可用。

| 工作包 | 当前实现 | 验证与限制 |
| --- | --- | --- |
| A：错误语义与 CI | 搜索来源结果分类、readiness/liveness、完整后端测试命令已落地 | 本地故障注入通过；远端 CI 未触发，不声称远端通过 |
| B：日志与读接口 | 日志/树限额，分页/概览/按需详情和前端消费已接线 | O(N) 轻量候选/概览成本保留；旧全量接口仅兼容 |
| C：网盘契约 | 10 家注册策略接统一文件/转存/分享能力契约 | 假响应与签名契约通过；无真实账号联调，不明结果保守 pending |
| D：用例与职责 | 五类触发入口、分享与引流通过用例，Telegram/计划/进度已拆分 | 准入仍在入口；单进程及崩溃恢复限制不变 |

下文标记“目标”的规则是本轮已接线模块及新代码的职责要求；未涉及的存量 CRUD 暂保留既有事务方式，不为套新样板扩张范围。每批完成时同步更新此表、示例、测试证据与相关 ADR，不能只改文档就标为完成。

---

## 0. 三分钟速查

提交前验证全量测试、分层、应用导入；涉及模型/迁移时另验证空库迁移与 schema 漂移。以下 PowerShell 命令从仓库根目录启动，使用项目虚拟环境并显式隔离数据库。建议在独立终端执行，避免测试环境变量影响随后启动的开发服务。

```powershell
$backendPython = (Resolve-Path .\backend\.venv\Scripts\python.exe).Path
$env:XXM_TESTING = '1'
$env:DATABASE_URL = 'sqlite://'
$env:RUN_MIGRATIONS = '0'
$env:SCHEDULER_ENABLED = '0'
Push-Location backend
try {
    & $backendPython -m pytest tests regression_tests -q --basetemp .tmp/pytest-guide
    if ($LASTEXITCODE -ne 0) { throw '后端测试失败' }
    & $backendPython -c "import app.main; print('import ok')"
    if ($LASTEXITCODE -ne 0) { throw '应用导入失败' }
} finally {
    Pop-Location
}
& $backendPython scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json
if ($LASTEXITCODE -ne 0) { throw '分层门禁失败' }
```

需要迁移/schema 检查时，接着运行以下命令。每次创建新的隔离库，不使用真实 `data/app.db`：

```powershell
$backendPython = (Resolve-Path .\backend\.venv\Scripts\python.exe).Path
$env:XXM_TESTING = '1'
$env:RUN_MIGRATIONS = '0'
$env:SCHEDULER_ENABLED = '0'
New-Item -ItemType Directory -Force .\backend\.tmp | Out-Null
$schemaProbeDb = Join-Path (Resolve-Path .\backend\.tmp).Path ("schema-check-" + [guid]::NewGuid().ToString('N') + '.db')
$env:DATABASE_URL = 'sqlite:///' + $schemaProbeDb.Replace('\', '/')
Push-Location backend
try {
    & $backendPython -m alembic upgrade head
    if ($LASTEXITCODE -ne 0) { throw '空库迁移失败' }
} finally {
    Pop-Location
}
& $backendPython scripts/check_schema_drift.py --db $schemaProbeDb --ignore-file scripts/schema_drift_allowlist.txt
if ($LASTEXITCODE -ne 0) { throw 'Schema 漂移检查失败' }
$env:DATABASE_URL = 'sqlite://'
```

2026-11-13 代码审查记录：全量测试 **570 passed、2 skipped**，分层 **252 模块、90 deferred imports、0 violations、0 import cycles**。这是带日期的历史基线；验收提交记录当次实际结果，不将固定测试数量作为通过条件。预算以 `backend/.layering-baseline.json` 为准。本轮接线后扫描为 287 模块、112 deferred、0 violations、0 cycles；验证记录见报告。

**十条铁律**（违反任何一条，评审直接打回）：

1. 表现层不直接 import `infrastructure` / `persistence`；装配点见 §1.1。目标业务路由只取得用例/查询入口，不能经容器转发具体类来规避职责边界。
2. 路由零裸 `db.commit()`；目标动作的事务由应用用例拥有，未改造的存量写端点暂保留 `transaction(db)`，见 §4。
3. 网盘、HTTP、Telegram IO 不占用数据库事务；显式结束只读事务，不把 `autoflush=False` 当成已经释放事务。
4. 网盘写入是独立外部阶段，最终通知在业务结果提交成功后发送；数据库失败不自动重放外部写入。
5. 仓储只 `flush` 不提交；提交由明确的用例/工作单元边界拥有。渐进持久化逐阶段登记，不能包成一次长事务。
6. 缓存定义容量、过期及写后失效；锁表和在途 single-flight 定义容量策略及释放规则，不错误地用 TTL 淘汰正在使用的锁/任务。
7. 后台工作走受控入口；转存执行受 `TaskAdmission` 配额约束，通知与轮询使用各自边界，不挤占转存执行槽。
8. 响应模型改动必须同步前端 TS 类型与 `regression_tests/golden/frontend_contract.json`。
9. 迁移必须可 downgrade；新增列必须可空或带 server_default。
10. 禁止 `except Exception: pass`；失败不能伪装成功空结果，可选通知降级须有日志且不推翻已提交的业务结果。

---

## 1. 分层规则：什么代码放哪里

### 1.1 层与依赖方向

| 层 | 目录 | 允许 import |
| --- | --- | --- |
| presentation（表现） | `app/api`（routes / presenters / deps）、`app/schemas`、`app/middlewares` | presentation / application / domain / platform |
| application（应用） | `app/application`（use_cases / queries / dto / 用例级服务） | application / domain / platform |
| domain（领域） | `app/domain`（实体、值对象、端口 Protocol） | domain |
| persistence（持久化） | `app/infrastructure/persistence`（models / repositories / session） | persistence / domain / platform |
| infrastructure（基础设施） | `app/infrastructure`（drive / execution / scheduling / notifications / search / http） | infrastructure / domain / application / persistence / platform |
| platform（平台/横切） | `app/core`（settings / errors / deps / runtime / logging / metrics） | platform |
| tooling（工具） | `app/scripts` | 全层 |

**装配点例外**：`app/main.py`、`app/application/container.py`、`app/application/factories.py` 在分层扫描中作为 composition root 豁免。这是工具规则，业务调用方通过 container/factories 获取依赖，不因此去 import 会启动应用装配的 `app.main`，也不在装配点放业务流程。

**已登记的 documented exception**：`app/application/tmdb_cache.py` → `app.infrastructure.persistence`（4 条，见 [ADR 0001](architecture/adr/0001-tmdb-cache-writer-isolation.md)）。新增例外必须：写 ADR → 改 `scripts/check_layering.py` 的 `DOCUMENTED_EXCEPTIONS` → 在 PR 说明，三者缺一不可。

### 1.2 目录树（当前目录，不代表职责改造已完成）

```text
backend/app/
├── main.py                 # create_app：lifespan（备份→迁移→权限→清理→调度→TG 轮询）、中间件、异常处理
├── api/
│   ├── routes/             # 端点：解析参数 → 调 application → presenter → 返回
│   ├── presenters/         # 纯函数：视图 DTO → 响应模型（不接 Session/db）
│   └── deps.py             # 认证/权限依赖
├── schemas/                # pydantic 响应/入参模型（对外契约）
├── application/
│   ├── container.py        # 组合根：现有类/模块出口；目标为向入口提供已装配的用例/端口
│   ├── use_cases/          # 业务流程；现有 run_task 统一参数，事务/通知收口属于批次 D
│   ├── queries/            # 读模型：frozen dataclass 视图 + 批量装配（禁 N+1）
│   ├── dto/                # application ⇄ presentation 共用的数据形状
│   ├── notifications/      # 通知选择/发送/队列
│   └── *.py                # 用例级服务（tasks/news/tmdb_view/audit/…）
├── domain/
│   └── ports/              # Protocol 端口（cache/locks/drive/task/notify/clock）——零 IO 零实现
├── infrastructure/
│   ├── persistence/        # models/ + repositories/ + session.py（事务边界实现）
│   ├── drive/              # factory（注册表/实例 LRU/单飞）+ adapters/ + gateway（端口实现）
│   ├── execution/          # task_executor / drama_executor / shared
│   ├── scheduling/         # APScheduler 封装 + task_admission
│   ├── notifications/      # 通知队列、Telegram bot 管理
│   ├── search/             # 搜索插件宿主
│   └── http/               # 出站 HTTP 客户端（豆瓣/TMDB 等）
├── core/                   # settings / errors / deps / runtime(锁、单飞、TTL 缓存、令牌刷新) / logging / metrics
└── scripts/                # 运维脚本（ensure_admin、init_db）
```

### 1.3 三条硬规则

1. **导入与职责都要检查**：表现层不 import `infrastructure` / `persistence`。container 现有的 `SessionLocal / commit / get_db / transaction` re-export 用于存量边界；目标路由获取已装配用例，不获取具体执行器/账号管理器/适配器或直接调用仓储。替换 import 只能修导入方向，不能作为职责改造的完成证据。
2. **application 不 import presentation**：不能返回 `app.schemas` 的模型；返回 `app/application/dto` 或 `queries` 视图，由 presenter 转成响应模型。
3. **新增顶层包必须注册层**：改 `scripts/check_layering.py` 的 `LAYER_OF_PREFIX`，否则脚本按未知层处理并报违规。

### 1.4 门禁怎么用

```bash
# 日常检查（0 容忍）
python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json

# 看某条规则的具体违规位置（file:line）
python scripts/check_layering.py --fail-on 0 --top 50

# 只在「违规数下降」时刷新预算文件，且必须与本次改动同一提交
python scripts/check_layering.py --baseline backend/.layering-baseline.json --update-baseline
```

> 2026-11-13 文件快照：`{"violations": 0, "deferred_imports": 90, "cycles": 0}`，实际值以预算文件为准。
> 当前 CLI 的失败判定只比较 violations，cycles/deferred 会报告但不因此自动失败；验收仍要求 violations=0、cycles=0，必须检查报告。
> deferred 的变动需说明装配/延迟加载理由，不能通过增加转发把业务依赖藏起来。预算不放宽，不能把报告数值当作业务解耦程度。

---

## 2. 新增一个功能的动线（7 步）

以「任务标签（task_tags）」为示意说明目标写法。该功能及以下同名模块并未在当前仓库实现，代码片段不是可直接运行的现有接口。

| # | 产出 | 放哪里 | 验收 |
| --- | --- | --- | --- |
| 1 | ORM 模型 | `infrastructure/persistence/models/task_tag.py` | 只用 SQLAlchemy 声明，无业务逻辑 |
| 2 | 迁移 | `alembic/versions/YYYYMMDD_task_tags.py` | `upgrade head` + `downgrade -1` 都成功，CI 的 db-schema-gate 绿 |
| 3 | 仓储函数 | `infrastructure/persistence/repositories/task_tags.py` | 只碰 `TaskTag` 模型；不 commit、不返回 `Select` |
| 4 | 应用 DTO 与端口 | `application/dto/`、`domain/ports/` | 输入/结果为明确类型，端口不暴露 ORM、具体适配器或原始响应 |
| 5 | 查询/用例及装配 | `application/queries/task_tags.py`、`application/use_cases/`、`container.py` | 查询批量装配；用例拥有短事务、审计、提交后通知；容器返回已装配实例 |
| 6 | presenter + 路由 | `api/presenters/task_tag.py`、`api/routes/task_tags.py` | 只做权限、命令映射、用例调用和响应；不以行数代替职责验收 |
| 7 | 测试 | `tests/test_task_tags.py` +（改契约时）`regression_tests/` 与 golden | 见第 10 节 |

### 2.1 目标路由样板（写端点，示意）

```python
# backend/app/api/routes/task_tags.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_scoped, require_permissions_scoped
from app.api.presenters.task_tag import task_tag_out
from app.application.container import get_db, task_tag_use_case
from app.application.dto.task_tag import CreateTaskTagCommand
from app.core.permissions import TASK_WRITE          # 权限码统一在 core/permissions
from app.schemas.task_tag import TaskTagCreateIn, TaskTagOut

router = APIRouter(prefix="/task-tags", tags=["task-tags"])


@router.post("", response_model=TaskTagOut, dependencies=[Depends(require_permissions_scoped(TASK_WRITE))])
def post_task_tag(
    payload: TaskTagCreateIn,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user_scoped),
):
    command = CreateTaskTagCommand(
        name=payload.name, color=payload.color, actor_user_id=current.user.id,
    )
    view = task_tag_use_case(db).execute(command)
    return task_tag_out(view)
```

会话可以作为边界依赖传入，但路由不组织业务写入或提交。示意中的命令/结果与用例职责见附录 A.2；在真实功能中定义后才能接线，不要仅新增同名访问器。

纯数据库用例：写库 → 同事务审计 → 提交 → 返回值对象。含网盘 IO 的用例：读取计划并结束读事务 → 外部操作 → 短事务保存结果和审计 → 提交后最终通知 → 返回值对象。需要重新读取结果时由用例在提交后处理，presenter 不触发 ORM 查询。

### 2.2 读端点样板（走 queries，不内联拼装）

```python
@router.get("", response_model=list[TaskTagOut])
def get_task_tags(db: Session = Depends(get_db)):
    return [task_tag_out(view) for view in list_task_tag_views(db)]
```

规则：**路由里没有 `select(...)`、没有 `json.loads`、没有循环查库**。装配逻辑在 `application/queries`（批量一次查完），字段转换在 `api/presenters`。

---

## 3. 数据库与迁移

- 模型只在 `infrastructure/persistence/models/`；模型里不写业务判断、不发外部请求。
- 迁移必须**可 downgrade**；db-schema-gate 会用空库跑 `alembic upgrade head` 并比对 ORM 表集合：
  - 模型有、迁移没建 → 失败（漏迁移）；
  - 迁移多建、模型没有 → 只允许 `scripts/schema_drift_allowlist.txt` 里的 10 张历史遗留表，新增即失败。
- **禁止在生产路径/测试里用 `Base.metadata.create_all` 建库**（历史上由此产生「模型/迁移双真相」）。测试需要建表时优先走迁移，或显式标注为隔离测试。
- 迁移前备份：
  - 启动时自动备份（`app/infrastructure/persistence/backup.py` → `backup_before_migrate()`，保留 5 份）；
  - 手动：`python ../scripts/backup_db.py --db data/app.db --keep 5`；
  - 上线前预检：`python ../scripts/migration_preflight.py --db data/app.db --fail-if-pending`。
- 有数据回填的迁移：分批 + 幂等 + 可重入；不要在单个事务里做大表全量更新（SQLite 写锁会把接口卡死）。
- 新增列：可空或带 `server_default`（SQLite `ALTER TABLE` 限制），并同步更新模型默认值。
- JSON 列（`addition_json` / `extra_json` / `adapter_snapshot`）不是垃圾桶：
  - 读的时候必须容错（`_load_json_dict` 之类的解析器），坏数据不能把接口打挂；
  - 写入必须有大小上限与字段白名单，禁止把无界列表塞进去。
- 索引：`task_id`、`started_at`、`status`、TMDB 缓存键这类查询条件必须有索引；新增热点查询先看执行计划。
- SQLite 特定事实（写代码前知道就没坑）：WAL 模式、`busy_timeout=30000`、`foreign_keys=ON`、连接池 5+10（见 `infrastructure/persistence/session.py`）。

---

## 4. 事务与提交

### 4.1 三个原语，别用错

| 原语 | 用途 | 谁用 |
| --- | --- | --- |
| `with transaction(db):` | 一个短数据库工作单元；异常自动 rollback，提交内置锁重试 | 目标用例/后台工作单元；存量路由迁移前可保留 |
| `commit(db)` | 显式结束当前写阶段，成功后才发该阶段的最终通知 | 已登记边界，不能由目标路由代替用例决定 |
| `end_read_tx(db)` | 结束**只读**事务（≠业务提交） | 长时间只读路径、刷新前释放读锁 |

表现层/应用层需要这些原语时从 `app.application.container` 取；基础设施可以直接使用其内部实现。当前 `UnitOfWork` 仅包装 Session 的事务上下文，不具备自动仓储注入、阶段编排或外部副作用恢复功能；不要在示例里把它当成已有的完整用例框架。

### 4.2 五条规则

1. **目标动作由用例拥有事务**：`test_commit_ownership.py` 目前只检查裸 commit 的位置和预算，不会证明 `commit(db)`/`transaction(db)` 的业务归属正确。目标路由连同提交、审计、通知编排一起移走，配套增加行为/边界测试；存量 CRUD 暂保留既有方式。
2. **事务里不做外部 IO**：正确形态是「读并结束只读事务 → 外部 HTTP/网盘 → 短写事务」。ORM 属性访问可能重新打开事务，阶段间传递值对象；有未提交写入时不能用只读事务清理函数偷偷提交/丢弃变更。
3. **提交后返回值对象**：需要最新数据库状态时由事务边界拥有者 refresh/重新查询；不要把已过期 ORM 对象交给 presenter 或跨线程使用。最终通知从明确执行结果取得信息，不依赖可能裁剪的展示文本。
4. **锁冲突要么安全重试、要么显式 5xx**：`transaction`/`commit` 内置的 `commit_with_locked_retry` 只重试 commit 且校验会话未被数据库回滚；`retry_on_locked(fn)` 的语义是「重跑整个用例」，**禁止用它包单个 commit**（会导致静默丢数据）。
5. **提交点迁移必须登记**：`test_commit_ownership.py` 维护「文件 → 预算 → 归属类别 → 理由」总账（2026-11-13 接线后预算为 94 处，实际以测试文件为准）。用例接管旧提交时同步减少旧文件预算，登记新归属，保持总预算不扩大；新增后台工作单元必须说明理由，不能只调大预算使检查通过。

### 4.3 反模式速查

| 反模式 | 后果 |
| --- | --- |
| 目标路由经容器调用仓储、提交、发送通知 | 导入检查可能通过，但业务编排仍分散在入口 |
| 已过期 ORM 对象交给 presenter 或另一个线程 | 隐式查询、会话状态错误，结果难以预测 |
| 事务里调用网盘/HTTP | 长事务卡住 WAL checkpoint，全站写阻塞 |
| 只读路径不结束事务 | 读事务一直持有快照，写者被拖慢 |
| `except Exception: pass` | 失败不可见，数据静默丢失 |
| 后台任务自己开 `SessionLocal()` 且不登记 | 提交归属失控，无法审计 |
| 用 `retry_on_locked(db.commit)` | 锁失败后重试提交空事务 → 静默丢数据 |

---

## 5. 并发与异步

### 5.1 前提：单进程假设（硬约束）

部署形态固定为 **1 个 uvicorn 进程 + 1 个容器副本**（`Dockerfile` 无 `--workers`，见 [ADR 0003](architecture/adr/0003-single-process-deployment-assumption.md)）。
不要给 uvicorn 加 `--workers`、不要水平扩容：那会让迁移、调度、TG 轮询、准入、缓存全部失效（重复转存、通知双发、权限漂移）。

### 5.2 场景 → 用什么

| 场景 | 用什么 | 位置 |
| --- | --- | --- |
| 同一 key 需要串行（同素材引流、同账号构造适配器） | `LRUKeyedLocks(maxsize=1024)` + `with locks.keyed(key):` | `app/core/runtime/keyed_locks.py` |
| 同一 key 只允许跑一次（令牌刷新、缓存首次回填） | `SingleFlight(maxsize=256).run(key, factory)` | `app/core/runtime/single_flight.py` |
| 账号等短时快照 | `TtlCache(maxsize, ttl_seconds)` + 写后 `invalidate_drive_accounts()` | `app/core/runtime/ttl_cache.py`、`cache_version.py` |
| 对外请求限速（按网盘/账号） | `KeyedRateLimiter.wait(key, min_interval, max_interval)` | `app/core/runtime/rate_limiter.py` |
| 通知发送（不阻塞请求） | `send_async(...)` → 队列单消费者 | `app/application/notifications/legacy_notify.py:828` + 队列 `app/application/notifications/queue.py` |
| 长任务执行 | `TaskAdmission.reserve()` + `start_worker()`（daemon 线程） | `app/infrastructure/scheduling/task_admission.py` |
| 只读路径占着事务 | `end_read_tx(db)` 尽早结束 | `app/core/runtime/tx.py` |

### 5.3 禁止事项

- **模块级无界 dict 当缓存**：必须用 `TtlCache` 或 `LRUKeyedLocks` 这类有界容器（历史教训：引流锁表只增不删，素材名维度可累积数千把锁）。
- **请求线程里 `time.sleep`**：限速放到 `KeyedRateLimiter`/基类 `_throttle_request`，不要在路由里睡。
- **持全局锁做网络 IO**：全局锁仅保护注册表/缓存读写；构造/登录 IO 在其外执行。同 key 的构造、令牌刷新及引流写入需要覆盖完整操作的互斥/单飞，允许在对应 key 的范围内做 IO，不能为缩短锁持有时间破坏防重。
- **裸 `threading.Thread(...)`**：除执行器与通知消费者这两处受控入口外，不要新起线程；异步化优先用「队列 + 单消费者」。
- **无限排队**：通知队列满 1000 条即丢弃并告警（`send_async` 返回 False）；新队列必须定义「满了怎么办」。
- **只依赖本地状态做幂等**：转存类必须查网盘侧事实（同名文件是否已存在），本地记录只作辅助。

### 5.4 压测与观测

- 压测脚本：`scripts/load_test.py`（接口 p50/p95）、`scripts/bench_task_list.py`（任务列表专项）。
- 并发相关回归：`regression_tests/test_keyed_locks.py`、`test_notify_queue.py`、`test_task_admission.py`、`test_runtime_components.py`。

---

## 6. 接入第 11 家网盘（适配器）

新增网盘先明确其能力、统一结果与失败语义，再实现适配器、注册和测试。文件数量不是完成条件；当前接口仍有历史差异，统一契约已按任务书批次 C 接线，新适配器仍须通过其声明能力的契约测试，不得只注册就声称兼容。

1. 在 `app/infrastructure/drive/adapters/` 新建 `xxx_adapter.py`，继承 `BaseCloudDriveAdapter`；
2. 在 `app/infrastructure/drive/factory.py` 的 `ADAPTER_MAP` 与 `URL_PATTERNS` 各加一行（或调用 `AdapterFactory.register_adapter / register_url_pattern`）；
3. 测试放 `regression_tests/test_<name>_adapter.py`，复用统一契约用例；`test_gateway_port.py` 的假网盘样板可作起点。假响应证明归一与编排，真实账号联调另记结果。

必须实现的抽象方法（其余有默认实现，按需覆盖）：

| 方法 | 语义 |
| --- | --- |
| `init()` | 登录/初始化，成功后可复用实例 |
| `get_stoken(pwd_id, passcode)` | 根据解析出的分享 ID 和提取码取令牌，具体签名以基类为准 |
| `get_detail(...)` | 分享/目录详情 |
| `ls_dir(pdir_fid, max_items)` | 列目录 |
| `save_file(...)` | 转存（核心写操作） |
| `query_task(task_id)` | 查询异步任务状态 |
| `mkdir(dir_path)` / `rename(...)` / `delete(...)` | 目录/文件操作 |
| `get_fids(...)` | 取文件 id |
| `extract_url(url)` | 解析分享 ID、提取码、分享目录 ID 和路径，不是获取下载直链 |

约定：

- **区分业务规则与网盘机制**：文件筛选、季集和增量计划属于业务/执行流程；`file_names` 参数、远端任务轮询、UC 中转目录和移动、不同返回字段属于网盘边界。用例通过统一网关/小能力端口调用，不在公共执行器中加网盘类型分支。
- **目标统一契约（批次 C，已接线）**：文件值对象明确 ID/名称/目录/大小/可选令牌；转存结果明确 completed/pending/failed、保存文件映射、远端任务 ID 和是否核实。提交成功不等于转存完成，令牌是否必需由具体网盘验证。
- **能力显式声明**：不支持分享/引流等能力时明确拒绝；不以 `hasattr`、基类方法比较、`inspect.signature` 或捕获 `TypeError` 后再次写入决定能力。
- **写入结果不明先核对**：数据库失败或写请求超时不能直接重放；返回文件顺序不保证与计划一致，重命名前核对来源与目标映射。

- **百度转存核对**：以完整的写前/写后目标目录快照识别新增文件，写前快照失败不提交转存。名称特殊字符变化的兼容仅在百度策略中处理，必须同时满足来源名称、目标名称与返回的新文件 ID 唯一对应；去除特殊字符后发生碰撞、ID 缺失或复用均保留 pending。待确认日志列出未能关联的文件名，不自动重放写入。

  批量转存只提交一次；提交成功后立即开始只读核对，默认确认窗口 60 秒、每 5 秒重新完整读取一次目标目录，最多 13 次（正在执行的列目录请求由自身超时限制）。空列表、部分可见和暂时读取失败可在窗口内继续核对；必须同一次完整快照包含全部可唯一关联的新增文件，不能累计不同快照凑齐。超时保持 pending；返回真实远端任务 ID（若有），网关最终确认也读取完整目录。增量子目录提交转存前统一将执行日志阶段设为 `save_file`。
- **限速与令牌**：用基类的 `_rate_limit_key()` / `_token_refresh_key()` / `_throttle_request()`；令牌刷新走 `TokenManager` 的单飞，不要自己写重试循环。
- **必须实现 `close()`**：实例被 LRU 淘汰时会调用；新增的 session/连接必须能释放。
- **失败要分类**：`classify_share_failure` 的返回值决定「分享失效 / 需要提取码 / 风控」的用户文案，不要新造字符串。
- **纯重构保持业务口径**：尤其保留 pending、手动执行日期与引流预览确认规则。任务书明确要求修正的搜索错误/健康状态属于有意契约变化，前后端及 golden 同步更新，不能被“逐字保留”阻止。

---

## 7. 读模型与响应契约

### 7.1 读：queries 视图（不是 ORM 对象）

- 复杂读模型放 `app/application/queries/`：frozen dataclass 视图，**不持有 Session、不持有 ORM 对象**；
- 关系数据必须在本层**一次批量查完**（禁止 N+1）：`regression_tests/test_task_queries.py` 用 SQL 计数锁定了「列表查询条数与任务数无关」；
- 路由只做 `[presenter(view) for view in list_xxx(db)]`。

### 7.1.1 列表、概览、详情的目标边界（批次 B，已接线）

- 常用列表先筛选/排序/取页 ID，再批量装配当页任务，默认 20 条、最大 100 条；不先读取全部任务与执行记录后切片。
- 列表/概览不读取 `run_log`、`tree_summary` 和完整 snapshot；所需徽标、成功更新及网盘类型用受控小字段投影。
- 工作台/日历需要全局数据时用轻量概览；该集合仍有 O(N) 元数据成本，不能宣称所有接口都与任务数无关。
- 分类、网盘候选、失败计数、名称排序和批量操作作用于全局匹配结果，不局限当前页。中文名称自然排序与同名网盘优先级保持现有口径；全局轻量索引成本单独测量。
- 执行详情和日志按需加载；UTF-8 日志分块默认/最大 64 KiB，返回实际下一字节偏移，跨任务 execution ID 统一 404。
- 新分页接口与旧全量 `/api/tasks` 并存直到内置调用方迁移完成；不能直接改变旧接口形状或悄悄截为首 N 条。
- 概览与分页缓存分开，分页按过滤/排序/页码区分；修改、删除、执行完成后失效相关缓存，旧请求不能覆盖新筛选结果。
- 具体接口、日志块及 facets 契约以 [任务书](backend-hardening-taskbook.md) 的批次 B 为准；发布顺序为先兼容后端、再新前端，回退反序。

### 7.2 写：presenter 纯函数

`app/api/presenters/*` 是纯函数：入参是应用层视图/DTO，出参是 `app/schemas` 的 pydantic 模型。**不接 db、不做查询、不做业务判断**。

### 7.3 契约三件套（改响应 = 改契约）

1. 新端点必须声明 `response_model=`；
2. 改任何响应模型/枚举/字段名，必须同步：
   - `frontend/src/types/tasks.ts`（或对应 types 文件）；
   - `backend/regression_tests/golden/frontend_contract.json`；
3. 更新 golden：先按 §0 设置隔离环境，再在 `backend/` 目录执行：
   ```powershell
   $previousGoldenUpdate = $env:UPDATE_CONTRACT_GOLDEN
   try {
       $env:UPDATE_CONTRACT_GOLDEN = '1'
       & $backendPython -m pytest regression_tests/test_frontend_contract_golden.py -q --basetemp .tmp/pytest-guide
       if ($LASTEXITCODE -ne 0) { throw 'Golden 更新测试失败' }
   } finally {
       $env:UPDATE_CONTRACT_GOLDEN = $previousGoldenUpdate
   }
   ```
   然后把 golden 的 diff 与代码改动放在**同一个提交**里。

golden 的覆盖范围由 `test_frontend_contract_golden.py` 和实际 golden 文件决定；新增分页摘要、详情、错误分类时扩展对应覆盖，不以历史模型数量代替检查。字段+类型+顺序、前端字段集合、SSE 线格式和错误体均需验证。
「后端有、前端暂时没消费」的字段必须在 `DRIFT_ALLOWLIST` 里写理由；前端一旦消费，白名单条目会变红，必须删掉。

生成文案的更多配图使用 `GET /api/media/{tv|movie}/{id}/images`，沿用 `TASK_READ` 权限。路由负责校验和错误响应，`application/tmdb_images.py` 获取配置后结束读事务，再由 TMDB 客户端获取海报/横图元数据；不把默认语言作为图片筛选条件。未配置返回 503、上游失败返回 502，成功空列表返回 200。对应类型为 `types/media.ts` 的 `TMDBImage`/`TMDBImages`，已纳入契约 golden。前端 `TMDBImagePicker.vue` 仅在打开时加载，当前页面缓存成功列表 5 分钟，缩略图每页 24 张；图片由浏览器直连 TMDB CDN，不新增后端图片磁盘缓存。选图后的原图准备和默认海报请求都必须防止晚到响应覆盖新选择。

追剧日历顶部“刷新已存进度”仅重新读取任务概览并复用已有 TMDB 资料，不清空缓存、不发起 TMDB 详情请求，也不等待后台资料刷新。它读取任务上次记录的已存进度，不扫描网盘。每部剧左上角“更新本剧资料”通过 `refreshTmdbDetail(id)` 强制更新单个 TV 条目；同一 TMDB ID 的多网盘/多日期入口合并请求，成功后更新共享缓存与排期映射，失败保留旧资料并允许重试。周视图、月视图及月视图更多弹窗均提供入口，单剧加载状态不遮挡整个日历。首次进入及后台检查仍可按原有过期规则补充资料。

每个网盘进度行的“校正”调用 `POST /api/tasks/{task_id}/saved-progress/correct`（`TASK_WRITE`），与任务执行共享准入锁。用例先冻结任务/有效账号/识别规则并结束读事务，扫描器只读独立保存目录及相关子目录，完整成功后以短事务条件更新 `extra.saved_latest` 并记录审计；允许把错误历史水位降低，不修改其他配置或网盘文件。空目录可清零；分页失败、读取上限、无法识别集号、并发配置变化均保留原值。不得拿 TMDB 集数、目录名“全 N 集”或不完整列表充当实际保存证据。新网盘必须显式声明完整列目录能力并通过分页失败测试，否则拒绝校正；继承旧 `ls_dir` 不代表具备完整枚举能力。前端保持“已存 10 / 16”格式，按任务合并校正请求，成功后更新任务缓存，任务变更事件只刷新本地进度。

手动校正忽略以“彩蛋”标注的附加文件、文件名包含“角色番综”的内容，以及无可识别集号的 `YYYY.MM.DD-标题` / `YYYY-MM-DD-标题` 日期命名内容；日期开头但带有明确集号的正片仍计入。仅有附加内容、没有可识别正片时保留原进度；其他无法识别文件仍拒绝校正，并返回具体文件的相对路径和数量。此过滤仅用于手动校正，不改变自动转存和重命名规则。

### 7.4 SSE 与错误体（前端按字节/状态码解析）

- SSE 事件：`init` / `log` / `stage` / `done`，空闲 15s 发 `: ping`；
  格式固定 `event: <name>\ndata: <json>\n\n`，JSON 用 `ensure_ascii=False`；
- `tasks_share` 的流结束哨兵是 `data: [DONE]`（单独一段，不带 event 行）；
- 业务错误通过 `ApiError(code, message, http_status, detail)` 映射为 `{code, message, detail}`；未处理异常使用现有全局 500 处理，不能把异常原文返回用户。
  409（`TASK_ALREADY_RUNNING`）/ 429（`TASK_CAPACITY_EXCEEDED`）是前端重试与 toast 的判据，**不许改成 200 + failed**（见 [ADR 0002](architecture/adr/0002-task-admission-stays-at-call-sites.md)）。

### 7.5 搜索与健康检查目标语义（批次 A，已接线）

- 搜索无匹配：200、success=true、空列表；无启用源需要明确提示。部分源失败而其他源完成：200、partial=true、脱敏 warnings。
- 所有启用源失败：502/SEARCH_UPSTREAM_FAILED；全部失败且均为超时：504/SEARCH_UPSTREAM_TIMEOUT。成功源的空结果不能算失败。数据库和程序异常保持 500，不伪装空结果。
- `/api/health` 作为 readiness：数据库正常 200，故障 503，保留现有响应字段。新增 `/api/health/live` 仅检查 HTTP 进程响应，200，不访问数据库或网盘。
- 搜索异常已按来源分类；health 数据库故障已返回 503，并有不访问数据库的 live 端点，接口与故障注入测试已接线。

---

## 8. 配置、密钥与灰度

- 新配置项加到 `app/core/settings.py` 的 `Settings`（pydantic-settings，环境变量名 = 字段名大写），给默认值 + 一行注释说明生效范围。
- 灰度开关统一命名 `use_xxx`，默认 `True`；`False` 时必须能回退到旧路径（参考 `use_unified_run_use_case` / `use_notify_queue` / `use_keyed_locks`）。
  新增开关要三处登记：`settings.py`、方案文档 §10.4、至少一个回归测试（覆盖两个分支）。
- 密钥只放 `backend/data/secrets.json`（首次启动自动生成）或环境变量；**不写进代码、不提交**。
- 数据库地址在 `settings.py` 里硬编码为 `sqlite:///./data/app.db`，仅当 `XXM_TESTING=1` 时允许 `DATABASE_URL` 覆盖 —— 测试/脚本必须显式设置该变量，否则会写到真实库。
- 调度开关 `scheduler_enabled` 是进程级；测试和 CI 里应设为 0，避免跑到真实定时任务。

---

## 9. 可观测性

- **审计**：所有 mutating 端点、后台写操作都要 `write_audit_log(...)`，并与业务写在同一个事务里（回滚则审计一起回滚，这是有意的）。
- **异常**：禁止 `except Exception: pass`；错误分支记录可定位且脱敏的日志并明确返回语义。可选通知失败不推翻已提交业务，SQL/提交失败不能吞掉；搜索故障不伪装成功，目标语义见 §7.5。
- **慢请求**：`settings.slow_request_ms`（默认 1000ms）由 Timing 中间件记录；新增外部调用要给埋点，不要出现「不知道慢在哪」。
- **执行日志现状**：执行历史默认每任务保留 20 条（`task_execution_history_limit`）；`ExecutionLog` 已按追加时 UTF-8 字节限额保存头尾。记录条数限制、SSE 有界队列和 gzip 都不能替代单次日志容量控制。
- **执行日志目标（批次 B1，已接线）**：`execution_log_max_bytes` 默认 262144 字节，`execution_tree_max_bytes` 默认 65536 字节，均为正整数，按 UTF-8 字节计；日志在追加时就有界，保留头尾及截断提示/元数据，不能只在 render/响应时裁剪。
- **展示与事实分开**：现有成功通知解析 `tree_summary` 提取新增文件名，裁剪前必须先改为结构化结果驱动通知/进度/重命名，避免展示限额影响业务。历史大日志按需分块读取，不批量改写旧记录。

---

## 10. 测试要求

### 10.1 放哪里

| 目录 | 定位 | 修订时 CI 现状与目标 |
| --- | --- | --- |
| `backend/tests/` | 按业务模块组织的行为测试 | 发布工作流命令已覆盖 tests 与 regression_tests；远端运行结果需在实际 CI 执行后确认 |
| `backend/regression_tests/` | 分层/事务/契约/并发/幂等/端口等护栏，包含源码及运行行为测试 | 当前 CI 已运行；接入全量后仍保留 |

两组测试都需要长期生效，目录不代表测试寿命或重要性。按现有模块习惯归位；本地完整验收运行两组，CI 目标同样运行两组。CI 覆盖状态以 workflow 内容和实际运行结果为准，不因修改本文就视为已接入。

### 10.2 新功能至少要有的 4 类测试

1. 正常路径（含返回字段断言）；
2. 非法入参 / 权限不足 / 资源不存在（错误码 + 状态码）；
3. 并发或幂等（两次调用不产生两条记录 / 重复执行不重复转存）；
4. 事务回滚（异常时数据与审计都不落库）。

### 10.3 三个真实教训

- **不要用假对象骗过状态机**：`test_unit_of_work.py` 早期用假 db 让「锁冲突下静默丢数据」通过了测试；现在用真实临时 SQLite + 第二条连接 `BEGIN IMMEDIATE` 造锁冲突。
- **契约测试要跑真实字节**：SSE 不是断言常量，而是真的执行 `_stream_response()` 收 chunk 逐字节比对。
- **跑不到的代码 = 没有测试的代码**（2026-11-09 事故）：仓储下沉时把 `lookup = select(...)` 的第一处用法换成了 `repo.get_row(...)`，函数末尾第二处 `db.scalar(lookup)` 忘了一起换 → 用户点「TMDB 缓存刷新」直接 500（`NameError`）。同一批自动改写还漏了 4 个 import（`commit` / `drive_accounts_repo` / `datetime` / `random`）和一个模块内 helper（`_to_sort_ts`），全量测试却一直是绿的——因为这些分支没有用例。
  护栏一：`regression_tests/test_no_undefined_globals.py`（标准库 `symtable` 扫 `app/**/*.py`，出现「未定义的全局名」即失败；有例外必须登记且写明理由）。
  护栏二：`regression_tests/test_tmdb_cache_refresh_route.py`（直接调 `post_tmdb_cache_refresh` + 真实临时 SQLite + 假网络层，把「点刷新」整条路跑一遍）。新写的端点/分支也照这个套路补一个「真能被点到的测试」，别只测服务函数。
  **自查动作**：改完 import / 搬完函数后，除了跑测试，还要跑一次 `python -c "import app.main"` 和上面的静态护栏。

### 10.4 命令

完整 PowerShell 命令及隔离环境见 §0。已经设置该环境后，在 `backend/` 下运行：

```powershell
# 完整验收；测试数量以当次输出为准
& $backendPython -m pytest tests regression_tests -q --basetemp .tmp/pytest-guide

# 仅护栏，适合迭代反馈，不能替代完整验收
& $backendPython -m pytest regression_tests -q --basetemp .tmp/pytest-guide

# 单文件验证
& $backendPython -m pytest regression_tests/test_commit_ownership.py -q --basetemp .tmp/pytest-guide
```

Windows 可显式指定上述 `--basetemp`，避免临时目录权限或 MSYS 路径问题；该目录仅作 pytest 临时产物，不放用户数据，也不与另一个并行测试进程共用。

涉及前端，在仓库根目录使用已有工具检查，不新增测试框架：

```powershell
$frontendRegressionFiles = @(Get-ChildItem .\frontend\regression_tests\*.test.mjs | ForEach-Object { $_.FullName })
node --test $frontendRegressionFiles
if ($LASTEXITCODE -ne 0) { throw '前端回归失败' }
pnpm --dir frontend build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
```

### 10.5 真实服务联调与自动测试的边界

网盘响应归一、Telegram 分派和搜索插件加载可以且应使用假客户端做自动化验证，不能笼统称为“不可测试”。真实网络、真实账号权限及第三方行为仍需按 `scripts/e2e_smoke.md` 联调；PR 分开记录自动测试、浏览器检查和真实服务联调。环境不具备时写明未验证，不能把假响应通过标成真实网盘可用。

---

## 11. 提交与评审

- **一笔提交一个主题**；代码 + 对应的 golden/baseline/迁移必须同一笔（否则 CI 红或回滚不彻底）。
- 不要提交：`backend/data/*.db*`、`backend/data/secrets.json`、`__pycache__`、`.tmp/`、`backend/pytest-of-*/`。
- PR 描述模板：
  ```text
  改了什么：<一句话>
  为什么：<问题/需求>
  影响面：<端点/模型/迁移/契约>
  验证：pytest 结果、门禁结果、手工 e2e（如适用）
  ```
- 回滚优先级：① 关灰度开关（`use_xxx=False`）→ ② revert 提交 → ③ 用备份恢复库（`scripts/backup_db.py` 产物）。
  不要手工改生产库结构。

---

## 12. Definition of Done（复制到 PR 勾选）

- [ ] `python -m pytest tests regression_tests -q` 全绿（新增测试已加）
- [ ] `python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json` → `violations=0`、`cycles=0`
- [ ] `python -c "import app.main"` 成功
- [ ] 改模型/迁移：空库 `alembic upgrade head` 成功、`downgrade -1` 成功、`check_schema_drift` 无新增漂移
- [ ] 改响应/错误/SSE：golden 已更新且前端类型同步
- [ ] 目标路由只调用用例/查询；没有通过容器继续取得具体执行器、账号管理器或适配器来组织业务
- [ ] 提交点/旁路会话在 `test_commit_ownership.py` 登记归属；后台线程另验证受控入口、配额、会话隔离与释放
- [ ] 事务不跨外部 IO；提交失败不重放网盘写入；最终通知在提交成功之后发送
- [ ] 涉及读接口/日志：分页先于完整装配、列表不读取大字段、全局筛选/缓存正确、日志字节限额与分块读取有测试
- [ ] 涉及网盘：统一结果/能力契约通过，差异留在网盘边界，pending/结果不明不误判成功
- [ ] 新增配置/开关：`settings.py` 与文档同步；配置默认/边界测试、开关双分支测试齐全
- [ ] 涉及网盘/TG/搜索插件：附自动验证和真实联调记录，不能联调的部分明确注明
- [ ] 改造批次的现状表、样例、ADR 与 CI 覆盖状态按实际交付同步，未把目标写成已实现

---

## 附录 A：常用代码片段

### A.1 仓储函数（基础设施内部示意，不 commit）

基础设施内部可以返回 ORM 行；对应用用例暴露的端口应映射成明确值对象，而非把仓储模块或 ORM 对象直接交给路由。下例的 task_tag 模型是 §2 的示意功能。

```python
# backend/app/infrastructure/persistence/repositories/task_tags.py
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.infrastructure.persistence.models.task_tag import TaskTag


def list_tags(db: Session) -> list[TaskTag]:
    return list(db.execute(select(TaskTag).order_by(TaskTag.id)).scalars())


def add_tag(db: Session, *, name: str, color: str | None) -> TaskTag:
    row = TaskTag(name=name, color=color)
    db.add(row)
    db.flush()          # 只 flush：拿 id 但不提交
    return row
```

### A.2 目标用例与装配（示意，当前未实现 task_tags 功能）

§2.1 的 `CreateTaskTagCommand` 为 frozen dataclass，字段为 `name: str`、`color: str | None`、`actor_user_id: int`；返回的 `TaskTagView` 也是 frozen dataclass，字段为 `id: int`、`name: str`、`color: str | None`。

用例接收同一 Session 上的 store/audit 端口实例：`store.add(name=..., color=...)` 返回不可变标签记录，`audit.record_created(actor_user_id=..., tag_id=...)` 写入审计，两者只 flush 不 commit。端口在 domain 中定义，不引用应用 DTO 或基础设施类；store 适配器负责将 A.1 的 ORM 行映射成 domain 值对象。

```python
# application/use_cases/create_task_tag.py 内的用例方法示意
from app.application.container import transaction
from app.application.dto.task_tag import CreateTaskTagCommand, TaskTagView


def execute(self, command: CreateTaskTagCommand) -> TaskTagView:
    with transaction(self.db):
        tag = self.store.add(name=command.name, color=command.color)
        self.audit.record_created(actor_user_id=command.actor_user_id, tag_id=tag.id)
        view = TaskTagView(id=tag.id, name=tag.name, color=tag.color)
    return view
```

上例只包含数据库写入；有外部 IO 的业务不能把整个操作放进该事务。store/audit 出错或提交失败向上传播，不能返回 view 假装成功。

```python
# application/container.py 内的目标装配示意
def task_tag_use_case(db):
    from app.application.use_cases.create_task_tag import CreateTaskTagUseCase
    from app.infrastructure.persistence.repositories.task_tags import TaskTagStore
    from app.infrastructure.persistence.repositories.audit import TaskTagAudit

    return CreateTaskTagUseCase(db=db, store=TaskTagStore(db), audit=TaskTagAudit(db))
```

这些示意类由实际功能实现时定义，不是当前仓库已经提供的符号。装配点只建立依赖，不调用业务、不持有请求 ORM 对象、不跨线程复用会话；存量 `tasks_repo()` 等模块访问器由对应批次迁移，不一次性改写全部仓储。

### A.3 按 key 串行 + 单飞

```python
from app.core.runtime.keyed_locks import LRUKeyedLocks
from app.core.runtime.single_flight import SingleFlight

_locks = LRUKeyedLocks(maxsize=1024)
_flight = SingleFlight(maxsize=256)

with _locks.keyed(f"lead_in:{drive}:{account}:{path}"):
    token = _flight.run(f"token:{account}", lambda: refresh_token(account))
```

### A.4 后台会话与执行槽（存量方式示意，目标归属见 §4）

```python
from app.application.container import commit, db_session, task_admission_service
from app.application.tasks import get_task
from app.application.use_cases.run_task import RunPolicy, RunTaskUseCase


task_admission = task_admission_service()
slot = task_admission.reserve(task_id)  # HTTP/SSE 在建流/启动 worker 前完成准入

def work():
    with db_session() as db:           # 会话在 worker 线程内创建
        task = get_task(db, task_id)   # 只传 ID 入线程，在本线程加载 ORM
        RunTaskUseCase(db).run(task, RunPolicy.for_trigger("scheduler"))
        commit(db)                    # 存量后台提交点，迁移时同步归属测试

slot.start_worker(work)
```

所需符号分别从 `app.application.container`（`db_session`、`commit`、`task_admission_service`）、`app.application.tasks`（`get_task`）及 `app.application.use_cases.run_task`（`RunTaskUseCase`、`RunPolicy`）取得。当前 `start_worker()` 内部已持有并释放 slot，线程创建失败也会释放；无需再包一层 `with slot`。调度器原有同步循环继续用 `with slot`，不因为这个线程示例改成异步调度。

批次 D2 完成后，提交与通知由执行用例接管，worker 只负责在线程内建立资源并调用用例；不能把上面的存量 commit 复制进新入口，形成双重提交。

### A.5 适配器签名示意（不是完整可实例化的实现）

```python
from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter


class XxxAdapter(BaseCloudDriveAdapter):
    DRIVE_TYPE = "xxx"

    def init(self) -> None: ...
    def get_stoken(self, pwd_id: str, passcode: str = "") -> dict: ...
    def ls_dir(self, pdir_fid: str, max_items: int = 0, **kwargs) -> dict: ...
    def save_file(self, *args, **kwargs) -> dict: ...
    def query_task(self, task_id: str) -> dict: ...
    def mkdir(self, dir_path: str) -> dict: ...
    def close(self) -> None: ...
```

---

## 附录 B：常见问题

**Q1：这段代码该放哪一层？**

| 你想写的东西 | 放哪 |
| --- | --- |
| 新接口的参数/返回模型 | `app/schemas/` |
| 一次涉及多表/多步骤的业务流程 | `app/application/use_cases/`（或 `app/application/*.py` 用例级服务） |
| 列表/详情页的读模型装配 | `app/application/queries/` |
| SQL 查询语句 | `app/infrastructure/persistence/repositories/` |
| 调网盘/第三方 HTTP | `app/infrastructure/{drive,http}/` |
| 锁/缓存/单飞/令牌刷新 | `app/core/runtime/` |
| ORM→响应的字段映射 | `app/api/presenters/` |
| 定时/线程/配额 | `app/infrastructure/scheduling/` |

**Q2：门禁报 `presentation -> persistence` 违规怎么办？**
先判断这段 SQL/账号选择/事务/通知是否本就属于应用用例。目标路由应把该职责一起移到用例，由 container 装配依赖；不能只把 import 换成容器访问器继续在路由编排。未改造的存量边界可以使用已有导出，但必须标明迁移范围。

**Q3：测试报 `ModuleNotFoundError: No module named 'app'`？**
必须在 `backend/` 目录下跑，或显式 `PYTHONPATH=backend`。不要从仓库根跑 pytest。

**Q4：pytest 报 `PermissionError: [WinError 5]` 指向 `pytest-of-xxx`？**
先检查临时路径和权限；MSYS 路径只是可能原因之一。按 §0/§10.4 用项目 Python，并显式指定项目内专用 `--basetemp`；不删除或改权限整个用户临时目录。

**Q5：接口变慢，按什么顺序排查？**
① 是否 N+1（queries 层查数量）→ ② 是否有长事务/未结束的只读事务 → ③ 是否连接池等待（5+10）→ ④ 是否事务里带外部 IO → ⑤ 是否大响应没走 gzip。前四项都有对应的回归测试或门禁。

**Q6：我要临时加个 `--workers=4` 提性能，可以吗？**
不可以。见第 5.1 节与 [ADR 0003](architecture/adr/0003-single-process-deployment-assumption.md)：会重复迁移/重复调度/重复转存/通知双发。
