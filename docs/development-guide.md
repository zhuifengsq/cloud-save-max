# 后端开发规范：新模块接入 与 存量模块改造

> 适用范围：`backend/app/**`（第 7 节另含前端响应契约约束）。
> 配套文档：[架构演进方案](architecture-evolution-plan.md)、[架构总览](architecture/README.md)、[架构决策记录](architecture/adr/)。
> 维护原则：本文每一条「必须」都能被命令、测试或 CI 检查；写不进检查的规则不写进来。

**一句话**：分层靠门禁（`scripts/check_layering.py`）、事务靠边界（`transaction(db)`）、契约靠 golden（`backend/regression_tests/golden/`）。
这三样保持绿，重构成果就不会退回去。

---

## 0. 三分钟速查

提交前四条命令必须全绿（在仓库根 / backend 目录分别执行）：

```bash
# 1) 全量测试（cwd = backend）
cd backend
python -m pytest tests regression_tests -q
# 当前基线：509 passed, 2 skipped（新增测试后同步更新本行与 docs/architecture/README.md）

# 2) 分层门禁（cwd = 仓库根）
cd ..
python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json
# 期望：violations=0 import_cycles=0（modules/internal_imports 数字会随代码增长，不是指标）

# 3) 迁移能否建出模型期望的库（cwd = backend）
cd backend
XXM_TESTING=1 DATABASE_URL=sqlite:///./.tmp/schema-check.db python -m alembic upgrade head
XXM_TESTING=1 python ../scripts/check_schema_drift.py --db .tmp/schema-check.db --ignore-file ../scripts/schema_drift_allowlist.txt

# 4) 应用能 import（cwd = backend）
python -c "import app.main; print('import ok')"
```

**十条铁律**（违反任何一条，评审直接打回）：

1. 只有 `app.application.container` 是跨层通道；表现层不直接 import `infrastructure` / `persistence`。
2. 路由里不出现裸 `db.commit()`，写端点用 `with transaction(db):`。
3. 外部 IO（网盘、HTTP、Telegram）不放在事务里。
4. 提交之后再发副作用/再刷新对象；提交之后不要用提交前读到的属性当真相。
5. 用例/服务只 `flush`，提交由事务边界拥有者决定。
6. 模块级缓存必须「有界 + 有 TTL + 写后失效」，禁止无界 dict。
7. 新增后台线程必须走既有的受控入口（执行器/通知消费者），并受 `TaskAdmission` 配额约束。
8. 响应模型改动必须同步前端 TS 类型与 `regression_tests/golden/frontend_contract.json`。
9. 迁移必须可 downgrade；新增列必须可空或带 server_default。
10. 禁止 `except Exception: pass`；降级要打日志并说明返回值语义。

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

**唯一例外（组合根）**：`app/main.py`、`app/application/container.py`、`app/application/factories.py` 可以被任意层 import —— 它们是装配点，不属于任何层。

**已登记的 documented exception**：`app/application/tmdb_cache.py` → `app.infrastructure.persistence`（4 条，见 [ADR 0001](architecture/adr/0001-tmdb-cache-writer-isolation.md)）。新增例外必须：写 ADR → 改 `scripts/check_layering.py` 的 `DOCUMENTED_EXCEPTIONS` → 在 PR 说明，三者缺一不可。

### 1.2 目录树（当前完成态，新增代码按此归位）

```text
backend/app/
├── main.py                 # create_app：lifespan（备份→迁移→权限→清理→调度→TG 轮询）、中间件、异常处理
├── api/
│   ├── routes/             # 端点：解析参数 → 调 application → presenter → 返回
│   ├── presenters/         # 纯函数：视图 DTO → 响应模型（不接 Session/db）
│   └── deps.py             # 认证/权限依赖
├── schemas/                # pydantic 响应/入参模型（对外契约）
├── application/
│   ├── container.py        # 组合根：仓储/客户端/事务边界/组件的唯一出口
│   ├── use_cases/          # 业务流程（run_task.py = 唯一任务执行入口）
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

1. **表现层不 import `infrastructure` / `persistence`**：要事务、仓储、适配器，一律 `from app.application.container import ...`（`container` 已 re-export `SessionLocal / commit / get_db / transaction`）。
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

> 当前预算：`{"violations": 0, "deferred_imports": 84, "cycles": 0}`。
> `deferred_imports`（函数内延迟 import）是容器/避免循环依赖的产物，**允许增长但不参与失败判定**；
> `violations` 与 `cycles` 必须保持 0。

---

## 2. 新增一个功能的动线（7 步）

以「任务标签（task_tags）」为例，每步只写该层的东西：

| # | 产出 | 放哪里 | 验收 |
| --- | --- | --- | --- |
| 1 | ORM 模型 | `infrastructure/persistence/models/task_tag.py` | 只用 SQLAlchemy 声明，无业务逻辑 |
| 2 | 迁移 | `alembic/versions/YYYYMMDD_task_tags.py` | `upgrade head` + `downgrade -1` 都成功，CI 的 db-schema-gate 绿 |
| 3 | 仓储函数 | `infrastructure/persistence/repositories/task_tags.py` | 只碰 `TaskTag` 模型；不 commit、不返回 `Select` |
| 4 | 装配访问器 | `application/container.py` 加 `def task_tags_repo()` | 表现层/应用层只能从这里拿 |
| 5 | 读视图 或 用例 | `application/queries/task_tags.py`（读）/ `application/use_cases/*.py`（写） | 视图是 frozen dataclass；用例只 flush |
| 6 | presenter + 路由 | `api/presenters/task_tag.py`、`api/routes/task_tags.py` | 路由 < 60 行；写端点 `with transaction(db):` + 审计 |
| 7 | 测试 | `tests/test_task_tags.py` +（改契约时）`regression_tests/` 与 golden | 见第 10 节 |

### 2.1 路由样板（写端点）

```python
# backend/app/api/routes/task_tags.py
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user_scoped, require_permissions_scoped
from app.api.presenters.task_tag import task_tag_out
from app.application.audit import write_audit_log
from app.application.container import get_db, task_tags_repo, transaction
from app.core.permissions import TASK_WRITE          # 权限码统一在 core/permissions
from app.schemas.task_tag import TaskTagCreateIn, TaskTagOut

router = APIRouter(prefix="/task-tags", tags=["task-tags"])


@router.post("", response_model=TaskTagOut, dependencies=[Depends(require_permissions_scoped(TASK_WRITE))])
def post_task_tag(
    payload: TaskTagCreateIn,
    db: Session = Depends(get_db),
    current: CurrentUser = Depends(get_current_user_scoped),
):
    with transaction(db):                            # 一个用例一个事务
        row = task_tags_repo().add_tag(db, name=payload.name, color=payload.color)
        write_audit_log(db, actor_user_id=current.user.id, action="task_tag.create", target_type="task_tag", target_id=None)
    db.refresh(row)                                  # 提交后再刷新，再序列化
    return task_tag_out(row)
```

**注意顺序**：写库 → 审计（同事务）→ 提交（由 `transaction` 负责）→ `refresh` → 序列化 → 返回。

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
| `with transaction(db):` | 一个用例一个事务；异常自动 rollback，提交内置锁重试 | 路由写端点、后台工作单元 |
| `commit(db)` | 「提交后还要发副作用/发通知/返回 id」的显式提交 | 需要精确控制提交时机的流程 |
| `end_read_tx(db)` | 结束**只读**事务（≠业务提交） | 长时间只读路径、刷新前释放读锁 |

全部从 `app.application.container` 取（唯一合法来源）。`infrastructure/persistence/unit_of_work.py` 只给基础设施/测试用，**表现层不要 import**。

### 4.2 五条规则

1. **路由零裸 `db.commit()`**：`regression_tests/test_commit_ownership.py` 会把路由里出现的裸 commit 判红。
2. **事务里不做外部 IO**：正确形态是「读 → 外部 HTTP/网盘 → 短写事务」。反例：早期 TMDB 刷新在同一个事务里做外部请求，直接复现 `database is locked`。
3. **提交后刷新对象**：`db.refresh(obj)` 或重新查询；提交前读到的属性不是真相（可能被并发改掉）。
4. **锁冲突要么安全重试、要么显式 5xx**：`transaction`/`commit` 内置的 `commit_with_locked_retry` 只重试 commit 且校验会话未被数据库回滚；`retry_on_locked(fn)` 的语义是「重跑整个用例」，**禁止用它包单个 commit**（会导致静默丢数据）。
5. **新增提交点必须登记**：`test_commit_ownership.py` 维护了一份「文件 → 预算 → 归属类别 → 理由」的总账（当前 95 处 / 11 文件，分四类：请求级、应用生命周期、后台自有工作单元、有意渐进持久化）。新增后台工作单元要在总账里登记并写理由，**不能靠调大预算蒙过去**。

### 4.3 反模式速查

| 反模式 | 后果 |
| --- | --- |
| 路由里 `db.commit()` + 之后又查询/序列化 | 提交后对象过期，序列化出旧值或触发额外查询 |
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
- **持锁做网络 IO**：锁只保护内存状态与「构造过程」，IO 移到锁外。
- **裸 `threading.Thread(...)`**：除执行器与通知消费者这两处受控入口外，不要新起线程；异步化优先用「队列 + 单消费者」。
- **无限排队**：通知队列满 1000 条即丢弃并告警（`send_async` 返回 False）；新队列必须定义「满了怎么办」。
- **只依赖本地状态做幂等**：转存类必须查网盘侧事实（同名文件是否已存在），本地记录只作辅助。

### 5.4 压测与观测

- 压测脚本：`scripts/load_test.py`（接口 p50/p95）、`scripts/bench_task_list.py`（任务列表专项）。
- 并发相关回归：`regression_tests/test_keyed_locks.py`、`test_notify_queue.py`、`test_task_admission.py`、`test_runtime_components.py`。

---

## 6. 接入第 11 家网盘（适配器）

新增一家网盘 = **1 个文件 + 2 行注册 + 1 组测试**：

1. 在 `app/infrastructure/drive/adapters/` 新建 `xxx_adapter.py`，继承 `BaseCloudDriveAdapter`；
2. 在 `app/infrastructure/drive/factory.py` 的 `ADAPTER_MAP` 与 `URL_PATTERNS` 各加一行（或调用 `AdapterFactory.register_adapter / register_url_pattern`）；
3. 测试放 `regression_tests/test_<name>_adapter.py`（假响应即可，`test_gateway_port.py` 里有「假第 11 家网盘」样板）。

必须实现的抽象方法（其余有默认实现，按需覆盖）：

| 方法 | 语义 |
| --- | --- |
| `init()` | 登录/初始化，成功后可复用实例 |
| `get_stoken(share_url, passcode)` | 取分享令牌 |
| `get_detail(...)` | 分享/目录详情 |
| `ls_dir(pdir_fid, max_items)` | 列目录 |
| `save_file(...)` | 转存（核心写操作） |
| `query_task(task_id)` | 查询异步任务状态 |
| `mkdir(dir_path)` / `rename(...)` / `delete(...)` | 目录/文件操作 |
| `get_fids(...)` | 取文件 id |
| `extract_url(...)` | 取下载直链 |

约定：

- **不要在适配器里写业务编排**（多轮重试、选哪个文件、追剧季集判断）。编排在 `infrastructure/execution/`；跨网盘的统一语义走 `infrastructure/drive/gateway.py`（`DriveGatewayPort`：`list_dir / create_dir / transfer / check_share / probe_share / close`）。
- **限速与令牌**：用基类的 `_rate_limit_key()` / `_token_refresh_key()` / `_throttle_request()`；令牌刷新走 `TokenManager` 的单飞，不要自己写重试循环。
- **必须实现 `close()`**：实例被 LRU 淘汰时会调用；新增的 session/连接必须能释放。
- **失败要分类**：`classify_share_failure` 的返回值决定「分享失效 / 需要提取码 / 风控」的用户文案，不要新造字符串。
- **用户可见文案逐字保留**：重构/换实现时，错误消息按原文复制（有 golden 和 e2e 测试盯着）。

---

## 7. 读模型与响应契约

### 7.1 读：queries 视图（不是 ORM 对象）

- 复杂读模型放 `app/application/queries/`：frozen dataclass 视图，**不持有 Session、不持有 ORM 对象**；
- 关系数据必须在本层**一次批量查完**（禁止 N+1）：`regression_tests/test_task_queries.py` 用 SQL 计数锁定了「列表查询条数与任务数无关」；
- 路由只做 `[presenter(view) for view in list_xxx(db)]`。

### 7.2 写：presenter 纯函数

`app/api/presenters/*` 是纯函数：入参是应用层视图/DTO，出参是 `app/schemas` 的 pydantic 模型。**不接 db、不做查询、不做业务判断**。

### 7.3 契约三件套（改响应 = 改契约）

1. 新端点必须声明 `response_model=`；
2. 改任何响应模型/枚举/字段名，必须同步：
   - `frontend/src/types/tasks.ts`（或对应 types 文件）；
   - `backend/regression_tests/golden/frontend_contract.json`；
3. 更新 golden 用：
   ```bash
   cd backend
   UPDATE_CONTRACT_GOLDEN=1 python -m pytest regression_tests/test_frontend_contract_golden.py -q
   ```
   然后把 golden 的 diff 与代码改动放在**同一个提交**里。

golden 覆盖：10 个后端响应模型（字段+类型+顺序）、前端 15 个 TS 类型字段集合、SSE 逐字节线格式、错误体。
「后端有、前端暂时没消费」的字段必须在 `DRIFT_ALLOWLIST` 里写理由；前端一旦消费，白名单条目会变红，必须删掉。

### 7.4 SSE 与错误体（前端按字节/状态码解析）

- SSE 事件：`init` / `log` / `stage` / `done`，空闲 15s 发 `: ping`；
  格式固定 `event: <name>\ndata: <json>\n\n`，JSON 用 `ensure_ascii=False`；
- `tasks_share` 的流结束哨兵是 `data: [DONE]`（单独一段，不带 event 行）；
- 错误统一抛 `ApiError(code, message, http_status, detail)`，响应体固定 `{code, message, detail}`；
  409（`TASK_ALREADY_RUNNING`）/ 429（`TASK_CAPACITY_EXCEEDED`）是前端重试与 toast 的判据，**不许改成 200 + failed**（见 [ADR 0002](architecture/adr/0002-task-admission-stays-at-call-sites.md)）。

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
- **异常**：禁止 `except Exception: pass`；至少 `logger.exception(...)`，并明确「降级返回什么」。
- **慢请求**：`settings.slow_request_ms`（默认 1000ms）由 Timing 中间件记录；新增外部调用要给埋点，不要出现「不知道慢在哪」。
- **执行日志**：`run_log` 有长度上限、执行记录每任务只保留最近 20 条（`task_execution_history_limit`）；不要把大对象塞进去。

---

## 10. 测试要求

### 10.1 放哪里

| 目录 | 定位 | 是否进 CI |
| --- | --- | --- |
| `backend/tests/` | 业务行为测试（unittest 风格，按模块组织） | ❌ 只在本地（`docker-publish.yml` 只跑 regression_tests） |
| `backend/regression_tests/` | **护栏**：分层/事务/契约/并发/幂等/端口（pytest 风格，多用标准库 `ast` 检查源码） | ✅ CI 必跑 |

规则：**必须长期生效的约束，写成 regression_tests**；只验证某次业务行为的，放 tests。

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

```bash
cd backend
# 全量（当前 509 passed, 2 skipped）
python -m pytest tests regression_tests -q

# 只跑护栏（CI 同款，最快反馈）
python -m pytest regression_tests -q

# 只跑一个文件
python -m pytest regression_tests/test_commit_ownership.py -q
```

**Windows 注意**：pytest 临时目录若指向 MSYS 路径会落到 `pytest-of-<user>` 并报权限错误，需转成 Windows 路径：

```bash
TMPW="$(cygpath -w "$(pwd)/.tmp")"; TMPDIR="$TMPW" TEMP="$TMPW" TMP="$TMPW" python -m pytest tests regression_tests -q
```

### 10.5 测不到的部分（必须人工 e2e）

真实网盘转存、Telegram 交互、搜索插件加载这三块没有自动化覆盖；改动它们时按 `scripts/e2e_smoke.md` 手工跑一遍并在 PR 里贴结果。

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
- [ ] 新增提交点/后台线程：已在 `test_commit_ownership.py` 登记并写理由
- [ ] 新增配置/开关：`settings.py` + 文档 + 双分支测试齐全
- [ ] 涉及网盘/TG/搜索插件：已按 `scripts/e2e_smoke.md` 手工验证并贴结果

---

## 附录 A：常用代码片段

### A.1 仓储函数（只碰自己的模型、不 commit）

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

### A.2 容器访问器（装配点唯一出口）

```python
# backend/app/application/container.py
def task_tags_repo():
    from app.infrastructure.persistence.repositories import task_tags
    return task_tags
```

### A.3 按 key 串行 + 单飞

```python
from app.core.runtime.keyed_locks import LRUKeyedLocks
from app.core.runtime.single_flight import SingleFlight

_locks = LRUKeyedLocks(maxsize=1024)
_flight = SingleFlight(maxsize=256)

with _locks.keyed(f"lead_in:{drive}:{account}:{path}"):
    token = _flight.run(f"token:{account}", lambda: refresh_token(account))
```

### A.4 后台工作单元（提交自己负责，并登记总账）

```python
slot = task_admission.reserve(task_id)      # 拿不到就 409/429（调用点决定怎么呈现）
def work():
    with slot:
        # from app.application.container import RunPolicy, RunTaskUseCase, db_session
        with db_session() as db:             # 后台自己开 session（container 提供）
            RunTaskUseCase(db).run(task, RunPolicy.for_trigger("scheduler"))
            db.commit()                      # 归属：background-owned
slot.start_worker(work)
```

### A.5 适配器骨架

```python
from app.infrastructure.drive.adapters.base_adapter import BaseCloudDriveAdapter


class XxxAdapter(BaseCloudDriveAdapter):
    DRIVE_TYPE = "xxx"      # 基类在 __init__ 里据此生成 self.drive_type（base_adapter.py:81）

    def init(self) -> None: ...
    def get_stoken(self, share_url: str, passcode: str | None = None) -> dict: ...
    def ls_dir(self, pdir_fid: str, max_items: int = 0) -> list[dict]: ...
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
把 import 换成 `from app.application.container import ...`；确实需要新能力（新仓储/新组件）就在 `container.py` 加访问器，而不是在路由里直连。

**Q3：测试报 `ModuleNotFoundError: No module named 'app'`？**
必须在 `backend/` 目录下跑，或显式 `PYTHONPATH=backend`。不要从仓库根跑 pytest。

**Q4：pytest 报 `PermissionError: [WinError 5]` 指向 `pytest-of-xxx`？**
临时目录路径是 MSYS 风格。按第 10.4 节的 `cygpath` 写法设置 `TMPDIR/TEMP/TMP`。

**Q5：接口变慢，按什么顺序排查？**
① 是否 N+1（queries 层查数量）→ ② 是否有长事务/未结束的只读事务 → ③ 是否连接池等待（5+10）→ ④ 是否事务里带外部 IO → ⑤ 是否大响应没走 gzip。前四项都有对应的回归测试或门禁。

**Q6：我要临时加个 `--workers=4` 提性能，可以吗？**
不可以。见第 5.1 节与 [ADR 0003](architecture/adr/0003-single-process-deployment-assumption.md)：会重复迁移/重复调度/重复转存/通知双发。
