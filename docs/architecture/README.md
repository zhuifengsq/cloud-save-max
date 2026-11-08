# 架构分层与门禁一页纸

> 适用对象：所有要改 `backend/app/` 的人。主方案见 [架构演进方案](../architecture-evolution-plan.md)（§3.1 分层规则、§3.6 事务模型、§9 阶段计划），
> 现状 → 目标目录的逐模块映射见其 [附录 A](../architecture-evolution-plan.md#附录-a现状--目标目录映射可直接当搬迁清单)，本文不复述。
>
> 当前状态：**S3 地基已落地，业务代码尚未迁移**。`app/domain/ports`、`app/infrastructure/persistence/unit_of_work.py`
> 只是新代码的落脚点与契约，不被任何现有路由 import（下一步才切换）。

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
- `app/services/` → application（混有 infrastructure，按模块逐个迁）
- `app/models/`、`app/db/` → persistence（infrastructure 子层）
- `app/extensions/{adapters,runtime,search_plugins}`、`app/thirdparty/` → infrastructure
- `app/core/` → platform；`app/main.py` → composition root

## 3. 新代码写在哪里（S3 起生效）

1. **端口协议**只写 `app/domain/ports/`（`typing.Protocol`，零实现、零 IO）；实现留在 infrastructure。
2. **事务**只写 `app/infrastructure/persistence/unit_of_work.py` 的 `transaction(db)` / `UnitOfWork`；
   presentation 与新 application 代码禁止 `db.commit()`。现有路由的 89 处 commit 按下表逐步改。
3. domain 只允许 import domain + 标准库；infrastructure 可以 import domain 端口，但不得反向 import api。
4. 跨层只走端口：触发方依赖 `TaskRunUseCasePort` / `DriveGatewayPort` / `NotifierPort` / `ClockPort`，
   不再 new 具体执行器或适配器（方案 §3.4/§3.5）。

## 4. 门禁与检查命令（仓库根目录，用 backend/.venv 的 python）

| 用途 | 命令 | 通过标准 |
| --- | --- | --- |
| 分层门禁 | `python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json` | 违规数 ≤ 预算（当前 165；33 deferred；1 cycle） |
| schema 漂移 | `python scripts/check_schema_drift.py` | 库表集合 == 模型表集合（`--ignore` 可豁免已确认遗留表） |
| 迁移预检 | `python scripts/migration_preflight.py` | 报告 current/head/pending、库体积、WAL、freelist、最近备份 |
| 备份+校验 | `python scripts/backup_db.py --keep 5` | integrity_check/quick_check 均为 ok |
| 测试 | `cd backend && python -m pytest tests regression_tests -q` | 当前基线 404 passed, 2 skipped |

预算规则：**只降不升**。新增一处违规 CI 立即红；每完成一个模块迁移就把 `.layering-baseline.json` 调低。
豁免只允许「临时 + 有 issue」，不允许改脚本的 `ALLOWED` 放宽。

## 5. 回退开关（方案 §9，已落地在 `app/core/settings.py`）

`use_unified_run_use_case` / `use_notify_queue` / `use_keyed_locks` 默认 True（新路径），
出现回归时按开关回退到旧实现；开关只作为「切换期保险」，不得长期保留两套逻辑。

## 6. 迁移期纪律

- 逻辑分层先行：**先在新包写新代码，再逐个模块搬**，不做一次性大搬迁（方案 §3.2）。
- 一次 PR 只搬一个模块，且同步下调预算；搬完的模块不得再被旧代码 import（`_compat` shim 仅短期）。
- 数据面约束（备份/回滚/schema 双真相）见 [audit-data-observability.md](audit-data-observability.md) §2/§3/§7。
- 已知限制：`check_schema_drift.py` 当前会报 10 张遗留表（plugin_*/sync_*），在迁移里显式清理前用 `--ignore` 逐表豁免。
