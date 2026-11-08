# 架构分层与门禁一页纸

> 适用对象：所有要改 `backend/app/` 的人。主方案见 [架构演进方案](../architecture-evolution-plan.md)（§3.1 分层规则、§3.6 事务模型、§9 阶段计划），
> 现状 → 目标目录的逐模块映射见其 [附录 A](../architecture-evolution-plan.md#附录-a现状--目标目录映射可直接当搬迁清单)，本文不复述。
>
> 当前状态：**S4 全部完成（6/6 批，2026-11-09）**，只剩 S5 收尾。S0–S3 全部完成：路由层 0 处裸 `db.commit()`、22 个仓储经
> `app/application/container.py` 装配、提交点登记预算 95 处、6 个端口（KeyedLock / SingleFlight / Clock / TaskRunUseCase /
> DriveGateway / RateLimiter+TokenManager）已进生产路径、schema 漂移门禁已进 CI。S4 完成「查询链路 presenter 下沉 + 响应契约
> golden」「扫码会话回收」「账号快照缓存 + 按账户限速 + 令牌刷新 single-flight」「网盘网关端口实现与装配（薄语义层）」
> 「物理搬目录 5 批」（`services/`→`application/`、`extensions/adapters/`→`infrastructure/drive/`、
> `extensions/search_plugins/`→`infrastructure/search/`，旧路径留 84 个垫片）。回归 **493 绿**、分层违规 115、环 0。
> 门禁已把 `app.application` / `app.infrastructure` 前缀登记为真实层，装配点（`main` / `container` / `factories`）单独豁免。
> 未完成：`models/``db/` 仍原地（persistence 内的迁移单列 S5）、`domain/` 实体未建、`application/queries` 未拆、垫片待一个版本后删。

## 1. 四层 + 横切层（唯一契约）

| 层 | 目标包 | 一句话职责 | 禁止 |
| --- | --- | --- | --- |
| presentation | `app/api/` + `schemas/` + `middlewares/` | 协议适配、鉴权、DTO 装配、错误码映射 | SQL/事务、直接 new 执行器/适配器 |
| application | `app/application/`（新代码） | 用例编排、**唯一事务边界**、端口调用 | ORM 查询、HTTP client、具体适配器 |
| domain | `app/domain/`（本次新增） | 实体/值对象/端口协议，纯 Python | FastAPI、SQLAlchemy、requests、任何 IO |
| infrastructure | `app/infrastructure/`（新）+ 现状 `models/db/extensions/thirdparty` | ORM、仓储、适配器、调度、通知、缓存/锁实现 | 反向 import `app/api` |
| platform | `app/core/` | 配置、日志、指标、错误类型、权限、时钟 | 依赖业务层（端口除外） |
| composition root | `app/main.py` | 唯一装配点 | 业务逻辑 |

依赖方向由 `scripts/check_layering.py` 编码为 `ALLOWED` 字典并作为 CI 门禁；规则文本以方案 §3.1 为准。

## 2. 现有目录映射（只记「现在在哪」，搬迁按附录 A）

- `app/api/`、`app/schemas/`、`app/middlewares/` → presentation（原地保留，逐步剔除业务逻辑）
- `app/application/` → application（S4-6 已从 `services/` 搬入 52 个模块，同名保留）
- `app/infrastructure/{persistence,drive,http,execution,scheduling,notifications,search}/` → infrastructure
- `app/models/`、`app/db/` → persistence（**仍在旧路径**，S5 单独一批迁入 `infrastructure/persistence/`）
- `app/services/`、`app/extensions/`、`app/thirdparty/` → 只剩兼容垫片（84 个，docstring + 显式 re-export，见 `app/_compat/`）
- `app/core/` → platform；`app/main.py` → composition root

## 3. 新代码写在哪里（S3 起生效）

1. **端口协议**只写 `app/domain/ports/`（`typing.Protocol`，零实现、零 IO）；实现留在 infrastructure。
2. **事务**只写 `app/infrastructure/persistence/unit_of_work.py` 的 `transaction(db)` / `UnitOfWork`（实现仍在 `app/db/session.py`）；
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
| 分层门禁 | `python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json` | 违规数 ≤ 预算（当前 115；63 deferred；0 cycle；329 模块） |
| schema 漂移 | `python scripts/check_schema_drift.py --ignore-file scripts/schema_drift_allowlist.txt` | `drift=0`（白名单只含 10 张已确认遗留表；`model_only` 永不豁免） |
| 迁移预检 | `python scripts/migration_preflight.py` | 报告 current/head/pending、库体积、WAL、freelist、最近备份 |
| 备份+校验 | `python scripts/backup_db.py --keep 5` | integrity_check/quick_check 均为 ok |
| 测试 | `cd backend && python -m pytest tests regression_tests -q` | 当前基线 493 passed, 2 skipped |

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
  搬目录后 `app.application` / `app.infrastructure` 前缀已登记进 `scripts/check_layering.py` 的 `LAYER_OF_PREFIX`（不再是盲区）；
  旧前缀 `app.services` / `app.extensions` / `app.thirdparty` 保留映射，因为其下只剩垫片、垫片只 import 新路径。
- 一次 PR 只搬一个模块，且同步下调预算；搬完的模块不得再被旧代码 import（`_compat` shim 仅短期）。
- 数据面约束（备份/回滚/schema 双真相）见 [audit-data-observability.md](audit-data-observability.md) §2/§3/§7。
- 已知限制：实库有 10 张遗留表（plugin_*/sync_*），已登记在 `scripts/schema_drift_allowlist.txt`；
  清理前新增表进白名单必须在 PR 说明清理计划。空库 `alembic upgrade head` 后 `drift=0` 是本轮已实测的结果。
