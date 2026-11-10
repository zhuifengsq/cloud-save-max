# Cloud Save Max 后端分层重构与演进方案

> 版本 v1 · 2026-11-08 · 作者：架构组 · 适用范围：backend/app（Python 3.13 + FastAPI + SQLAlchemy 2.0 + SQLite/WAL + APScheduler，单进程）
> 硬约束：**不新增、不升级任何后端/前端依赖**（requirements.txt 与 package.json 保持不变）。所有方案只用仓库现有能力实现。
> 配套交付：`docs/architecture/audit-api-domain.md`、`docs/architecture/audit-runtime-concurrency.md`、`docs/architecture/audit-data-observability.md`（三份专题审计）、`scripts/check_layering.py`（分层依赖门禁脚本，已可运行）。

---

## 0. 一页摘要

### 0.1 一句话结论

这不是一次重写。这个项目的分层其实**已经存在**——`api / services / extensions / models / core` 就是分层雏形；问题在于**依赖方向没有约束、横切能力没有收口、执行入口没有唯一化**。因此方案的核心是「立规则 → 收口三条关键链路 → 逐模块搬家」，而不是推倒重来。

### 0.2 体检数字（本次实测，可复现）

| 指标 | 当前值 | 含义 | 采集方式 |
| --- | --- | --- | --- |
| 模块数 / 内部 import 边 | 188 / 610 | 规模可控，适合渐进改造 | `scripts/check_layering.py` |
| 跨层违规 import | **165** | 目标架构规则下的既有违规总数 | 同上 |
| 函数内延迟 import（规避循环依赖） | **33** | 循环依赖已存在，只是被 import 语句藏起来了 | 同上 |
| import 强连通分量（环） | **1 个，27 个模块** | `app.db.base` 尾部反向 import `app.models`，牵一发动全身 | 同上 |
| 路由层 `db.commit()` | **89 处** | 事务边界在表现层，用例无法复用 | `Select-String` |
| 函数签名穿 `db: Session` | **220 处**（仅 services） | ORM 会话穿层，Active Record 风格锁死 | `Select-String` |
| 线程锁分配点 | **25 个锁对象 / 18 个文件**（含 2 把 RLock、2 个 BoundedSemaphore） | 锁各自为政，无统一抽象与生命周期 | 脚本统计 |
| 模块级可变注册表 | **19 处** | 缓存/会话/锁表各自实现，多数无淘汰 | `Select-String` |
| 裸 `except Exception:` | **250 处**（另有 223 处 `except Exception as e`） | 异常吞噬，故障不可观测；两类合计 473 处 | 脚本统计 |
| HTTP 端点 / 路由模块 | 133 / 28 | API 面已不小，契约必须冻结 | 扫描装饰器 |
| 后端测试基线 | **404 passed**（排除 2 个陈旧微博测试后） | 改造有护栏，但护栏本身要先修 | `pytest tests regression_tests -q` |
| 最大文件 | handlers.py 2364 行、cloud189_adapter.py 2302 行、baidu_adapter.py 2007 行 | 上帝模块集中在 Telegram 与网盘适配器 | `wc -l` |
| 实库表数 / ORM 元数据表数 | **37 / 26**（10 张无模型遗留表，其中 2 张有数据共 26 行） | schema 双真相：迁移、模型、测试三方不同源 | `sqlite_master` + `Base.metadata.tables` |
| 数据库体积 / 空闲页 | 5,013,504 B / 233 页（19%，`auto_vacuum=0`） | 只增不减，全仓无 VACUUM | `PRAGMA freelist_count` |
| 测试是否入库 | `backend/tests` **0 个 tracked 文件**（`.gitignore` 忽略）；CI 只跑 `regression_tests` 的 10 个 tracked 文件 | 「404 项护栏」目前只存在于本地磁盘 | `git ls-files` |

### 0.3 三个架构决策

**决策 1：先立规则、再动代码（阶段 0-1 零业务风险）。**
用 `scripts/check_layering.py` 把「谁能 import 谁」变成 CI 门禁与**只降不升的预算**（当前 165）。这一步不改任何业务代码，却能让后面每一步都不再新增耦合——没有这一步，重构必然边改边烂。

**决策 2：按「链路」而非「模块」分批评审，三条链路依次收口。**
执行链路（任务运行/预览/定时/Telegram）→ 数据访问（事务边界 / 仓储 / 短事务重试）→ 网盘与查询链路（10 家适配器 + 账号 + 令牌/限速；列表/详情/序列化/分页）。每条链路独立成期、独立上线、独立回滚，避免「大刀阔斧一次性搬迁」的经典失败模式。

**决策 3：横切能力用 5 个进程内组件替代，绝不引入新依赖。**
`CachePort`（cachetools）、`KeyedLock`（threading + LRU 淘汰）、`SingleFlight`（同 key 只跑一次）、`TaskAdmission`（已有，保留并下沉）、`TaskRunUseCase`（唯一执行入口）。明确写入 ADR：**本架构是单进程架构**；未来多 worker 化需要 Redis 等基础设施，属于另一个项目，不在本期范围。

### 0.4 分期与投入（每期可独立上线、可单独回滚）

| 阶段 | 主题 | 周期 | 主要产出 | 回滚方式 |
| --- | --- | --- | --- | --- |
| S0 | 冻结与护栏 | 1-2 天 | 修 2 个陈旧测试、分层门禁进 CI、备份脚本 + 迁移预检、tests 入库、清 `pytest-of-qiuqu` ACL 残留 | 纯新增，删除即可 |
| S1 | 逻辑分层 + 横切组件 | 1 周 | 目标包骨架、端口协议、3 个只增不删的注册表换 LRU | 单文件 flag 回退（新旧实现并存） |
| S2 | 统一任务执行入口 | 1.5-2 周 | `TaskRunUseCase`，6 个直连点收敛为 1 | `settings.use_unified_run_use_case` 开关 |
| S3 | 事务边界与仓储下沉 | 2 周 | UoW + 仓储、`retry_on_locked`、修 27 模块 import 环 | 按模块回滚（每模块一次 PR） |
| S4 | 网盘链路 + 查询链路 | 2 周 | `DriveGateway` 注册表、实例 LRU+TTL、presenter 下沉 | 每网盘一个 PR，单独回滚 |
| S5 | 收尾与固化 | 1 周 | 删除 shim、预算归零、ADR 与分层规范一页纸 | 文档与门禁，无运行时风险 |

总计约 **9-11 周**（单人全职口径；两人并行可压到 6-7 周）。**任何一期都可以停下来**：S1 结束时系统已经「不再继续腐化」，S2 结束时最危险的并发隐患已消除。

### 0.5 总验收标准（Definition of Done）

~~~text
结构：跨层违规 165 -> 0（每期只降不升）；延迟 import 33 -> <=10；import 环 1 -> 0
事务：路由层 db.commit() 89 -> <=10；TaskExecutor 直连实例化 6 -> 1；准入调用点 6 -> 1
尺寸：单文件 > 1500 行 0 个；> 800 行 <= 4 个（均为数据表驱动型适配器，见 §3.8）
行为：404 项既有测试全绿 + 新增链路级回归；OpenAPI 契约 diff 为空（除显式灰度字段）
性能：GET /api/tasks p50 不回退（当前 89-132ms 区间）、12 并发 p95 不回退；SSE 首事件 < 1s
稳定：活动执行 <= 4；409/429 语义一致；SQLite locked 错误率 < 0.1%；重复执行 0 次
数据：执行记录 running 覆盖 100%；迁移在临时库 100% 跑通；schema 漂移新增 0；DB freelist 可见
~~~

---

## 1. 现状体检：分层为什么「看起来有、实际上没有」

### 1.1 目录分层 vs 依赖分层

仓库现在的目录是**按技术角色**切的（routes / services / extensions / models），这本身没错；错在依赖方向没有被约束。用目标规则对现状打分，得到 165 条违规，分布如下：

| 违规方向 | 条数 | 典型证据 | 真实危害 |
| --- | --- | --- | --- |
| application → persistence | 60 | `app/services/audit.py:3` → `app.models.audit_log`；`app/services/drama_lead_in.py:30` → `app.models.drive_account` | 业务逻辑与 ORM 表结构绑定，改表=改业务，无法单测 |
| presentation → persistence | 38 | `app/api/routes/audit_logs.py:9` → `app.db.session`；`app/api/routes/dashboard.py:9` → `app.models.task` | 路由直接拼 SQL/事务，用例无法复用 |
| application → infrastructure | 30 | `app/services/drive_accounts.py:12` → `app.extensions.adapters.adapter_factory`；`app/services/resource_search.py:13` → `app.extensions.runtime.adapter_registry` | 业务直接 new 基础设施，换实现=改业务 |
| presentation → infrastructure | 23 | `app/api/routes/tasks.py:18-26` 同时 import `adapter_factory/base_adapter/adapter_registry/account_manager/task_scheduler/task_admission/task_executor` | 路由成了「装配中心」，最典型的上帝模块 |
| application → presentation | 10 | `app/services/share_preview_batch.py:805` → `app.api.routes.system_settings`（函数内）；`app/services/telegram_bot/actions.py:13` → `app.api.routes.tasks_share` | 依赖倒挂：服务反向依赖路由 |
| platform → persistence | 4 | `app/core/deps.py:15-18` → `app.db.session` + 三个 models | 认证依赖（横切层）直连数据库，横切层不可替换 |

**口径说明（重要，避免与审计报告冲突）**：上表 165 是**逐条 import 边**的计数（同一模块的多条 import 分别计；层对 60/38/30/23/10/4 合计 165）。若按「去重后的模块对」统计，api-domain 审计得到 168（含存疑项）。两者不是同一把尺子：**CI 门禁用逐条边口径**（更严格、只降不升），评审文档可引用去重口径但必须注明。引用时不要混用两套数字。

此外还有 **1 个 27 模块的 import 环**，根因是一行代码：

~~~python
# backend/app/db/base.py:1-7
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

import app.models   # 文件尾部反向导入：26 个模型全部注册进 Base
~~~

后果不是「跑不起来」，而是**导入任意一个模型都会连带导入全部 26 个模型**，`alembic` 自动生成、测试收集、冷启动都被放大；更糟的是它掩盖了 models 层真实的依赖关系，让「模型边界」无法评估。

### 1.2 用例编排散落：同一个业务动作有 6 个实现

「运行一个任务」这件事，在当前代码里有 6 处直连 `TaskExecutor(...).run_task(...)`。**口径说明**：3 条是活跃主链路（HTTP 手动、SSE、定时调度），2 条是衍生路径（SSE 预览、Telegram），1 条是**无人调用的死代码**（`services/tasks.py:228`，曾尝试下沉但没接线）。

| 位置 | 触发来源 | 是否走准入 | 是否审计 | 是否通知 | 行为差异 |
| --- | --- | --- | --- | --- | --- |
| `backend/app/api/routes/tasks.py:220` | HTTP POST /tasks/{id}/run | 是（:218） | 是 | 追剧任务通知 | `share_check_always_notify=True` |
| `backend/app/api/routes/tasks_stream.py:175` | SSE 运行 | 是（:155） | 否 | 通过 SSE 事件 | 自带日志队列 |
| `backend/app/api/routes/tasks_stream.py:292` | SSE 预览 | 是（:255，task_id=None） | 否 | 否 | `persist_execution=False` |
| `backend/app/extensions/runtime/task_scheduler.py:248` | APScheduler 定时 | 是（:235） | 否 | 调度器通知 | 遇库锁重试策略 |
| `backend/app/services/tasks.py:231` | （无调用方，死代码） | 是（:229） | 否 | 否 | **整段函数无任何调用方，可零风险删除——这是最便宜的收口切入点** |
| `backend/app/services/telegram_bot/actions.py:480` | Telegram 指令 | 由 `handlers.py:2345` 代持（:1503 调用） | 否 | 是 | 失败回滚语义不同 |

这正是「修改容易引发连锁 Bug」的机制性原因：**同一业务规则被复制 6 份，任何一处改动都要人工同步 5 处**。第二轮的 `task_admission` 补丁已经让 4 个入口共享限额，值得肯定，但它只是把「限额」这个横切能力抽了出来，**用例本身仍然重复**。

### 1.3 事务边界在表现层

| 位置 | `db.commit()` 数 | 说明 |
| --- | --- | --- |
| `backend/app/api/routes/` | **89** | 事务由路由决定，services 只做 `flush` |
| `backend/app/services/` | 80 commit + 54 flush/rollback = 134 次事务操作 | 时而 commit 时而 flush，调用方无从判断 |
| `backend/app/extensions/` | 16 | 运行时/适配器也会自行提交 |

配合 220 处 `db: Session` 参数穿层，结果是：**谁都能提交事务，谁都不对事务负责**。典型事故形态是「路由 commit 之前抛出异常 → 部分写入」，或者「service 内部 commit 把调用方的其他修改一起提交」。

更严重的是**事务粒度本身**：`backend/app/extensions/runtime/task_executor.py:95` 的 `run_task` 全程只 `flush`（:198/:224/:245/:388），唯一 `commit` 在调用方——`backend/app/api/routes/tasks_stream.py:176` 或 `backend/app/extensions/runtime/task_scheduler.py:262`。也就是说：**一次网盘转存（分钟级、含多次外部 HTTP）从头到尾处于同一个 SQLite 写事务内**，期间写锁不放、WAL 无法 checkpoint。这不是「commit 在路由」的风格问题，而是并发能力的硬上限。

此外存在**单请求双会话**：`backend/app/services/share_preview_batch.py:543-553` 在 `_short_tx` 里另开 `SessionLocal()` 写库（:549 commit / :552 rollback），而形参 `db` 只负责读；`backend/app/api/routes/tasks_stream.py:135`（准备）与 `:171`（worker）各开一个 `SessionLocal()`。同一请求的两段写入落在不同会话，回滚语义无法统一。

### 1.4 横切能力各自为政

- **25 处**线程锁分布在 18 个文件：`adapter_factory.py:65`、`base_adapter.py:54/59`、`drama_lead_in.py:59-60`、`tmdb_cache.py:31/33`、`proxy_image_cache.py:31`（64 把分片锁）、`share_preview_batch.py:35/40` 等，命名、粒度、失效策略各不相同。
- **19 处**模块级可变注册表（缓存/会话/锁表），其中至少 3 处有明确泄漏风险：
  - `backend/app/services/drama_lead_in.py:60` `_LEAD_IN_LOCKS` 按「网盘+账号+目录+素材名」懒创建，**只增不删**，素材名维度可累积到数千把锁；
  - `backend/app/extensions/adapters/adapter_factory.py:65` `_instance_cache` 除 `clear_cache()` 外**无淘汰**，cookie 轮换产生新 key 后旧适配器实例与连接被遗留；
  - `backend/app/services/drive_account_auth_sessions.py:22` `_SESSIONS` 依赖手工清理。
- **250 处**裸 `except Exception:`（另有 223 处带别名写法，合计 473 处）：包括 `backend/app/services/telegram_bot/actions.py:487`（通知失败就 `rollback()`，把业务写入一起回滚）。其中至少 4 处会**吞掉「落库/通知/清理」失败**（`task_executor.py:337-338`、`tasks_stream.py:195-220`、`task_scheduler.py:458-459`、`notifications/sender.py:63-81`）——这不是日志噪音，而是**数据丢失**。
- **全局锁把进程串行化**：`notifications/legacy_notify.py:741-798` 的 `send()` 持模块级 `_send_lock` 覆盖「改配置 + 发送全部渠道（含 join 线程）」，单次可达十几秒；`extensions/adapters/adapter_factory.py:149-165` 在全局 `RLock` 内构造适配器（**构造过程含网盘登录**）；`extensions/adapters/base_adapter.py:65-76` 持 `_rate_limit_lock` 期间 `time.sleep`；`aliyun_adapter.py:236-276` 在 `_token_lock` 内发起最长 30s 的 HTTP 且**无锁内二次校验**（N 个线程排队各自刷新），而 `xunlei_adapter.py:273-294` 是正确 single-flight 写法——同一个仓库两种范式。
- **缓存无界/无锁**：`middlewares/timing.py:26` 用真实请求 path 作为 metrics 键（含 ID，基数无界），`core/metrics.py:19` 的 `defaultdict` 无上限无锁；`services/media_discovery.py:18` 的 `TTLCache(maxsize=5000)` 被 `:19` 的 6 线程共享且**无锁**（对照 `services/resource_search.py:21-22` 有锁的正确写法）。

### 1.5 上帝模块与包级耦合

| 文件 | 行数 | 结构问题 |
| --- | --- | --- |
| `backend/app/services/telegram_bot/handlers.py` | 2364 | 76 个 def、50 次 `commit`、4 处自建 `SessionLocal`；指令解析 + 权限 + 展示 + 业务调用 + 线程调度混在一起 |
| `backend/app/extensions/adapters/cloud189_adapter.py` | 2302 | 单网盘协议 + 登录 + 签名 + 上传 + 分享校验全部内联 |
| `backend/app/extensions/adapters/baidu_adapter.py` | 2007 | 同上 |
| `backend/app/services/drama_lead_in.py` | 1397 | 47 个 def，混合 5 类职责：素材匹配 + 文件操作 + 锁 + 设置读取 + 自签预览令牌（:715-760） |
| `backend/app/services/tmdb_cache.py` | 997 | 缓存 + 事务 + 外部 IO + 后台任务调度 |

注意：**适配器体积本身不是罪**（10 家网盘的协议差异是真实复杂度），问题是它们同时承担了「协议实现 + 会话管理 + 限速 + 持久化」。见 §3.8 的「按变化原因拆分」策略。

### 1.6 已经完成、本轮不得回退的优化（护栏）

来自 `docs/backend-optimization-baseline.md`（P0~P5）与 `docs/backend-optimization-round2-report.md`，重构过程中必须保持：

| 能力 | 关键点 | 验证方式 |
| --- | --- | --- |
| 模型级 eager loading 已解除 | `backend/app/models/task.py:32-35` 不得改回 `lazy="selectin"` | GET /api/tasks p50 399→132ms 不回退 |
| SSE 异步生成器 | `tasks_stream.py` 的 `_SseQueue`（deque maxlen=256）不得改回同步生成器 | 断连滞留 < 1s，普通接口 p95 不受影响 |
| 选择性 gzip | 4.36MB → 71,797 字节 | `/api/tasks` 传输体积 |
| 适配器 init 去重 + 实例缓存加锁 | `adapter_factory.py` | 并发首次登录只发生一次 |
| 权限缓存 | `backend/app/core/deps.py:26-27` TTL 30s | 认证 SQL 条数 |
| 执行准入 | `task_admission` max_active=4 | 409/429 语义 |
| TMDB 短事务领取 | `services/tmdb_cache.py` + 迁移 `20261105_tmdb_refresh_claim` | 上游阻塞期间其他连接可写 |
| 图片代理出站校验 | `api/routes/media_discovery.py:121` + `services/proxy_image_cache.py` | SSRF 用例 |

### 1.7 领域规则没有归属，契约靠手工镜像

同一套业务规则存在**三份实现**：

| # | 位置 | 形态 |
| --- | --- | --- |
| 1 | `backend/app/api/routes/tasks_share.py:193-359` | 路由里 375 行的单函数，且 `:290` 反向 import 执行器私有函数 `_parse_size` |
| 2 | `backend/app/extensions/runtime/drama_executor.py:436-465` | 执行器内副本 |
| 3 | `backend/app/services/drama_share_autoupdate.py:267-279` | 换链逻辑内副本，重新解析同一批 `addition` 键 |

前端契约同样是手工镜像：`backend/app/schemas/task.py:23/:44` 的 `addition` 只是 `dict[str, Any]`（零校验），但 `frontend/src/components/tasks/DramaTaskDrawer.vue` 读了 84 处 `addition.*`、涉及 23 个键；`frontend/src/types/tasks.ts:48-93` 手工镜像 `backend/app/schemas/task.py:51-59/:87-109`，**无任何 codegen**；160 个 schema 类里 0 个使用 `from_attributes`，全部手工装配 dict。

后果：改一处字段名，后端不报错、前端静默失效——这正是「修改容易引发连锁 Bug」的第二个机制（第一个是 6 份执行副本）。

### 1.8 数据库真相与迁移风险（本轮实测）

分层之外还有一条更隐蔽的「双真相」：**代码里以为的表结构，和库里实际的表结构，不是同一个东西**。

| 观测项 | 实测值 | 证据 |
| --- | --- | --- |
| 实库表数 / ORM 元数据表数 | 37 / 26 | `backend/data/app.db`（5,013,504 B）与 `Base.metadata.tables` |
| 无模型遗留表 | 10 张：`plugin_configs`(13 行)、`plugin_definitions`(13 行)、8 张 `sync_*`(0 行) | `sqlite_master` 与元数据求差 |
| 外键约束 | `PRAGMA foreign_keys` 未开启（`backend/app/db/session.py:30-36` 只设 WAL/synchronous/busy_timeout/temp_store） | 模型里的 `ondelete=CASCADE`（`models/task_execution.py:15`、`task_lead_in.py:21`）全部失效，删任务只靠 ORM cascade（`models/task.py:35`） |
| 迁移静默跳过 | 7 处 `if table not in existing_tables: return`（如 `backend/alembic/versions/20260625_sync_tasks.py:25`）+ inspector 守卫建表（`20261106_drama_lead_in.py:20-48`） | 部分迁移在真实库上等于空操作，但版本号照常前进 |
| 测试建表方式 | 全部 `Base.metadata.create_all`（14 处，如 `backend/tests/test_task_execution_prune.py:17-19`） | 迁移缺列/多表永远测不出 |
| Alembic 渲染能力 | `backend/alembic/env.py:39-48` 无 `render_as_batch` / `compare_server_default` | SQLite 改列必须 batch 模式，否则生成的迁移跑不动 |
| 启动即自动迁移 | `backend/app/main.py:47-58`（`RUN_MIGRATIONS` 默认 `"1"`，`command.upgrade(cfg, "head")`），lifespan 第一步 `main.py:68` | 无锁、无备份、无预检、无 try/except；DB URL 仅在 `XXM_TESTING=1` 时可被环境变量覆盖（`backend/app/core/settings.py:106-112`），否则硬编码 `sqlite:///./data/app.db` |
| JSON 长字段 | 全部 `Text`，无 schema、无长度上限（`models/task.py:27-28`、`task_execution.py:19-23`、`drive_account.py:17-19`） | 实测 `tmdb_media_cache.payload_json` 单行 942 KB；`ExecutionLog.lines` 无上限（`extensions/runtime/execution_log.py:40-41`） |
| 备份 / 回滚 | 无任何备份脚本；`README.md:113-137` 只写 volume；`secrets.json` 每次启动重写（`settings.py:114-142`） | 用旧库回滚会让 `refresh_tokens` 全失效；drop 表类迁移的 downgrade 只建空表（`20261028_drop_openlist_settings.py:25-42`） |

三个直接结论：

1. **自动迁移 + 硬编码 DB URL 是组合风险**：一次误跑不可逆（第二轮已真实发生一次，见 `docs/backend-optimization-round2-report.md:43`），必须升级为 S0 阻断项（备份 + 预检 + 临时库演练），而不是留到 S4 工程化。
2. **schema 双真相 + 测试用 create_all**：重构改模型若不落迁移，生产下次启动直接 `OperationalError`（SQLite 不会拒绝多余列，但缺列必炸）。S3 起「改模型必须带迁移」写进 PR 模板。
3. **护栏名存实亡**：`backend/tests` 与 `docs` 都在 `.gitignore` 里（`tests/*`、`backend/tests`、`docs`），CI（`.github/workflows/docker-publish.yml:30`）只跑 `pytest regression_tests`。404 项测试是本地资产，不是团队资产——S0 必须把它变成可执行门禁（是否改 `.gitignore` 需用户确认，见 R19）。

---

## 2. 根因分析：为什么会走到今天

1. **分层按「技术角色」切，没有按「依赖方向」切。** routes/services/extensions 是「代码放哪儿」的分类，不是「谁能依赖谁」的契约。没有契约，`services` 迟早会 import `extensions`（现 30 处），`extensions` 又会反向 import `services`（现 23 处），最后靠函数内延迟 import（33 处）续命。
2. **ORM 模型直接充当领域模型（Active Record 风格）。** `models/*` 被 services 引用 60 次、routes 引用 38 次，领域知识（任务状态机、分享链接规则、追剧集数匹配）没有独立表达位置，只能寄生在 service 函数与路由分支里。
3. **Session 作为参数穿层（220 处）。** 事务的「开始/提交/回滚」没有归属，只能由最外层的路由顺手 commit（89 处），于是「用例」= 「一个路由函数」，无法被定时器、Telegram、CLI 复用——这正是 6 份执行副本的由来。
4. **缺少端口（Port）概念。** services 直接 `from app.extensions.adapters import adapter_factory`（30 处），业务与「网盘怎么访问」硬绑定；想加一家新网盘，要同时改业务层。
5. **缺少统一的执行/调度抽象。** `TaskExecutor` 是「一个类干完整条链路」，于是每个触发方都得自己复刻：取任务→准入→执行→提交→审计→通知。
6. **缺少横切组件层。** 锁、缓存、single-flight、重试、异常策略各写一遍（25 把锁 / 19 个注册表 / 250 处裸 except），没有统一的 TTL、上限、淘汰与观测。
7. **线程池与连接池双饥饿（被误诊为「接口慢」）。** 连接池只有 15（`db/session.py:18-22`：pool_size=5 + max_overflow=10），而长任务端点（`tasks.py:216-231` 等 4 处同步 def）会把整个任务连同请求级事务一起钉在 anyio worker 线程上：4 个活动任务 + 后台 job 就占掉约 1/3 连接，剩余连接在排队时 anyio token 又被占住——表象是「接口变慢」，实质是两池互相等待。这条是「并发与稳定性隐患」的物理解释，也是 §4.1 必须先把长任务后台化的原因。

这 7 条是「症状清单」，也是阶段 S1~S4 的设计目标：S1 解决 6（横切组件），S2 解决 5 与 7（统一执行 + 长任务后台化），S3 解决 3（事务归属），S4 解决 2 与 4（ORM 与端口），全程由 S0 的规则门禁保证 1 不再恶化。

---

## 3. 目标架构设计

### 3.1 四层 + 一个横切层的依赖规则（唯一契约）

| 层 | 目标包（完成态） | 职责 | 允许依赖 | 明确禁止 |
| --- | --- | --- | --- | --- |
| 表现层 presentation | `app/api/` | 协议适配、入参校验（Pydantic schema）、鉴权/权限、DTO 装配、错误码映射 | application、domain、platform | SQL/事务/`Session`、直接 `new TaskExecutor`、直接 import `extensions` |
| 应用层 application | `app/application/` | 用例编排、**事务边界**、准入/幂等/重试策略编排、端口调用 | domain、platform、仅端口（`domain/ports`） | ORM 查询构造、`select()`、HTTP client、具体适配器 |
| 领域层 domain | `app/domain/` | 实体/值对象/领域服务/端口协议；纯 Python，可独立单测 | domain + 标准库 | FastAPI、SQLAlchemy、requests、任何 IO |
| 基础设施层 infrastructure | `app/infrastructure/`（现状 `models/`、`db/`、`extensions/`、`thirdparty/`） | ORM 映射与仓储实现、网盘适配器、调度、通知、外部 HTTP、缓存/锁实现 | domain（端口）、application（仅端口）、platform | 反向 import `app/api` |
| 横切层 platform | `app/core/`（保留名）+ 新增 `app/core/runtime/` | 配置、日志、指标、错误类型、权限、时钟、缓存/锁/准入组件 | 自身 | 依赖业务层（除端口协议） |
| 组合根 composition root | `app/main.py` | 唯一装配点：创建实例、注册中间件/路由/生命周期 | 全部 | 业务逻辑 |

**规则的执行方式**：`scripts/check_layering.py` 把上表编码为 `ALLOWED` 字典，在 CI / pre-commit 中执行 `python scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json`。**不需要任何新依赖**（标准库 `ast` 实现，188 个模块全量扫描 < 1s）。

### 3.2 第一步：逻辑分层先行（物理目录不动，零业务风险）

阶段 S1 只做四件事，**不移动任何文件**：

1. 引入 `app/domain/`、`app/application/`、`app/core/runtime/` 三个新包（新代码只能写在里面）；
2. 把现有包**映射**到目标层（脚本已内置该映射，见下表）；
3. 用「预算只降不升」冻结现状：`backend/.layering-baseline.json` 记录 `violations=165 / deferred=33 / cycles=1`，CI 只校验「不新增」；
4. 把三处「只增不删」的注册表（`_LEAD_IN_LOCKS`、`_instance_cache`、`_SESSIONS`）替换为 LRU 组件（这是 S1 唯一动到存量代码的地方，加 flag 可回退）。

| 现有包 | 映射层 | 处理策略 |
| --- | --- | --- |
| `app/api/`、`app/schemas/`、`app/middlewares/` | presentation | 原地保留，逐步剔除业务逻辑 |
| `app/services/` | application（混有 infrastructure，需拆分） | 按模块逐个迁移到 `application/` 或 `infrastructure/` |
| `app/models/`、`app/db/` | persistence（infrastructure 子层） | 原地保留，仅允许 infrastructure 引用 |
| `app/extensions/adapters`、`runtime`、`search_plugins`、`sync_plugins` | infrastructure | 原地保留，逐步提供端口实现 |
| `app/thirdparty/` | infrastructure | 原地保留，包一层端口 |
| `app/core/` | platform | 原地保留，新增 `runtime/` 子包 |
| `app/main.py` | composition root | 保留唯一装配职责 |

> 这是整个方案能安全落地的关键机制：它**允许存量脏代码存在，但禁止新增**。平均每次 PR 把预算往下调 5%，四个月内自然清零，而不是靠一次性大重构。

### 3.3 完成态目录树（S5 目标，物理迁移按模块分批完成）

~~~text
backend/app/
├── main.py                          # 组合根：装配 + 生命周期（保留 app.main:app 入口不变）
├── core/                            # 横切层（platform）
│   ├── settings.py  logging.py  metrics.py  instrumentation.py
│   ├── errors.py  exception_handlers.py  permissions.py  security.py
│   └── runtime/                     # 【新增】进程内横切组件
│       ├── cache.py                 #   CachePort + TTLCache/LRUCache 实现
│       ├── locks.py                 #   KeyedLock（LRU 淘汰） + SingleFlight
│       ├── admission.py             #   由 extensions/runtime/task_admission.py 下沉
│       └── clock.py
├── domain/                          # 领域层（纯 Python，零框架）
│   ├── task/                        #   TaskSpec / TaskState / RunTrigger / RunOutcome / 状态机
│   ├── drive/                       #   DriveType / DriveCapability / ShareLink / TransferPlan
│   ├── drama/                       #   EpisodeNumber / MaterialMatcher / LeadInPlan
│   └── ports/                       #   Protocol：仓储、网关、缓存、锁、通知、时钟
├── application/                     # 应用层（用例 + 事务边界）
│   ├── use_cases/                   #   run_task.py（唯一执行入口）/ run_preview.py / sync_drama.py ...
│   └── queries/                     #   读模型查询（返回 DTO，不含 ORM 对象）
├── infrastructure/                  # 基础设施层
│   ├── persistence/                 #   models/（原 app/models）、repositories/、session.py、uow.py
│   ├── drive/                       #   adapters/（原 10 家适配器）、factory.py（唯一注册表）
│   ├── scheduling/                  #   原 extensions/runtime/task_scheduler.py
│   ├── notifications/               #   原 services/notifications/ + telegram 发送侧
│   └── http/                        #   原 thirdparty/
├── api/                             # 表现层
│   ├── deps.py  errors.py
│   ├── presenters/                  #   原 services/task_serialization.py
│   └── routes/                      #   133 个端点
└── _compat/                         # 迁移期 shim（旧路径 re-export，S5 删除）
~~~

**为什么分两步（逻辑 → 物理）**：物理搬迁会改动 `alembic/env.py`、`Dockerfile`、`uvicorn app.main:app`、测试导入路径，风险集中在「看不见的地方」。逻辑分层先行可以用 1 天建立规则，之后每次搬迁只影响一个模块，`_compat/*.py` 保证旧 import 路径继续可用（`from app.services.tasks import run_task` 依旧能工作）。

### 3.4 五个核心端口（用 `typing.Protocol`，标准库，零依赖）

`Protocol` 的好处是**实现类不需要继承**：10 家网盘适配器一行都不用改就能满足契约；测试里用假对象直接替换，不需要 mock 框架。

~~~python
# app/domain/ports/drive.py
from typing import Protocol, Sequence

class RemoteEntry(Protocol):        # 只读视图，避免把适配器内部结构泄露给业务层
    name: str
    is_dir: bool
    size: int

class DriveGateway(Protocol):
    drive_type: str
    def list_dir(self, path: str) -> Sequence[RemoteEntry]: ...
    def transfer(self, share: ShareLink, target_dir: str) -> TransferResult: ...
    def check_share(self, share: ShareLink) -> ShareStatus: ...

# app/domain/ports/cache.py
class CachePort(Protocol):
    def get_or_set(self, key: str, factory: Callable[[], T], ttl: float | None = None) -> T: ...
    def invalidate(self, prefix: str) -> int: ...

# app/domain/ports/locks.py
class KeyedLockPort(Protocol):
    def acquire(self, key: str, timeout: float | None = None) -> ContextManager[None]: ...

class SingleFlightPort(Protocol):
    def run(self, key: str, factory: Callable[[], T]) -> T: ...   # 同 key 并发只执行一次
~~~

| 端口 | 解决问题 | 现有替代物（迁移期可原地实现） |
| --- | --- | --- |
| `TaskRepository` / `ExecutionRepository` | 消除 60 处 application→models 直连 | 先包一层薄仓储，内部仍用现有 `select()` |
| `DriveGateway` | 消除 30 处 application→infrastructure 直连 | 10 家适配器已实现同名方法，直接满足 Protocol |
| `CachePort` | 统一 12 处手写缓存（TTL/上限/淘汰） | `cachetools.TTLCache`（已在依赖中） |
| `KeyedLockPort` / `SingleFlightPort` | 替换 `_LEAD_IN_LOCKS`、`_instance_cache`、64 分片锁 | `threading.Lock` + `OrderedDict` 实现 LRU 淘汰 |
| `Notifier` / `Clock` | 让通知与时间在测试中可替换 | `notifications.send` / `datetime.now` |

### 3.5 统一执行入口 `TaskRunUseCase`（收益最大的一步）

目标：把 §1.2 的 6 份执行副本收敛为 1 份，触发方只做「调用 + 结果映射」。

~~~python
# app/application/use_cases/run_task.py
class RunTaskUseCase:
    def __init__(self, uow_factory, executor_factory, admission, audit, notifier, clock):
        ...

    def execute(self, task_id: int, trigger: RunTrigger) -> RunOutcome:
        with self.admission.reserve(task_id):                  # 1. 准入（唯一位置）
            with self.uow_factory() as uow:                    # 2. 短事务：只读任务快照
                spec = uow.tasks.snapshot(task_id)             #    事务结束即释放
            result = self.executor_factory(spec).run(spec)      # 3. 外部 IO 在事务外
            with self.uow_factory() as uow:                    # 4. 短事务：落执行记录 + 审计
                uow.executions.save(spec, result)
                uow.audit.write(action="task.run", outcome=result)
            self.notifier.task_finished(spec, result, trigger)  # 5. 通知走端口，失败不回滚业务
            return result
~~~

| 触发方 | 改造后职责 | 代码量变化 |
| --- | --- | --- |
| `api/routes/tasks.py` | 解析请求 → `use_case.execute(id, RunTrigger.MANUAL)` → `presenter.execution(outcome)` | 约 -30 行 |
| `api/routes/tasks_stream.py` | 只保留 `_SseQueue` 推送适配，执行交给用例 | 约 -40 行 |
| `extensions/runtime/task_scheduler.py` | `with retry_on_lock(): use_case.execute(id, RunTrigger.SCHEDULE)` | 约 -25 行 |
| `services/telegram_bot/actions.py` | 直接调用用例，删除自建通知/回滚逻辑 | 约 -20 行 |
| `services/tasks.py` | **删除重复的 `run_task`**，改为 shim 转发（保留旧 import 兼容） | 约 -10 行 |

**行为差异的处置**：`share_check_always_notify`、`persist_execution` 这类参数改为 `RunTrigger` 的策略字段（`RunPolicy`），由用例按触发来源决定，**不再由调用方各传各的**。这是「消除连锁 Bug」的直接手段。

### 3.6 事务与一致性模型

**规则 1：一个用例一个事务，事务边界只在 application 层。** 表现层禁止 commit；仓储只做 `flush`；`UnitOfWork.__exit__` 负责 commit/rollback。

**规则 2：外部 IO 一律在事务外。** 已有正面先例（第二轮 TMDB 短事务）：领标记（短事务）→ HTTP（无事务）→ 回填（短事务）。该模式推广到：网盘转存、Telegram 发送、图片代理回源、搜索插件调用。

**规则 3：SQLite 写竞争用「短事务 + 有界重试」而不是加锁。** `backend/app/db/session.py` 已有 `is_sqlite_locked_error(exc)` 判定函数，但**当前无人调用**；把它接成装饰器 `retry_on_locked(attempts=3, backoff=0.05*2^n)`，只包裹**幂等的数据库操作**，绝不包裹网盘副作用。

**规则 4：执行记录必须有 `running` 态。** 现状是「只在末尾落库」（`backend/app/extensions/runtime/task_executor.py:373-388`），进程被 kill 就完全没有记录；SSE 异常路径还会伪造 `id=0` 且不落库（`backend/app/api/routes/tasks_stream.py:195-220`）。改为：用例开始时短事务 `insert(running)` → 执行期只更新日志缓冲 → 结束时短事务 `update(success/failed)`。这也是让「任务成功率」指标第一次可信的前提。

**规则 5：禁止旁路 Session。** 一个用例只允许一个 Session 作用域。现状存在同一请求两条会话：`backend/app/services/share_preview_batch.py:543-553` 的 `_short_tx` 自建 `SessionLocal`（`:549` commit / `:552` rollback）而形参 `db` 只读；`backend/app/api/routes/tasks_stream.py:135/:171` 各开一次。旁路会话会绕过 UoW 的事务与回滚语义（这是 `database is locked` 的另一条来源）。

**规则 6：跨事务的外部副作用必须有幂等键与「事实源」。** 转存是外部副作用，重试不安全。现状 `backend/app/extensions/runtime/task_scheduler.py:266-273` 只对 `OperationalError` 重试，`:268` 的注释自认 cloud writes may already have succeeded——一旦重试就是重复转存。S2 之前必须先定：幂等键（`task_id + share_link + 目标目录`）、事实源（以网盘侧「已存在/已转存」查询为准，而非本地记录），并且只对「确定未写出」的错误重试。

**规则 7：禁止长读事务。** `backend/app/api/routes/tasks.py:220-231` 在请求级事务里跑完整任务，长事务会阻碍 WAL checkpoint，`-wal` 随任务时长膨胀——这类问题短任务压测永远测不出。凡事务跨越外部 IO 或长循环，一律拆三段式。

**规则 8：反序列化与日志必须有上限。** `backend/app/services/task_serialization.py:31/56/86-87` 四处裸 `json.loads` 无 `try/except`，坏数据直接 500；`backend/app/extensions/runtime/execution_log.py:40-41` 的 `lines` 无上限原样返回。统一用 `safe_json_loads(value, default)`，并给 `run_log` 设上限（如 500 条 / 64 KB），超出截断并标记。

**规则 9：提交重试必须校验「会话没有被数据库自己回滚」。** 这条是本轮实测得出的硬规则。SQLite 锁冲突发生在 `flush` 阶段时，SQLAlchemy 会**自动回滚整个事务并把 session 置为不可用**；此时若外层包装器只是「再 commit 一次」，第二次 commit 提交的是一个**空事务并成功返回**——调用方以为写成功了，实际一行都没落库（详见 §10.7，已复现）。因此 `retry_on_locked` **不能**用来包单个 `commit()`：重试必须从用例入口重新执行，或者像 `commit_with_locked_retry()` 那样，在重试前校验 `db.in_transaction()` 仍为真且 `db.is_active` 仍为真，任一不满足就**抛原始锁错误**（宁可变 5xx，也不能静默丢数据）。

| 反模式（现存） | 后果 | 替代 |
| --- | --- | --- |
| 事务内发起外部 HTTP（`tmdb_cache.py` 旧路径、部分适配器） | 持写锁等网络，报 `database is locked` | 三段式短事务 |
| service 内部 `db.commit()` 提交调用方修改 | 部分写入 / 回滚不彻底 | UoW 统一提交 |
| 通知失败 `db.rollback()`（`telegram_bot/actions.py:487`） | 业务写入被回滚 | 通知失败只记录，不回滚 |
| 路由层直接 `db.commit()`（89 处） | 用例无法组合、无法重试 | 提交权上移到用例/UoW |
| 旁路 `SessionLocal()`（`share_preview_batch.py:543-553`、`tasks_stream.py:135/:171`） | UoW 回滚失效、写锁范围失控 | 一个用例一个会话作用域 |
| 长读事务内跑完整任务（`tasks.py:220-231`） | WAL 膨胀、checkpoint 被阻塞 | 三段式：读快照 → 事务外执行 → 短事务落库 |
| 执行记录只在末尾写入（`task_executor.py:373-388`） | 进程被杀即零记录，成功率不可信 | 开始 `insert(running)`，结束 `update` |
| 外部副作用无幂等键（`task_scheduler.py:266-273`） | 重试 = 重复转存 | 幂等键 + 网盘侧事实源 |
| 锁冲突后「再 commit 一次」（旧 `transaction()`） | **静默丢数据**：flush 已回滚，重试提交空事务并成功 | `commit_with_locked_retry`：两守卫校验，失败即抛 5xx |

### 3.7 缓存 / 锁 / 异步的规范（一页表格，团队照此执行）

| 场景 | 组件 | 键设计 | 上限与淘汰 | 失效策略 | 观测 |
| --- | --- | --- | --- | --- | --- |
| 权限/角色查询 | `CachePort`（TTL 30s，已有） | `perm:{user_id}` | 1024 条 LRU | 写权限时显式 invalidate（调用点已有） | 命中率 counter |
| 网盘适配器实例 | `CachePort`（TTL 180s 可配） | `drive:{type}:{account}:{mode}:{cfg_hash}` | 128 条 LRU + `close()` 钩子 | TTL + cookie 轮换时主动 invalidate 旧键 | 实例数 gauge |
| 追剧引流互斥 | `KeyedLockPort` | `leadin:{drive}:{account}:{dir}:{material}` | 512 条 LRU（**替换当前只增不删**） | 随 LRU 淘汰 | 等待时长 histogram |
| 刷新/回填去重 | `SingleFlightPort` | `refresh:tmdb:{type}:{id}:{lang}` | 随调用结束释放 | 异常也释放（finally） | 合并次数 counter |
| 图片代理 | `CachePort` + 分片锁保留 | `img:{sha1(url)}` | 磁盘配额（已有） | 304/ETag 语义不变 | 命中率 |
| 任务执行准入 | `TaskAdmission`（已有，下沉到 `core/runtime/`） | task_id / None | `max_active=4`（可配） | 任务真正结束才释放 | 活动数/拒绝数 gauge |
| 后台任务调度 | `APScheduler`（已有） | job id | `max_instances=1`（已有） | `misfire_grace_time=60` | 执行耗时/失败数 |

**异步处理的统一口径**：本项目是**单进程 + 同步 SQLAlchemy**，因此：

- CPU/IO 混合的长任务 → `threading.Thread`（daemon）+ 准入（现状正确，保留）；
- 需要推送的实时输出 → `asyncio.Queue` + `call_soon_threadsafe`（现状 `_SseQueue` 正确，保留）；
- 周期任务 → APScheduler（保留）；
- **不引入** Celery/RQ/消息队列（依赖冻结）。需要「可恢复的异步」时，用「数据库表 + APScheduler 轮询」实现（现有 `audit_log_scheduler` 即此模式）。

### 3.8 适配器拆分策略（回答「2000 行文件怎么办」）

不要为了「行数好看」而拆。原则：**按变化原因拆**。

| 拆出物 | 触发条件 | 目标 |
| --- | --- | --- |
| `*_protocol.py`（签名/加密/请求构造） | 同一家网盘有 3 个以上签名算法 | 纯函数，可单测 |
| `*_session.py`（登录/令牌/刷新） | 已有 `_token_lock` 的（aliyun_adapter.py:223、xunlei_adapter.py:174） | 统一到 `TokenManager` 组件 |
| `*_mapper.py`（响应 → `RemoteEntry`） | 响应字段 > 15 个 | 映射表驱动，减少分支 |
| `base_adapter.py` 的限速/重试 | 已在基类（保留） | 抽为 `RateLimiter` 组件，可配置 |

**验收方式不是行数，而是**：新增一家网盘只改 1 个新文件 + 1 处注册（当前 `ADAPTER_MAP` 硬编码 + `URL_PATTERNS`），且不改任何 `services/`。

---

## 4. 三条核心链路的重构设计（before / after）

分期的依据不是「目录好不好看」，而是**链路**。选这三条是因为：它们覆盖了全部 165 处分层违规中的 118 处，且是并发/稳定性问题的高发区。

### 4.1 链路一：任务执行链路（收益最高，风险最集中）

覆盖入口：`POST /api/tasks/{id}/run`、`GET /api/tasks/{id}/stream`（SSE）、APScheduler 追剧调度、Telegram `/run`。

| # | 现状步骤（证据） | 问题 | 重构后步骤 |
| --- | --- | --- | --- |
| 1 | 路由直接 `reserve()`（tasks.py:218、tasks_stream.py:155/255、task_scheduler.py:235、services/tasks.py:229、handlers.py:2345） | 6 处准入调用（含 1 处 `reserve(None)` 预览预演），参数各写各的 | 路由/触发方只构造 `RunTrigger`，交给 `RunTaskUseCase` |
| 2 | 每条路径各自 `new TaskExecutor(db)`（6 处：tasks.py:220、tasks_stream.py:175/292、task_scheduler.py:248、services/tasks.py:231、actions.py:480） | 6 份编排副本，行为分叉（`share_check_always_notify` 只有 tasks.py:220 传 True） | `executor_factory(spec)` 由 `RunPolicy` 决定行为 |
| 3 | 准入通过后 `daemon Thread` 启动（`task_admission.start_worker`） | 进程退出丢任务、无优雅停机 | 保留 daemon 线程，但增加 `shutdown` 事件 + 在飞任务登记表，lifespan 关闭时等待上限（如 10s） |
| 4 | **执行期不提交**：`run_task` 全程只 `flush`（`task_executor.py:198/:224/:245/:388`），commit 在调用方 `tasks_stream.py:176` 与 `task_scheduler.py:262` | 整个网盘转存（分钟级）处于同一个 SQLite 写事务：持写锁、阻塞 WAL checkpoint、失败只能整体回滚 | 三段式：短事务读任务快照 → 事务外执行（日志走内存队列/独立短事务）→ 短事务落执行记录 + 审计 |
| 5 | 通知在 `except` 里 `db.rollback()`（actions.py:487） | 业务写入被回滚 | 通知失败只记 `notification_failed`，不回滚 |
| 6 | SSE 断开只停推送、线程继续（原设计如此，正确） | 需明确文档化 | 保留；`RunOutcome` 增加 `client_disconnected` 标记用于观测 |
| 7 | SSE 收尾顺序固定为 `tasks_stream.py:176` commit → `:177` refresh → `:185-194` 推 done | 若把 commit 下沉到用例却不保证顺序，前端会收到「未落库」的任务 | 用例返回后再推事件，顺序 commit→refresh→done 用回归测试锁死 |
| 8 | 幂等缺口：调度器只对 `OperationalError` 重试（`task_scheduler.py:266-273`，`:268` 注释自认 cloud writes may already have succeeded） | 重试 = 可能重复转存 | S2 之前先落幂等键与网盘侧事实源（§3.6 规则 6），否则不做自动重试 |

**验收标准**：

- 四条入口跑同一个任务的日志序列一致（新增 `regression_tests/test_run_task_use_case.py` 用 fake executor 断言调用顺序 5 步）；
- `grep -rn "TaskExecutor(" backend/app` 结果仅剩 1 处（`executor_factory`）；
- 准入调用点仅剩 `run_task.py` 1 处；
- 现有 404 项测试全绿（`tests` 299 + `regression_tests` 105，排除 2 个陈旧微博测试）+ 4 条入口的 e2e 冒烟全部通过；
- 任务开始后 1s 内即可查到 `running` 执行记录；
- 「触发方 × 行为」真值表（通知 / 执行记录 / 分享校验 / 预览）评审通过并被 e2e 覆盖。

### 4.2 链路二：网盘账号与适配器生命周期链路

| # | 现状（证据） | 问题 | 重构后 |
| --- | --- | --- | --- |
| 1 | `DatabaseAccountManager.__init__` 每次实例化全表 `select(DriveAccount)`（account_manager.py） | 每条执行链路都多一次全表查询 | 账号快照走 `CachePort`（TTL 30s）+ 写账号时 invalidate |
| 2 | `adapter_factory._instance_cache` 无淘汰（:65），键含 `md5(config)[:16]`，cookie 轮换产生新键 | 旧实例永久驻留，`__init__` 里的登录/令牌全部泄漏 | 128 条 LRU + TTL 180s + 淘汰时调用 `close()` |
| 3 | `base_adapter._rate_limit_lock` 是**实例级**但实例被多线程共享，且 `sleep(0.05~0.10)` 全局串行 | 多账号并发退化成串行 | `RateLimiter(token_bucket)` 按 `(drive_type, account)` 维度，可配置速率 |
| 4 | 各适配器自建 `_token_lock`（aliyun:223、xunlei:174），无统一刷新语义 | 令牌过期风暴 | `TokenManager` 组件（SingleFlight 刷新） |
| 5 | `drive_account_auth_sessions._SESSIONS` 模块级字典（:22）只增不删 | 扫码登录会话泄漏 | `KeyedLockPort` + 会话 TTL（10 min）自动清理 |
| 6 | `ADAPTER_MAP` + `URL_PATTERNS` 硬编码在 factory | 新增网盘要改 factory | `register(drive_type, patterns, factory)` 装饰器注册表，配合 ports/entry 校验 |

**验收标准**：新增一家网盘只新增 1 个文件 + 1 行注册（用假的第 11 家网盘做验收测试）；同一账号连续 100 次取实例只构造 1 次；cookie 轮换后旧实例在 180s 内被关闭。

### 4.3 链路三：查询/读模型链路（低风险、见效快，作为试点）

| # | 现状（证据） | 问题 | 重构后 |
| --- | --- | --- | --- |
| 1 | 路由直接 `from app.models import ...`（38 处 presentation→persistence），如 `routes/audit_logs.py:9`、`routes/dashboard.py:9-10` | 表结构变化直接破接口 | 路由只依赖 `application/queries` 的 DTO |
| 2 | ORM → Pydantic 装配在 `services/task_serialization.py` | 视图逻辑落在 service 层 | 移到 `api/presenters/`（纯函数，无 DB） |
| 3 | 路由里 `db.commit()` 89 处、`select` 仅 14 处 | 「事务在路由、SQL 在下层」，边界割裂 | 读查询改为「一个 query 函数一个 Session 作用域」，路由零 commit |
| 4 | 列表接口曾 4.36MB / p50 399ms（P0 已优化到 131.9ms） | 回归风险 | 保留 `noload` + `row_number()` 最近 3 条策略，作为 Repository 的固定实现 |
| 5 | 前端契约硬字段：executions 固定截断 3 条（`services/task_serialization.py:88`）；SSE 用 `data: ` 前缀 + 字面量 `[DONE]` 结束（`frontend/src/types/tasks.ts:299-303`）；错误体固定 `{code,message,detail}`；`X-Retryable` / `X-Silent-Toast` / 50s 超时（`frontend/src/api/http.ts:13,16-19`） | 序列化下沉时最容易被「顺手改」的隐式契约 | 先建 golden 快照（`scripts/` 下 JSON + diff 脚本），再动序列化代码 |

**一个额外契约事实**：133 个端点里只有 84 个声明了 `response_model`，其余（如 `DELETE backend/app/api/routes/tasks.py:208`）靠返回 dict 隐式序列化——响应结构在 OpenAPI 文档里是空的。presenter 下沉前必须先补显式 schema，否则「契约 diff」无从比对。

**验收标准**：`/api/tasks`、`/api/dashboard/*`、`/api/audit-logs` 的响应 JSON 与重构前**逐字节一致**（用 `scripts/` 下快照脚本对比）；`SharePreview`（`backend/app/api/routes/tasks_share.py:385-418`，23 字段）与 `TaskOut` 快照在 S3 前入库；p95 不回退超过 10%。

---

## 5. 渐进式实施路线图（S0–S5，单人约 9–11 周）

**总原则**：每期都能独立上线、独立回滚；每期结束主分支可发布；任何一期都不要求前端改动（除 S4 明确列出的兼容性新增字段）。

### S0 冻结与护栏（1–2 天，先行，无业务改动）

| 项 | 内容 |
| --- | --- |
| 目标 | 让后续每一步都有「不被破坏」的证明 |
| 改动 | ① 处置 2 个陈旧测试（`tests/test_weibo_images.py`、`tests/test_weibo_service.py` 引用了已删除的 `app.services.weibo_images` / `app.models.weibo_worker`）——修或显式 `skip`，否则整批收集失败；② 提交 `scripts/check_layering.py` + `backend/.layering-baseline.json`；③ 新增 `.github/workflows/backend-ci.yml`（跑 `pytest tests regression_tests` + `check_layering --baseline`），**不改** 现有 `docker-publish.yml`；④ 清理 `backend/pytest-of-qiuqu/`（ACL 拒绝访问的历史临时目录）；⑤ 建立 `docs/architecture/` 与 ADR 模板；⑥ **备份脚本 + 迁移预检**（`scripts/backup_db.py`：`sqlite3.Connection.backup()` 到带时间戳文件 + `PRAGMA quick_check`；迁移前校验 DB URL 实际解析路径、拒绝非预期库、跑前自动备份）；⑦ **tests 纳入版本管理与 CI**（`backend/tests` 现被 `.gitignore` 忽略、0 tracked，需用户确认后调整忽略规则，见 R19）；⑧ **两条只读门禁**（`routes` 不得新增 `import app.extensions` / `app.models`；`routes` 内 `db.commit()` 只降不升）；⑨ 删除死代码 `backend/app/services/tasks.py:228-234`（无调用方）；⑩ `scripts/check_schema_drift.py`（元数据 vs 实库表/列 diff，只报告不改库） |
| 验收 | `pytest` 404 项全绿；`check_layering --baseline` 通过；CI 在 PR 上跑通 |
| 回滚 | 纯新增文件 + workflow，删除即可 |
| 风险 | 用户工作区已有 12 个未提交修改（含 `tasks.py`、`task_executor.py`）——S0 前先 `git stash` 或让用户自行提交，避免覆盖 |

### S1 逻辑分层 + 横切组件（1 周）

| 项 | 内容 |
| --- | --- |
| 目标 | 建立规则与 3 个可复用组件，**不移动任何现有文件** |
| 改动 | ① 新建 `app/domain/ports/{cache,locks,drive,task,notify,clock}.py`（纯 Protocol）；② 新建 `app/core/runtime/{cache,locks,admission,clock}.py`，把 `task_admission.py` 逻辑搬入 `admission.py`（旧路径 shim 转发）；③ `cache.py` 用 `cachetools.TTLCache` 实现 `CachePort`，`locks.py` 用 `OrderedDict` 实现 LRU `KeyedLock`；④ 用新组件替换 3 个最痛的注册表：`drama_lead_in._LEAD_IN_LOCKS`（:60，只增不删）、`adapter_factory._instance_cache`（:65）、`drive_account_auth_sessions._SESSIONS`（:22）；⑤ 新建 `app/core/runtime/notify_queue.py`（标准库 `queue.Queue(maxsize=1000)` + 单消费者 daemon 线程 + 丢弃计数），**先只建组件不改调用方** |
| 验收 | 三处替换后，长跑 24h 的进程内存不再随素材数线性增长（用 `scripts/load_test.py` 生成 5000 条素材名验证 `len(_LEAD_IN_LOCKS)` ≤ 512）；`notify_queue` 有单测（满队列丢弃计数、消费者异常不退出）；`check_layering` 违规数**不增加**；全量回归通过 |
| 回滚 | 每个替换点各留 feature flag（`settings.use_lru_keyed_lock`），默认开启，异常可一键关闭回落旧实现 |
| 风险 | 替换锁实现时并发语义变化 → 先写并发单测（N 线程抢同 key 只允许 1 个进入） |

### S2 统一执行入口（1.5–2 周，核心一期）

| 项 | 内容 |
| --- | --- |
| 目标 | 6 份执行副本 → 1 个 `RunTaskUseCase` |
| 改动 | ① 新建 `app/application/use_cases/run_task.py` + `app/domain/task/{spec,trigger,outcome}.py`；② `TaskExecutor.run_task` 改为接收 `TaskSpec`（现接收 ORM `Task`）+ `RunPolicy`；③ 6 处调用点改为调用用例；④ 删 `services/tasks.py:229-232` 的重复实现，留 shim；⑤ 执行记录改「开始 `insert(running)` → 结束 `update(终态)`」（规则 4），并把 SSE 的 `commit→refresh→done` 顺序写成契约测试；⑥ 通知调用改为投递 `notify_queue`（不再在任务线程内同步发送），发送结果落计数 |
| 验收 | `grep -rn "TaskExecutor(" app/` 仅 1 处；`grep -rn "admission.reserve\|_admission.reserve" app/` 仅 1 处；四条入口行为一致性测试通过；404 项测试全绿 |
| 回滚 | `settings.use_unified_run_use_case`（默认开）；关闭后 6 处调用点走旧分支（旧代码保留一个版本周期） |
| 风险 | **行为分叉是最危险的点**：`share_check_always_notify=True` 只在 HTTP 手动触发时传，统一后必须在 `RunPolicy.for_trigger()` 里精确复刻；建议先写一张「触发方 × 行为开关」真值表，评审后再改代码。**自动重试的前置条件**：必须先落幂等键与网盘侧事实源（§3.6 规则 6），否则 S2 只做「不重试」的保守版本 |

### S3 事务边界与仓储下沉（2 周）

| 项 | 内容 |
| --- | --- |
| 目标 | 消除 60 处 application→persistence 违规与 89 处路由 commit |
| 改动 | ① 新建 `infrastructure/persistence/{unit_of_work,repositories/*}.py`；② 按模块把 `services/*` 的 `select()` 搬进仓储（先做 `tasks`、`drive_accounts`、`audit_logs`、`tmdb_cache` 四个高频模块）；③ `db/session.py` 的 `is_sqlite_locked_error` 接成 `retry_on_locked` 装饰器，用于幂等写；④ 修 `app/db/base.py:7` 的尾部 `import app.models`（这是 27 模块 import 环的根因）→ 改为显式在 `models/__init__.py` 侧导入或由 Alembic 单独导入 `app.models`，验证 `python -c "import app.models"` 后 `check_layering` 的 cycles=0 |
| 验收 | `check_layering`：application→persistence 违规 ≤ 20（当前 60）、cycles=0；`grep -c "db.commit()" app/api/routes/` ≤ 10（当前 89）；回归全绿；新增「事务边界」单测：用例抛出异常后 DB 无残留 |
| 回滚 | 按模块回滚（每个模块一次 PR）；仓储层是新增文件，旧 `services` 保留 |
| 风险 | 48 个既有迁移不可重写；**只新增列/表，不删不改**。任何迁移必须在临时库验证（`XXM_TESTING=1` + 临时 `DATABASE_URL`，第二轮已有踩坑记录） |

### S4 网盘链路与查询链路（2 周，可与 S3 并行）

| 项 | 内容 |
| --- | --- |
| 目标 | 网关端口化 + 读模型迁移 |
| 改动 | ① `infrastructure/drive/factory.py` 改为注册表；② 适配器实例缓存/LRU/close 落地（S1 已备组件）；③ `RateLimiter` 替换 `base_adapter._rate_limit_lock`；④ 三条查询链路按 §4.3 迁移，`task_serialization` → `api/presenters/` |
| 验收 | 快照对比 JSON 逐字节一致；`/api/tasks` p95 不回退 >10%；假第 11 家网盘验收通过 |
| 回滚 | 每个网盘一个 PR，出问题只回该网盘 |
| 风险 | 适配器是关键路径且**用户正在改动 `baidu_adapter.py`/`quark_adapter.py`/`base_adapter.py`** → S4 前必须与用户确认其改动已合并，否则先做查询链路 |

### S5 收尾与固化（1 周）

| 项 | 内容 |
| --- | --- |
| 目标 | 清零预算、删除 shim、固化规范 |
| 改动 | ① 删除 `app/_compat/` 与旧 `services` 重复实现；② `.layering-baseline.json` 预算归零并入 CI 硬门禁；③ 物理迁移剩余模块（按附录 A 映射表）；④ 补 `docs/architecture/README.md`（分层规范一页纸）+ 3 份 ADR |
| 验收 | `check_layering --fail-on 0` 无基线即通过；全部测试 + 前端类型检查通过 |
| 回滚 | 无（此时已无待回滚项） |
| 风险 | 集中删代码易漏 import → 用 `python -c "import app.main; from app.main import app"` + 全量测试把关 |

> 人力说明：以上为**单人有经验后端**的估算。若两人并行，S3 与 S4 可压缩到 3 周，总周期约 6–7 周；但 S2 建议不要并行（行为分叉风险最高）。

---

## 6. 关键技术指标（基线 → 目标 → 采集方式 → 门禁）

指标分四组。**结构指标防止腐化，性能指标防止回退，稳定性指标防止事故，质量指标防止「改完没人敢发布」**。所有指标都可用现有依赖采集（`scripts/` 脚本 + 标准库 `logging`/`tracemalloc` + 已有 `/metrics`），不引入 Prometheus client 之外的任何东西（本项目 `/metrics` 为自研实现）。

### 6.1 结构指标（架构腐化度）

| 指标 | 当前基线 | S2 目标 | 最终目标 | 采集方式 | 门禁 |
| --- | --- | --- | --- | --- | --- |
| 分层违规总数 | 165 | ≤140 | 0 | `check_layering.py --json` | CI 超基线即失败 |
| application→persistence | 60 | ≤45 | 0 | 同上（按层对） | 同上 |
| presentation→persistence | 38 | ≤25 | 0 | 同上 | 同上 |
| presentation→infrastructure | 23 | ≤15 | 0 | 同上 | 同上 |
| application→presentation（逆向） | 10 | 0 | 0 | 同上 | 硬门禁 |
| import 环数量 / 最大环模块数 | 1 / 27 | 1 / ≤10 | 0 / 0 | 同上（SCC） | 硬门禁 0 |
| 函数内延迟 import（掩盖依赖） | 33 | ≤20 | ≤10 | 同上 | 软门禁（新增即失败） |
| 路由层 `db.commit()` | 89 | ≤40 | ≤10 | `grep -c` 脚本 | 只降不升 |
| `TaskExecutor(` 直接实例化 | 6 | 1 | 1 | `grep` | 硬门禁 1 |
| 准入 `reserve` 调用点 | 6（含 1 处 `reserve(None)`） | 1 | 1 | `grep` | 硬门禁 1 |
| 路由直接 import models | 12 条 import / 8 个文件 | ≤6 条 | 0 | `grep` | 只降不升 |
| SQLite 锁重试实现套数 | 4 套互不兼容 | 1 | 1 | `grep retry_on_locked / is_sqlite_locked_error` | 硬门禁 1 |
| 执行记录 `running` 覆盖率 | 0%（只在末尾落库） | 100% | 100% | 执行记录表统计 | 硬门禁 100% |
| `except Exception` 总数 | 473（250 裸写 + 223 `as e`） | ≤400 | ≤300 | AST 扫描脚本 | 只降不升 |
| 迁移在临时库演练通过率 | 无（仅 Docker 发布内跑） | 100% | 100% | S0 预检脚本 + CI | 硬门禁 |
| schema 漂移（元数据 vs 实库） | 10 张孤儿表、0 缺表 | 只报告 | 0 新增 | `scripts/check_schema_drift.py` | 新增即失败 |
| >1000 行模块数 | 11（handlers 2364、cloud189 2302、baidu 2007…） | ≤9 | ≤3 | `wc -l` 脚本 | 只降不升 |

### 6.2 性能指标（不允许回退）

| 指标 | 当前基线（实测） | 目标 | 采集方式 |
| --- | --- | --- | --- |
| `GET /api/tasks` 单并发 p50 | 131.9 ms（P0 前 399.3） | ≤150 ms | `scripts/bench_task_list.py` |
| `GET /api/tasks` 12 并发 p50 | 1200.5 ms（P0 前 5154.9） | ≤1300 ms | `scripts/load_test.py` |
| 列表响应体（gzip） | 77,433 B（原始 4,001,263 B） | ≤100 KB | 同上 |
| Pydantic 输出编码耗时 | 5–6 ms | ≤8 ms | 现有 timing 中间件 |
| SSE 每流缓冲峰值 | 258 条（旧 6,002） | ≤300 | `_SseQueue` 长度埋点 |
| 跨线程唤醒次数/秒 | 106–108（旧 24,008） | ≤200 | 计数器 |
| 调度任务启动抖动 | 未测 | p95 ≤2 s | APScheduler 事件监听 |
| 外部 HTTP p95（网盘/TMDB） | 未测 | 按 provider 分开记录，超 5s 告警 | 已有 HTTP 埋点 |
| 适配器实例构造次数（同账号 100 次请求） | 100（无缓存命中统计） | 1 | 计数器 |

### 6.3 稳定性与并发指标（事故预防）

| 指标 | 目标 | 采集方式 | 告警阈值 |
| --- | --- | --- | --- |
| 活动任务数 | ≤4（可配） | `TaskAdmission` gauge | 持续 10 min = 4 时告警 |
| 准入拒绝（409/429）率 | <1% 请求 | 计数器 | >5% 告警 |
| `database is locked` 发生率 | <0.1% 写操作 | `retry_on_locked` 计数 | >0.5% 告警 |
| 锁表规模（`_LEAD_IN_LOCKS` 等） | 稳定（LRU 上限内） | 定时采样 len | 超上限 20% 告警 |
| 线程数（24h 稳态） | 波动 <10% | `threading.active_count()` 定时采样 | 单调上升即告警 |
| RSS 内存（24h） | 增长 <5% | `/proc` 或 `tracemalloc` 采样 | >10% 告警 |
| 优雅停机耗时 | ≤10 s | lifespan shutdown 计时 | >15 s 记录 |
| 任务成功率 | 与重构前基线持平（按 provider 分组） | 执行记录表统计 | 下降 >5% 告警 |
| 未捕获异常（500）率 | <0.5% | 现有 exception handler 计数（需补 rid） | >1% 告警 |
| `-wal` 文件体积 | 稳态 < 32 MB，任务结束后可回落 | 定时 stat `backend/data/app.db-wal` | 单调增长 / 超 100 MB 告警 |
| 连接池等待超时 | 0 次/小时 | `pool_timeout` 异常计数 | 出现即告警 |
| 通知送达率 | 与发送计数持平（失败可观测） | 发送成功/失败计数器（现 `sender.py:63-81` 吞异常） | 失败率 >1% 告警 |
| 调度漏跑（misfire/coalesce 合并） | 0 | APScheduler `add_listener` 事件 | 出现即告警 |
| DB 体积 / freelist 比例 | 5.0 MB / 19% | 定时采样 PRAGMA | 体积 30 天翻倍或 freelist >30% 告警 |
| 执行记录完整性 | 100% 任务有 running→终态两段记录 | 执行记录表校验 | 存在缺段即告警 |
| anyio 线程池占用 | 长任务 ≤4，普通请求可及时调度 | 运行时采样（token 占用/排队） | 持续占满或排队 >1s 告警 |

### 6.4 质量与交付指标

| 指标 | 当前 | 目标 | 说明 |
| --- | --- | --- | --- |
| 后端回归用例数 | 404（排除 2 个陈旧微博测试） | 每期 +15，S5 ≥500 | 现有 unittest 风格，不引入 pytest 插件 |
| 前端类型检查 | 通过 | 保持通过 | `pnpm build`（含 `vue-tsc -b`） |
| CI 时长 | 无后端 CI | ≤6 min | 新增 backend-ci.yml |
| 单次变更回滚时间 | 未知 | ≤10 min（flag 关闭或 revert 单 PR） | 每期独立 PR 策略 |
| 迁移冒烟覆盖 | Docker 发布流程内 | 100% 迁移在临时库跑通 | 沿用第二轮教训 |
| 接口契约一致性 | 无检查 | 三条查询链路 JSON 快照对比 | `scripts/` 新增 snapshot 对比 |

---

## 7. 风险登记册与规避策略

| # | 风险 | 触发信号 | 概率/影响 | 规避策略 | 兜底/回滚 |
| --- | --- | --- | --- | --- | --- |
| R1 | **行为分叉回归**（统一执行入口后，某入口不再发通知/不再写执行记录） | 各入口成功率、通知条数对比异常 | 高 / 高 | 改造前写「触发方 × 行为开关」真值表并评审；每条入口保留 e2e 冒烟；用 fake executor 断言 5 步顺序 | `settings.use_unified_run_use_case=false` 回旧分支，保留旧代码一个版本 |
| R2 | SQLite 写锁（长事务/并发写） | `database is locked` 计数上升 | 中 / 高 | 短事务三段式；`retry_on_locked` 只包幂等写；写热点合并为单事务；不开多 worker | 回退单条链路为旧事务写法 |
| R3 | 搬迁引发循环依赖/导入崩溃 | 启动即 ImportError；`check_layering` cycles 增加 | 中 / 高 | 逻辑分层先行 + `_compat` shim；一次只搬一个模块；先修 `db/base.py:7` 的 27 模块 SCC | shim 保留旧路径，删除即回滚 |
| R4 | 48 个既有迁移被动到 | 迁移历史出现异常、本地库结构漂移 | 中 / 高 | **只新增迁移，不改历史**；所有迁移在临时库 + `XXM_TESTING=1` 验证（第二轮已踩坑）；先校验 DB URL 再执行 | 用备份库恢复（`PRAGMA quick_check` 验证） |
| R5 | 线程/锁/缓存泄漏 | 线程数单调增长、锁表超上限、RSS 上升 | 中 / 中 | S1 就把三个只增不删的注册表换 LRU；24h soak 测试纳入 S1 验收 | flag 关闭新实现 |
| R6 | **单进程假设被破坏**（有人给 uvicorn 加 `--workers`/水平扩容） | 部署配置变更 | 中 / 高 | 在 `Dockerfile`、`main.py` 启动日志、README 三处显式声明「进程内状态：权限缓存/适配器实例/任务准入/KeyedLock，禁止多 worker」；启动时检测 `WEB_CONCURRENCY>1` 直接 fail-fast | 无需回滚（静态防线） |
| R7 | 权限缓存越权（TTL 30s 内权限变更不生效） | 用户投诉权限未即时生效 | 低 / 中 | 保留现有 `invalidate_permission_cache` 调用点，并补「角色变更/用户禁用」路径的 invalidate 单测 | 调小 TTL 或临时禁用缓存 |
| R8 | 可观测性缺口（重构后问题定位变难） | 线上问题无法从日志还原 | 中 / 中 | 每期同步补埋点（准入/重试/锁等待/端口调用）；统一 `run_id` 贯穿日志 | 保留旧日志格式一个版本 |
| R9 | 范围蔓延（顺手改业务逻辑） | PR 混入功能改动 | 高 / 中 | 每个 PR 只做一件事；PR 描述必须写「本期目标 + 不在范围内」；重构期业务需求走独立分支 | 拆 PR |
| R10 | **覆盖用户未提交改动** | `git status` 冲突 / 丢失修改 | 中 / 高 | S0 前让用户提交或 stash 那 12 个文件（`tasks.py`、`tasks_stream.py`、`baidu/quark/base_adapter.py`、`task_executor.py`、`generated_share_check.py`、`telegram_bot/actions.py`、4 个回归测试）；S3/S4 动这些文件前二次确认 | 从 stash/分支恢复 |
| R11 | 前端契约破坏 | `pnpm build` 类型错误；页面 500 | 低 / 高 | 查询链路要求 JSON 逐字节一致（快照比对）；新增字段只增不删不改名 | 回滚单 PR |
| R12 | 性能回退（多一层端口/仓储） | bench 超阈值 | 中 / 中 | 端口为 `Protocol` 直连实现，无反射；基准脚本纳入 PR 前后对比；超过基线 10% 阻断 | 热点路径内联 |
| R13 | **外部副作用重复**（转存已成功、记录失败，重试导致重复转存） | 网盘侧出现重复文件；执行记录与事实不符 | 中 / 高 | 幂等键（task_id+share_link+目标目录）+ 网盘侧事实源；只对「确定未写出」的错误重试（`task_scheduler.py:266-273`、`:268` 注释已自认此风险） | 关闭自动重试，人工核对 |
| R14 | **WAL 膨胀 / checkpoint 阻塞** | `-wal` 持续增长、磁盘告警、读变慢 | 中 / 高 | 禁止长读事务（`tasks.py:220-231`）；转存三段式；监控 `-wal` 体积；必要时 `wal_checkpoint(TRUNCATE)` 定时执行 | 重启进程触发 checkpoint（须在低峰） |
| R15 | **通知丢失 + 成功率失真** | 任务成功但用户没收到通知；成功率与体感不符 | 高 / 中 | 通知移出任务线程（标准库 `queue.Queue` + 单消费者线程）；发送失败计数并告警；`sender.py:63-81` 不再吞异常 | 队列落盘重试（可选，本期不做） |
| R16 | **调度静默漏跑** | 追剧任务该跑没跑，日志无异常 | 中 / 高 | `add_listener` 记录每个 job 的提交/执行/错过事件；`misfire_grace_time=60` 与 `coalesce` 的真实影响写入文档；每个任务独立 job（而非单 job 串行） | 手动补跑接口 |
| R17 | **schema 双真相**（改模型不落迁移，生产启动即崩） | 启动 `OperationalError: no such column`；元数据与实库表数不一致 | 中 / 高 | 改模型必须带迁移（PR 模板）；S0 漂移审计脚本；迁移在临时库演练；禁止 `create_all` 作为唯一验证 | 备份库回滚 + 手工补列脚本 |
| R18 | **自动迁移不可逆**（启动即 upgrade，误打库无法回退） | 启动日志出现意外 revision；数据被清理 | 中 / 高 | S0 备份脚本 + 预检（校验解析后的 DB 路径、拒绝非预期库、失败即 abort）；`RUN_MIGRATIONS` 在生产显式配置 | 用迁移前备份恢复（`PRAGMA quick_check` 验证） |
| R19 | **护栏不入库**（`backend/tests`、`docs` 被 `.gitignore` 忽略；CI 只跑 regression） | 换机器/新同事跑不出 404 项测试 | 高 / 中 | 需用户确认后调整 `.gitignore`（移除 `backend/tests`、`docs`），把测试与架构文档纳入版本管理；CI 跑全量 | 保留忽略规则时，至少用 CI 缓存/制品保存测试快照（次优） |

### 7.1 三条不可协商的底线

1. **不破坏现有功能**：每期结束主分支可发布，任一期可独立回滚；
2. **不改依赖**：不新增 `requirements.txt` / `package.json` 条目（详见 §8）；
3. **不动 48 个既有迁移**：数据库变更只做增量。

---

## 8. 依赖冻结下的取舍（「不能改变依赖」的完整落地方式）

这条约束决定了方案的技术选型：**所有能力必须用已装依赖或标准库实现**。下表是逐项对照，也是「为什么不用主流方案」的书面回答。

| 想要的能力 | 主流方案（需新依赖，**本项目不可用**） | 本项目替代方案 | 代价 / 边界 |
| --- | --- | --- | --- |
| 分布式锁 | Redis Redlock / etcd | `threading.Lock` + `OrderedDict` 实现 LRU `KeyedLock`（`app/core/runtime/locks.py`） | **仅单进程有效**；配合 R6 的 fail-fast 防线 |
| 分布式缓存 | Redis / Memcached | `cachetools.TTLCache`（已在 requirements）+ `CachePort` | 进程重启即失效（可接受） |
| 异步任务队列 | Celery / RQ / Dramatiq / arq | `threading.Thread`（daemon）+ `TaskAdmission`（现状，保留）+ 「DB 状态表 + APScheduler 轮询」做可恢复任务 | 无跨进程消费；任务不持久化在队列里 |
| 依赖注入 | `dependency-injector` / `lagom` | 显式构造函数注入 + `main.py` 组合根 | 装配代码略长（但可读、可 grep） |
| 结构化日志 | `structlog` / `loguru` | 标准库 `logging` + `JSONFormatter`（自写 20 行）+ `run_id` ContextVar | 无现成彩色链路追踪 |
| 架构守护 | `import-linter` / `pytest-archon` | `scripts/check_layering.py`（标准库 `ast`，200 行，已实现） | 规则要自己写（已写好并入库） |
| 覆盖率 | `pytest-cov` | 标准库 `trace`/`sys.settrace` 采样，或用现有 `unittest` 计数 + 「关键路径必有测试」人工清单 | 无行级覆盖率报告 |
| 指标暴露 | `prometheus_client` | 现有自研 `/metrics`（已有 token 鉴权 + SQL/HTTP 埋点） | 无直方图原生分位数，用分桶近似 |
| 链路追踪 | OpenTelemetry | 现有 timing 中间件 + `slow_request_ms=1000` + `run_id` 串联 | 无跨服务 span（单进程也不需要） |
| 异步 ORM | SQLAlchemy async + `aiosqlite` | 同步 SQLAlchemy 2.0 + 在 FastAPI 同步端点里跑（现有模式）；SSE 用 `async` 端点 + `run_in_threadpool` 启动 | 高并发长连接需线程池（现状 40 token，已够） |
| 幂等/重试框架 | `tenacity` | 自写 `retry_on_locked` 装饰器（20 行）+ 数据库唯一约束兜底 | 只支持固定退避策略 |
| 迁移工具替换 | 保持 Alembic | 保持 Alembic 1.16.1，只做增量迁移 | — |
| 异步通知 | Celery / RQ | 标准库 `queue.Queue` + 单消费者 daemon 线程 + 有界长度 + 丢弃计数 | 进程退出时队列内消息可能丢失（可接受，记录告警） |
| 数据库备份 | 云备份 / `pg_dump` 类工具 | `sqlite3.Connection.backup()`（标准库）+ `PRAGMA quick_check` + 保留 N 份轮转 | 无异地备份，仅本机磁盘；重大迁移前需人工拷走 |
| schema 漂移检测 | Alembic autogenerate + 人工比对 | `scripts/check_schema_drift.py`（标准库 `sqlite3` + 元数据反射，只读报告） | 不做自动修复，避免误改生产库 |

**关键取舍结论**：本项目的并发瓶颈不在「锁不够强」，而在「事务太长 + 锁表泄漏 + 6 份执行副本」。这三件事全部可以用标准库解决，**因此不需要任何新依赖**，用户约束与架构目标是兼容的。

**唯一需要用户确认的边界**：单进程假设。它不是「暂不支持」，而是**硬编码在架构里的**——至少 14 处进程内状态（权限缓存、适配器实例、任务准入、`_LEAD_IN_LOCKS`、TMDB single-flight、通知锁…）加上启动期 `alembic upgrade`、APScheduler、Telegram bot、各账号适配器登录，全都假设「只有一个进程」。直接加 `uvicorn --workers N` 的后果是：**重复迁移、重复调度、重复轮询、每个进程各登录一遍网盘、同一任务被多个进程同时执行**。因此本期明确不做多进程，并在三处（`Dockerfile`、`main.py` 启动日志、`README`）显式声明 + 启动时检测 `WEB_CONCURRENCY>1` 直接 fail-fast。未来若必须水平扩容，需要 Redis/消息队列，属于另一个项目。

---

## 9. 30 / 60 / 90 天落地节奏

| 阶段 | 时间 | 交付物 | 验收口径 |
| --- | --- | --- | --- |
| 第 1 个月 | D1–D2：S0；D3–D9：S1；D10–D20：S2 前半（真值表 + 用例骨架 + 2 个入口切换） | 护栏 CI、3 个横切组件、`RunTaskUseCase` 骨架 | 404 项测试全绿；`check_layering` 不新增；2 条入口行为一致性测试通过 |
| 第 2 个月 | S2 后半（剩余 4 个入口 + 删重复实现）；S3 起步（`tasks`/`drive_accounts` 两个模块的 UoW + 仓储） | 6→1 执行入口完成；2 个高频模块事务下沉；`retry_on_locked` 上线 | `TaskExecutor(` 仅 1 处；路由 commit ≤60；cycles 修复验证；`database is locked` ≤0.1% |
| 第 3 个月 | S3 收尾（`audit_logs`/`tmdb_cache`）；S4 查询链路 + 网盘注册表；S5 收尾 | 预算清零、shim 删除、ADR 与分层规范一页纸 | `check_layering --fail-on 0` 无基线通过；JSON 快照一致；前端 `pnpm build` 通过 |

### 9.1 每周固定动作（防走样）

1. **周一**：跑一次 `python scripts/check_layering.py --json`，与上周预算对比，更新 `.layering-baseline.json`（只许下调）；
2. **每个 PR**：CI 必须过（测试 + 分层预算 + 迁移冒烟）；PR 描述必须含「本期目标 / 不在范围内 / 回滚开关」；
3. **每两周**：跑 `scripts/bench_task_list.py` + `scripts/load_test.py`，与 §6.2 基线对比，回退 >10% 阻断合并；
4. **每月**：一次 24h soak（`scripts/load_test.py` 长跑），检查线程数/锁表/RSS 三条曲线。

### 9.2 里程碑判定（Go / No-Go）

| 里程碑 | Go 条件 | No-Go 处理 |
| --- | --- | --- |
| M1（S1 结束） | 24h soak 无泄漏；三处注册表 LRU 生效；回归全绿 | 只回滚新组件（flag），保留基线 |
| M2（S2 结束） | 6→1 完成；四条入口 e2e 通过；`share_check_always_notify` 真值表逐项验证 | 逐入口回滚，不整期回滚 |
| M3（S3 结束） | cycles=0；application→persistence ≤20；无 `database is locked` 新增 | 按模块回滚 |
| M4（S5 结束） | 预算清零；全量测试 + 前端构建通过；发布后 7 天无 P1 事故 | 保留 shim 一个版本再删 |

---

---

## 10. 实施进展（2026-11-08，随代码落地持续更新）

本节记录**已经落地的代码**，与 §5 路线图逐期对应；未完成项明确标注「仅地基/未接线」，避免把设计当成现状。

### 10.1 已落地（S0 + S1 + S2 + S3，含尾巴与端口/CI 收口）

| 期 | 交付 | 关键文件 | 验证 |
| --- | --- | --- | --- |
| S0 | 陈旧微博测试显式 skip（消除整批收集失败）、迁移前热备、分层门禁进 CI | `backend/tests/test_weibo_images.py`、`test_weibo_service.py`、`backend/app/db/backup.py`、`backend/app/main.py`、`.github/workflows/layering-gate.yml`、`backend/.layering-baseline.json` | 备份产物与源库逐字节一致（5,013,504 B）；skip 路径返回 None |
| S0 | 分层检查器（标准库 ast，零依赖） | `scripts/check_layering.py`（188→206 模块全量扫描 <1s；`--fail-on`/`--baseline`/`--update-baseline`/`--json`） | 基线 165 条；当前 162 条 → 门禁 exit 0 |
| S1 | key 级锁表（有上限 LRU，防锁表无界增长） | `backend/app/core/runtime/keyed_locks.py`（`LRUKeyedLocks(1024)`）；接入 `services/drama_lead_in.py:1393-1407`（素材去重）、`extensions/adapters/adapter_factory.py:161`（单飞，256） | 新增 `regression_tests/test_keyed_locks.py` 全绿 |
| S1 | 适配器实例缓存有界 + 淘汰关闭 | `adapter_factory.py:181-215`（LRU 64、`close()` 在锁外调用）、`base_adapter.py` 新增 `close()` | 并发首建只创建 1 个实例；淘汰不影响在途调用 |
| S1 | 通知队列（单消费者线程，生产者不阻塞） | `backend/app/services/notifications/queue.py`（`Queue(1000)` + 1 daemon 线程）、`legacy_notify.py:801-847`（`send_async` 灰度）、`sender.py:82-92`（`send_runtime` 接线） | 新增 `regression_tests/test_notify_queue.py`；队列满丢弃并告警、消费异常不杀线程 |
| S1 | SQLite 外键真正生效 + 锁冲突统一重试壳 | `backend/app/db/session.py:45-48`（`PRAGMA foreign_keys=ON`，实测 `foreign_key_check` 为空）、`:75-110`（`retry_on_locked(fn, attempts=5, on_retry=...)`，仅锁冲突重试） | 现有 435 项测试全绿 |
| S2 | 统一任务执行入口：6 处直连收敛为 1 个用例 + 触发方真值表 | `backend/app/application/use_cases/run_task.py`（`RunPolicy.for_trigger`：http/sse/scheduler/telegram/preview）；替换 `api/routes/tasks.py:221`、`tasks_stream.py:176/:293`、`task_scheduler.py:251`、`telegram_bot/actions.py:481`；删除 `services/tasks.py` 死代码 | 新增 `regression_tests/test_run_task_use_case.py`（11 项，含灰度回退路径） |
| S2 | **准入仍留在调用点**（架构决策，见 §10.3） | 6 处 `task_admission.reserve` 原位保留 | SSE 409/429 与旧行为逐字一致 |
| S3 地基 | 端口层（Protocol，零实现零 IO） | `backend/app/domain/ports/{cache,locks,drive,task,notify,clock}.py`（8 个 Protocol） | 仅 import `typing`，可单测 |
| S3 地基 | 事务边界（UnitOfWork） | `backend/app/infrastructure/persistence/unit_of_work.py`（`transaction(db)` / `UnitOfWork`；commit 走 `commit_with_locked_retry`） | 新增 `regression_tests/test_unit_of_work.py`（6 项：提交/回滚/锁重试/非锁不重试/**真实 SQLite 争锁不静默丢数据**） |
| S3 接线 | **首次 UoW 接线：4 个低风险端点、8 处 `db.commit()` 收口** | `backend/app/db/session.py` 新增 `transaction(db, attempts=3)`（实现放持久化层，避免新增分层违规）；`infrastructure/persistence/unit_of_work.py` 改为纯 re-export；`api/routes/{system_settings,notifications,tmdb_settings,task_templates}.py` | 路由裸 `db.commit()` 89→81（18 文件）；新增 `regression_tests/test_route_transactions.py`（3 项）；功能冒烟验证 4 端点提交持久化 + 事务内抛异常零残留 |
| S3 接线 | **首次 UoW 接线：4 个低风险端点、8 处 `db.commit()` 收口** | `backend/app/db/session.py` 新增 `transaction(db, attempts=3)`（实现放持久化层，避免新增分层违规）；`infrastructure/persistence/unit_of_work.py` 改为纯 re-export；`api/routes/{system_settings,notifications,tmdb_settings,task_templates}.py` | 路由裸 `db.commit()` 89→81（18 文件）；新增 `regression_tests/test_route_transactions.py`（3 项）；功能冒烟验证 4 端点提交持久化 + 事务内抛异常零残留 |
| S3 地基 | 运维脚本三件套 | `scripts/backup_db.py`（sqlite3 backup API + 单文件产物 + integrity_check）、`check_schema_drift.py`（ORM metadata vs 实库表，`--ignore` 白名单）、`migration_preflight.py`（current/head/pending/体积/freelist/WAL） | 对 `.tmp` 副本实跑：integrity_check=ok、drift=10（10 张遗留表）、current=head、freelist 19% |
| S3 地基 | 分层规则落地说明 | `docs/architecture/README.md`（四层 + 横切、目录映射、新代码规则、门禁命令、回退 flag） | — |
| S3 修复 | **`transaction()` 在锁冲突下静默丢数据（已复现并修复）** | `backend/app/db/session.py` 新增 `commit_with_locked_retry(db, attempts=3)`（重试前校验 `in_transaction()` 与 `is_active`，否则抛原始锁错误）、`commit(db)` 薄封装；`transaction()` 改用前者；`regression_tests/test_unit_of_work.py` 整文件重写 | 修复前：接口 200、`victim` 行数 0；修复后：抛 `OperationalError: database is locked`、`victim` 不落库 |
| S3 接线 | **`users` / `roles` / `auth` / `audit_logs` 4 个路由文件提交收口（15 处）** | `api/routes/users.py`（5 处：服务调用+审计同一事务）、`roles.py`（4 处）、`auth.py`（3 处，改用锁安全 `commit(db)` 保持「先发 token 再提交」顺序）、`audit_logs.py`（3 处：懒建设置/清空/更新调度配置） | 路由裸 `db.commit()` 60→45（13 文件）；435 passed, 2 skipped |
| S3 接线 | **`drive_accounts` 全量提交收口：20 处 audit+commit → `with transaction(db)`** | `backend/app/api/routes/drive_accounts.py`（文件内 `db.commit()` 归零；`probe` 失败/扫码未完成/签到失败的「先落库再抛」语义不变；外部 IO 仍在事务外） | 路由裸 `db.commit()` 81→60（17 文件）；435 passed, 2 skipped |
| S3 接线 | **低风险 5 个路由文件 12 处收口** | `resource_search.py`（3，事务后 `cache_clear()`）、`setup.py`（1，改 `commit(db)` 保留 503 `DB_NOT_READY` 分支）、`magic_regex.py`（2，事务后 `list_rules`）、`dashboard.py`（2，清理+审计同事务）、`cache_management.py`（4） | 路由裸 `db.commit()` 45→33；435 passed, 2 skipped |
| S3 接线 | **插件与 TMDB 缓存 16 处收口** | `search_plugins.py`（5，`_audit` 与插件写同事务，`cache_clear()` 移到提交后）、`tmdb_cache.py`（7：5 处纯 DB 改 `transaction`；刷新/刷新关联任务 2 处改为 `commit(db)`，其外部 HTTP 长读事务在 S3 尾巴专用行处理） | 路由裸 `db.commit()` 33→17；435 passed, 2 skipped |
| S3 接线 | **任务写路径 15 处收口（经用户授权动 WIP 文件，只做定点替换）** | `tasks.py`（10：post/patch/status/delete/调度配置改 `transaction`；执行/预览/分享等外部 IO 之后改 `commit(db)`，保留 `refresh`）、`tasks_stream.py`（3，保持 `commit→refresh→q.put(done)` 与 done 事件契约）、`tasks_drive.py`（1）、`tasks_share.py`（1） | 路由裸 `db.commit()` 17→2；435 passed, 2 skipped；SSE/409·429 契约未变 |
| S3 接线 | **收尾 6 处：剧情引流设置 + 媒体发现** | `system_settings_drama_lead_in.py`（4，服务内部自管事务边界，路由只负责最终落库）、`media_discovery.py`（2，TMDB 外部 HTTP 之后 `commit(db)`；读事务在 S3 尾巴专用行关闭） | 路由裸 `db.commit()` **2→0（13 文件全部归零）**；435 passed, 2 skipped |
| S3 仓储下沉 ① | **审计/权限/设置类 7 个仓储 + 依赖装配点** | 新建 `infrastructure/persistence/repositories/{audit,permissions,magic_regex,notification_settings,task_executions,task_scheduler_setting,tmdb_cache_scheduler_setting}.py`；新建 `application/container.py`（唯一装配点：函数访问器，内部延迟 import 仓储）；`services/{audit,audit_logs,audit_log_scheduler,permissions,magic_regex,notifications/settings,notifications/task_notify,task_executions,task_scheduler,tmdb_cache_scheduler}.py` 改为委托 | violations 162→149；application→persistence 55→47；438 passed, 2 skipped |
| S3 仓储下沉 ② | **资源/账号/任务查询类 6 个仓储（8 个易模块）** | 新建 `repositories/{resource_search_sources,invalid_share_links,drive_account_probe_scheduler_setting,tmdb_settings,drive_accounts,tasks}.py`；`task_executions` 增 `list_recent_adapter_snapshots`；改 `services/{api_search_sources,generated_share_check,invalid_share_links,drama_share_autoupdate,shareurl_update_logs,tmdb_settings,search_plugins,drive_account_probe_scheduler}.py` | violations 141；application→persistence 39；438 passed, 2 skipped |
| S3 仓储下沉 ③ | **账号/权限/任务/缓存 6 个新仓储 + 会话上下文** | 新建 `repositories/{roles,users,refresh_tokens,telegram_bot_session,share_preview_batch_cache,tmdb_media_cache}.py`；`roles` 增 `list_roles_by_ids`、`drive_accounts` 扩到 20 个查询函数；`container` 增 `db_session()` 上下文；改 `services/{roles,users,auth,setup,telegram_bot/session_store,telegram_bot/handlers,tasks,share_preview_batch_cache,tmdb_view,dashboard_drama,drive_accounts,resource_search}.py` | violations 141→122；application→persistence 39→20；`handlers.py` 3 处 `with SessionLocal()` 改 `with db_session()`；438 passed, 2 skipped |
| S3 仓储下沉 ④ | **收尾：`telegram_bot/actions.py` 与 `resource_search` 模型引用清零（S3 主体完成）** | `actions.py`：3 个账号挑选用例改调 `services/drive_accounts` 的 `pick_*`/`get_active_account`，任务与执行记录查询改 `tasks_repo().count_tasks/count_enabled_tasks/get_first_by_shareurls`、`task_executions_repo().get_latest_for_task/list_desc`、`drive_accounts_repo().list_enabled_names/get_account`；`resource_search` 改 `list_active_by_type/list_active_drive_types` | **violations 122→117；application→persistence 20→15（验收阈值 ≤20）**；438 passed, 2 skipped |
| S3 尾巴 | **TMDB 批量刷新不再持有长读事务（WAL checkpoint 护栏）** | `backend/app/services/tmdb_cache.py` 新增 `_close_read_transaction()` 与 `close_read_tx` 参数（默认 `False` 保持执行器旧语义）；`refresh_tmdb_detail_sync` / `refresh_linked_tasks` / `refresh_expired_cache` / `get_tmdb_*_cached` 逐层透传；`api/routes/tmdb_cache.py`、`api/routes/media_discovery.py`、`extensions/runtime/task_scheduler.py` 的只读上下文传 `True` | 新增 `regression_tests/test_tmdb_refresh_read_txn.py`（3 项：HTTP 期间调用方无读事务 / 默认保持旧语义 / 整批逐项结束读事务）；438 passed, 2 skipped |
| S3 尾巴 | **提交归属分类：103 处 → 95 处，路由层 0** | `backend/app/core/runtime/tx.py`（`end_read_tx`：platform 层、零 ORM 依赖，避免 application 顶层 import persistence）、`app/db/session.py`（重导出保持旧 import 习惯）、`services/drama_lead_in.py`（7 处只读边界 `db.commit()`→`end_read_tx`；设置写入 `flush` 后交给路由提交；10→2）、`services/tmdb_cache.py`（`_close_read_transaction` 改用它；1→0） | 441 passed, 2 skipped；violations 117、deferred 58、环 0 |
| S3 尾巴 | **提交归属预算护栏（防止提交点悄悄回流）** | `backend/regression_tests/test_commit_ownership.py`（AST 扫描 `.commit()`：路由层必须为 0；文件级预算只降不升；新文件出现提交点必须登记归属；`SessionLocal(` 旁路会话白名单） | 3 项全绿；预算 **95 处 / 11 文件** |
| S3 尾巴 | **端口接入：4 个 Protocol 有了真实实现与生产接线** | `core/runtime/keyed_locks.py` 增 `acquire/release/keyed`（满足 `KeyedLockPort`）；新建 `core/runtime/single_flight.py`（`SingleFlight`，满足 `SingleFlightPort`；`adapter_factory.py` 的 per-key 手工 `with` 改为 `SingleFlight.run`）；新建 `core/runtime/clock.py`（`SystemClock`）并经 `container.clock()` 接入 `services/share_preview_batch_cache.py` 的时间源；`domain/ports/task.py` 的 `TaskRunUseCasePort` 对齐真实 `RunTaskUseCase.run(task, policy, ...)` 签名 | 新增 `regression_tests/test_ports_conformance.py`（6 项：isinstance 结构验收 + 并发只执行一次 + 异常透传 + 时钟接线） |
| S3 尾巴 | **schema 治理进 CI（模型/迁移偏差变成可执行门禁）** | `scripts/check_schema_drift.py` 修复「`db.base` 破环后不再代为 import models 导致 model_tables=0」并新增 `--ignore-file`；新增 `scripts/schema_drift_allowlist.txt`（10 张遗留表，逐张注释）；新增 `.github/workflows/db-schema-gate.yml`（空库 `alembic upgrade head` → drift 检查 → `migration_preflight --fail-if-pending`） | 实库 drift=0（26/26）；空库跑 48 个 revision 后 drift=0、preflight pending=0，两脚本 exit 0 |

| S4-1/2 | **查询链路：响应装配从 application 下沉到 presentation，先用 golden 锁契约** | 新建 `backend/app/api/presenters/task.py`（`task_out`/`execution_out`/`scheduler_out`，纯函数零 IO）；删除 `backend/app/services/task_serialization.py`；`api/routes/tasks.py`、`api/routes/tasks_stream.py` 与 3 个测试改 import；新增 `regression_tests/test_task_presenter_contract.py`（4 项：字段顺序、截断 3 条、JSON 解析、缺省值） | 454 passed；违规 117→**115**（`application→presentation` 10→8）；基线下调到 115 |
| S4-3 | **扫码登录会话回收（内存泄漏修复）** | `backend/app/services/drive_account_auth_sessions.py`：创建时 `_purge_expired_locked` 清过期项；`_MAX_SESSIONS=512` 超限淘汰最早到期；新增 `regression_tests/test_auth_session_ttl.py`（3 项） | 3 项全绿；放弃扫码的会话不再持有 adapter 直至进程重启 |

### 10.2 量化结果（2026-11-09 实测，可复现）

| 指标 | 改造前基线 | 当前 | 命令 |
| --- | --- | --- | --- |
| 测试总数 | 404 passed / 2 收集失败 | **553 passed, 2 skipped**（+149 项新护栏；含网关端口 14、分享页 errno 6、生成后自检 9、兼容层垫片 5、追剧运行日期判定 13） | `python -m pytest tests regression_tests -q` |
| 分层违规 | 165 条（60/38/30/23/10/4） | **0 条**（S5 收尾归零；本轮新增改动仍为 0） | `python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json` |
| 模块 / import 边 | 188 / 610 | **250 / 721**（deferred 33→84；增量含仓储访问、时钟/限速/网关实现、composition root 延迟 import、S4-6 搬目录后的旧路径垫片） | 同上 |
| 路由层裸 `db.commit()` | 89 处 / 18 文件 | **0 处 / 0 文件**（13 个路由文件全部归零，89 处全收口） | `grep -rc "db\.commit()" backend/app/api/routes` |
| 全 app 提交点 | 146 处（路由 45 / 服务 80 / 扩展 16 / main 2 / scripts 2 / db 1） | **95 处 / 11 文件**：handlers 50、actions 20、scheduler 14、drama_lead_in 2、main 2、scripts 2、db+core 2、task_executor 1、telegram_bot_manager 1、share_preview_batch 1（全部登记归属，见 §10.10） | `pytest regression_tests/test_commit_ownership.py -q`；`grep -rn "\.commit()" backend/app` |
| 锁冲突下的写入语义 | **静默丢数据**（flush 已回滚，重试提交空事务并成功） | 抛原始锁错误（5xx），行不落库 | `pytest regression_tests/test_unit_of_work.py -k lock_conflict -q` |
| TMDB 批量刷新读事务 | 最多 200 次外部请求共用一个读快照（checkpoint 无法推进） | 每项外部请求前后结束调用方读事务（`close_read_tx=True`）；执行器路径默认不变 | `pytest regression_tests/test_tmdb_refresh_read_txn.py -q` |
| 环 | 1 个 SCC / 27 模块 | **0 个 SCC**（`app/db/base.py` 末尾的 `import app.models` 已删，Alembic 显式导入） | 同上 |
| 应用启动 | — | `import app.main` OK | `python -c "import app.main"` |
| application→persistence 违规 | 60 条 | **15 条**（S3 验收阈值 ≤20） | `.layering-baseline.json` 层对明细 |
| 端口实现/接线 | 8 个 Protocol、0 引用 | **5 个有实现且进生产路径**（KeyedLock / SingleFlight / Clock / TaskRunUseCase / DriveGateway）；Notifier / Cache 留 S5 | `pytest regression_tests/test_ports_conformance.py regression_tests/test_gateway_port.py -q` |
| 网盘网关端口 | `DriveGatewayPort` 无实现；分享探测写在服务里直连适配器，`get_stoken`/`get_detail` 的网盘差异靠 `if drive_type == ...` 分支 | **`infrastructure/drive/gateway.py` 实现端口**：响应归一（fid/fid_token/目录判定/size）、失败三阶段（parse/verify/read）、quark `first_page` 与 baidu `check_errno` 收在实现里；`generated_share_check._probe` 改走端口，**用户文案逐字不变** | `pytest regression_tests/test_gateway_port.py -q`（13 项） |
| 新增网盘接入成本 | 需改服务分支或网关里的 `if drive_type` 才能支持新网盘 | **假第 11 家网盘用例**：只实现适配器方法即可跑通 `list_dir`/`probe_share`，端口层零改动 | 同上 `test_fake_eleventh_drive_needs_no_gateway_change` |
| 模型/迁移偏差检查 | 无（测试用 `create_all`，从不跑 alembic） | **空库升级后 drift=0**，CI `db-schema-gate` 双脚本 exit 0；新增遗留表必须登记白名单 | `python ../scripts/check_schema_drift.py --ignore-file ../scripts/schema_drift_allowlist.txt` |
| 执行入口副本 | 6 处直连（含 1 处死代码） | **1 个用例**（`grep -rn "TaskExecutor(" backend/app` 只剩用例与执行器自身） | 见附录 B |
| 查询链路装配位置 | `services/task_serialization.py`（视图逻辑在应用层） | **`api/presenters/task.py`**（纯函数，无 IO），旧模块已删除、5 处 import 全部改到新路径 | `grep -rn "task_serialization" backend/app`（应为空） |
| 登录会话内存 | 过期项只在被访问时删除，放弃扫码的会话与其 adapter 永久驻留；无上限 | 每次创建即清过期；上限 512，超出淘汰最早到期 | `pytest regression_tests/test_auth_session_ttl.py -q` |
| 锁表增长 | 素材名维度无界 dict | `LRUKeyedLocks` 上限 1024/256 | 单测断言上限与淘汰 |
| 账号快照 | 每次任务/预览都全表查 `drive_accounts` + 逐行解析 JSON（`DatabaseAccountManager.__init__`） | **进程内 TTL 30s + 版本号/指纹双校验**，写路径主动失效；flag 关可回旧路径 | `pytest regression_tests/test_account_snapshot_cache.py -q` |
| 网盘限速 | 只约束单个 adapter 实例（缓存 TTL 180s、cookie 轮换会产生同账户多实例 → 限速失效） | **按 `(网盘类型, 账户)` 维度共用预约时间点**，不同账户互不阻塞 | 同上（`FakeLimiter` 断言排队 key 与区间） |
| 令牌刷新 | 各自持实例锁，多实例并发刷新会让后到者作废先到的新 `refresh_token`（症状=偶发「登录失效，请重新扫码」） | **同实例内 single-flight 合并**；guangya 的 401 路径带 `stale_token`，不会把已换好的新令牌再刷废 | `pytest regression_tests/test_runtime_components.py -q` |
| 提交点归属登记 | 无（散落在路由/服务/线程） | **95 处全部登记四类归属**；路由层 0；新提交点不登记即失败 | `pytest regression_tests/test_commit_ownership.py -q` |

### 10.3 已做出的五个架构决策（与 §3 的差异需知悉）

1. **执行槽（准入）不搬进用例**：初版设计把 `task_admission.reserve` 收进 `RunTaskUseCase`，实测会让 SSE 端点从「同步 409/429」变成「200 + `done{failed}` 事件」——这是前端契约变化，违反「不影响现有业务」。最终决策：用例只收敛**执行参数**，准入留在原调用点；准入的端口化放到 S2+ 单独立项。
2. **提交收口的真实边界是「事务内不放外部 IO」，不是「把 commit 换成 transaction」**：路由层 89 处已全部处理完（13 文件 0 残留），但分两种改法：①纯 DB 短事务（设置/模板/权限/审计/缓存清理等）→ `with transaction(db)`，业务写与审计同生共死；②外部 IO（网盘、TMDB HTTP、Telegram）在前的流程 → 只把最后的 `db.commit()` 换成锁安全 `commit(db)`，**不缩短事务**。任务写路径（`tasks`/`tasks_stream`）经用户授权已接：准入（`task_admission.reserve`）、SSE `commit→refresh→done` 顺序、执行期不提交、`running` 态语义**逐点对照后保持原样**。长读事务已在 S3 尾巴处理（§10.1）：TMDB 刷新/详情/演员表的只读上下文先结束调用方读事务，再进入 HTTP 与独立 writer 写回；`close_read_tx` 默认 `False`，任务执行器等可能持有未提交写入的路径行为不变。
3. **锁冲突重试的语义从「重试 commit」改为「要么安全重试、要么显式失败」**：原实现是 `retry_on_locked(db.commit)`，在 SQLite flush 阶段锁冲突时会提交空事务并假装成功（§10.7）。新实现 `commit_with_locked_retry()` 只在「会话仍处事务中且仍 active」时重试，否则抛原始 `OperationalError`；`retry_on_locked()` 保留，但语义明确为「重跑整个用例」，不得包裹单个 commit。这是「宁可 5xx 也不静默丢数据」的取舍。
4. **仓储下沉用「container 函数访问器 + 函数内延迟 import」，不动 DI、不加依赖**：`services` → `app.application.container.xxx_repo()` → 函数体内 `from app.infrastructure.persistence.repositories import xxx` → 仓储 → models。这样既把 `select()`/`db.get()` 从业务层移走，又不引入依赖注入框架，也不会让 `application` 在模块顶层 import `infrastructure` 而触发新的分层违规（延迟 import 在门禁里记为 `deferred`，单独统计）。代价是「依赖关系要读函数体才看得见」，因此所有装配只在 `container.py` 一处发生，禁止服务自己 import 仓储模块。
5. **tmdb_cache 不硬搬（显式例外）**：该模块的写路径是 `Core insert/update` + 专用 writer engine（`_writer_engines`、`_write_slots=BoundedSemaphore(2)`、`NullPool`）与「UPDATE 领标记」的并发协议，整体搬仓容易把并发语义改坏，收益（3 条边）远小于风险。决策：**只把它的只读事务关闭逻辑修好（§10.1 尾巴），仓储化留到 tmdb 专项 ADR**；剩余 3 条 `application→persistence` 边作为已知例外计入预算。

### 10.4 灰度开关（默认全开，可单独立即回退）

| 开关（`backend/app/core/settings.py`） | 默认 | 关闭后的行为 |
| --- | --- | --- |
| `use_unified_run_use_case` | True | 各调用点走 `RunTaskUseCase._legacy_execute()`（逐字复刻旧实参） |
| `use_notify_queue` | True | `send_async()` 退化为同步 `send()`，恢复旧的阻塞语义 |
| `use_keyed_locks` | True | 素材去重回退到旧的无界 dict 锁表 |
| `use_account_snapshot_cache` / `account_snapshot_ttl_seconds` | True / 30 | 每次实例化都全表查 `drive_accounts` 并逐行解析（旧行为） |
| `use_keyed_rate_limiter` | True | 退回「每 adapter 实例一把锁 + 随机 sleep 0.05~0.10s」 |
| `use_token_refresh_single_flight` | True | xunlei/aliyun/guangya 退回各自原实现（实例锁或裸刷新） |

### 10.5 尚未完成（S3 接线 / S4 / S5，按优先级）

1. **提交归属（S3 已关闭，2026-11-09）**：原计划「把服务层 80 处 + 扩展层 16 处提交全部上移」，实测后改为**分类登记 + 预算护栏**（§10.10）。真正可上移且不改变失败语义的只有 `update_drama_lead_in_settings` 的末尾写提交（已交给路由，设置+审计同一次提交）；其余 95 处属于「后台任务自己拥有工作单元」「Telegram 每次 update 一个工作单元」「长 IO 前的只读边界」「有意的渐进持久化」，硬搬会改掉「卡片能刷新」「崩溃可恢复」「单条失败不影响整批」等现有行为。落地物：`end_read_tx()` 把「结束只读事务」与「提交业务数据」分开；`test_commit_ownership.py` 锁定 95 处预算，新提交点必须显式登记。
2. **端口接入（S3 已关闭，2026-11-09）**：4 个端口已从「0 引用」变成「有实现 + 进生产路径」——`KeyedLockPort`（`LRUKeyedLocks.acquire/release/keyed`）、`SingleFlightPort`（`SingleFlight`，`adapter_factory` 已改用它做首次创建合并）、`ClockPort`（`SystemClock` 经 `container.clock()` 接入预览缓存时间源）、`TaskRunUseCasePort`（签名与真实用例对齐）。`regression_tests/test_ports_conformance.py` 用 `runtime_checkable` 做结构验收。**仍未接线（S4）**：`DriveGatewayPort`（适配器方法名是 `ls_dir/save_file/create_share_link`，与端口的 `list_dir/transfer/check_share` 不一致，需要先说清「端口是给用例用的与网盘无关的语义」还是「改适配器命名」，属 S4 适配器拆分的一部分）、`NotifierPort`（`NotifyQueue.submit` 与 `send` 语义不同）、`CachePort`（现有缓存都是 SQLite 表，进程内 cachetools 只在权限/图片代理用）。
3. **S4 剩余批次**（2026-11-09，见 §10.12/§10.13/§10.14）：✅ 查询链路 presenter 下沉 + 契约 golden、✅ 扫码会话回收、✅ 账号快照缓存 / 按账户限速 / 令牌刷新 single-flight、✅ `DriveGatewayPort` 实现与装配（裁决为「薄语义层，不吸收执行器编排」，见 §10.14）；⏳ 物理搬目录 + `app/_compat/` shim（按附录 A 迁移 `services/*` → `application/`、`extensions/adapters` → `infrastructure/`，旧路径 re-export 一个版本）。**S4-5 遗留两个已知缺口**：① 令牌刷新 key 按「实例」而非按「账户」——跨实例共享令牌状态需要共享存储（本期不做）；② 适配器侧 `set_config_saver`/`_global_config_saver` 是死代码，`refresh_token` 轮换**不回写数据库**（写库路径见 `backend/app/services/telegram_bot/actions.py:942`），属 R13 幂等/凭据一致性专项。
4. **迁移与 schema 治理进 CI（S3 已关闭，2026-11-09）**：`check_schema_drift` 新增 `--ignore-file`，白名单 `scripts/schema_drift_allowlist.txt`（10 张遗留表逐张注释）；`.github/workflows/db-schema-gate.yml` 在空库上跑完整迁移链（48 个 revision）后做 drift 与 preflight 双检；本机实测 drift=0、pending=0。**仍待办**：10 张遗留表的「删除/归档」决策单（R17）、部署侧把 `migration_preflight` 加进启动前脚本（当前只有 `backup_before_migrate`）。
5. **前端契约护栏**（S5）：把 `executions` 固定 3 条、SSE `data:` + `[DONE]`、错误体 `{code,message,detail}`、23 字段 SharePreview 固化成 golden 测试。
6. **基线维护**：`deferred_imports` 现为 **59**（基线 33）——这是「container + 函数内延迟 import」方案的**预期代价**，不是退化，本轮已用 `--update-baseline` 刷新（同时把 `violations` 预算从 165 下调到 **117**、`cycles` 从 1 改 0，做到「只降不升」）；`modules` 188→230、`internal_imports` 610→666 属新增代码正常增长。
7. **风险 R13–R19 的落实**：幂等键、WAL 膨胀监控、通知送达率、调度漏跑、schema 双真相、自动迁移不可逆。「护栏入库」已按用户「精确放行」处理：`.gitignore` 移除 `backend/tests`、`docs` 整行忽略，改为只忽略 `docs/*.{html,png,jpg,jpeg,gif,zip}`（预览垃圾）；并按用户要求把 12 篇历史文档（`docs/backend-optimization-*.md`、`docs/superpowers/`、`docs/WPS*.md`、`docs/追剧任务引流文件*.md`）加回忽略。当前待推送 61 个未跟踪文件 + 25 个已跟踪修改；其中必须推送的是 17 个新架构代码、6 个新 regression test、4 个 scripts、workflow、baseline、26 个 `backend/tests`、5 篇架构文档。

### 10.6 对应「第几周」

- 原计划：S0 今天 → S1 本周 → S2 下周 → S3 第 3–4 周 → S4/S5 第 5–9 周。
- 当前：**S0 + S1 + S2 + S3 全部完成（含尾巴）**（§10.9 验收表 + §10.10 提交归属总账 + §10.11 端口与 schema 治理），**S4 全部完成（6/6 批）**：§10.12 查询链路与契约 golden、扫码会话回收；§10.13 账号快照缓存、按账户限速、令牌刷新 single-flight；§10.14 `DriveGatewayPort` 实现与装配；§10.15 + §10.16 物理搬目录 5 批全部落地（`infrastructure/{http,drive,execution,scheduling,notifications,search}` 就位，`services/` `extensions/` `thirdparty/` 只剩 84 个兼容垫片）。回归 **493 绿**、违规 115、环 0。相当于**第 6 周初**的位置；S4 之后只剩 S5（前端契约 golden、CI 补全、垫片删除与技术债收尾）。

### 10.7 本轮复盘：一次被测试发现的「静默丢数据」

**现象**：把 4 个端点的 `db.commit()` 收口到 `with transaction(db)` 后，用户侧的写数据量没有变化，接口也都是 200；只有专门写一个「持写锁时写库」的回归测试，才发现**行数是 0**。

**根因**（两条叠加）：
1. SQLite 锁冲突发生在 SQLAlchemy 的 `flush` 阶段时，SQLAlchemy 已经**回滚了整个事务**，并把 session 置为 `DEACTIVE`（`db.in_transaction()` 仍为 True，但 `db.is_active` 为 False）。
2. 外层重试壳只重试 `db.commit()`：第二次 commit 提交的是一个**空事务**并成功返回。于是「重试」把一次失败变成了「成功但没数据」。

**修复**（`backend/app/db/session.py`）：
- `commit_with_locked_retry(db, attempts=3)`：只有「锁错误 + 会话仍在事务中 + 会话仍 active」三者同时成立才退避重试；否则**抛原始锁错误**（`OperationalError: database is locked`，接口 5xx）。
- `commit(db)`：给「提交后还要发副作用」的流程用的薄封装。
- `transaction(db)` 改用前者；`retry_on_locked()` 保留但语义改为「重跑整个用例」，文档与注释明确禁止包单个 commit。

**为什么测试没拦住**：旧版 `test_unit_of_work.py` 用的是 `_FlakyDb` 假对象，它的 commit 抛异常时不改变任何状态，所以「重试成功」看起来是对的。真实 SQLite 的失败会**改变会话状态**，假对象永远模拟不出来。新测试用真实临时库 + 另一连接 `BEGIN IMMEDIATE` 持锁（`test_lock_conflict_is_not_masked`），断言「抛锁错误 + victim 行数 0」。

**可迁移的教训**（写进评审清单）：
1. **假对象测不出会话状态机**：事务/锁/重试类代码必须用真实 SQLite 文件库，另一个连接制造争用。
2. **重试的正确对象是「用例」而不是「commit」**：只要重试的粒度小于「已经发生副作用」的边界，就可能重复写或静默丢写。
3. **「接口 200」不是写入成功的证据**：写路径的验收必须包含「行数/内容确实变化」。
4. **同类风险还有几处未审**：`retry_on_locked` 的其它潜在调用点、`share_preview_batch.py` 的 `_short_tx`、`tasks_stream.py` 的旁路会话——下一轮 S3 接线时逐个检查「异常后是否会话已不可用却仍继续提交」。

### 10.8 提交收口的操作口径（可复制到后续批次）

1. **先分类再动手**：①纯 DB 短事务 → `with transaction(db)`；②事务前有外部 IO（网盘/TMDB/Telegram）→ 只把尾部 `db.commit()` 换成 `commit(db)`；③服务自己管理事务边界（如 `drama_lead_in` 先 commit 结束只读事务再跑网盘 IO）→ 路由只调用 `commit(db)`，不包 `transaction`。
2. **验收三元组**：全量测试（当前 447 passed, 2 skipped）+ 分层门禁（117 条 = 基线 117、环 0）+ `import app.main`；写路径另加「行数确实变化」的行为验证（见 §10.7）。
3. **定点替换、保留用户改动**：编辑前逐文件读原文，只替换目标行；`old_string` 要带足够上下文（`adb.commit()` 会同时命中 `return payload_out` 之类）；工作区 CRLF 文件用 `\r\n` 拼接，避免伪 diff。
4. **长读事务的关闭必须 opt-in**：服务不能自行 `commit()` 调用方会话——执行器路径可能已 `flush` 执行记录但尚未提交。做法是加 `close_read_tx: bool = False`，只由已知只读的调用方（只读路由、后台刷新任务）传 `True`；默认 `False` 时行为与改造前逐字一致，并有回归测试锁定。
5. **工具注意**：`py_compile app/api/routes/*.py` 在 pwsh 下不展开 glob（`[Errno 22]`），须逐文件循环；pytest 的 `tmp_path` 若命中他人目录（`backend/pytest-of-qiuqu`）会 `WinError 5`，运行前把 `TMPDIR/TEMP/TMP` 指到 `backend/.tmp`。

### 10.9 S3 仓储下沉的落地方式与验收（2026-11-09）

**一句话**：把「业务层直接写 SQL/拿 ORM 对象」改成「业务层调仓储函数」，但**不引入依赖注入框架**——用 `app/application/container.py` 里的函数访问器做唯一装配点，仓储模块只在函数体内被延迟 import。

**调用链（唯一允许的形状）**：

```python
# app/services/users.py（业务层，application）
from app.application.container import users_repo

def get_user(db, user_id: int):
    return users_repo().get_user(db, user_id)

# app/application/container.py（装配层，唯一知道仓储位置的地方）
def users_repo():
    from app.infrastructure.persistence.repositories import users
    return users
```

**硬规则**（评审时逐条对照）：

1. `app/services/**` 不得在模块顶层 `import` `app.models.*` 或 `app.infrastructure.*`；需要时走 `container`。
2. 每个仓储模块只碰自己的模型；跨模型 JOIN 由「主仓储」负责（如 `tasks_repo.list_drama_execution_rows` 在函数内延迟 import `TaskExecution`）。
3. 服务函数的类型注解用 `Any`（不用 `TYPE_CHECKING` 导入模型，否则仍算违规）；确实需要的字段访问保持不变。
4. `db.commit()` 的归属**不因搬仓储而改变**——仓储只做查询/装配，事务边界仍在调用方或 `transaction()`。
5. 仓储函数返回 ORM 对象或行元组均可，但**不得返回 `Select` 语句**（业务层不得持有查询构造能力）。

**已落地清单（22 个仓储模块）**：`audit`、`permissions`、`roles`、`users`、`refresh_tokens`、`magic_regex`、`notification_settings`、`task_executions`、`tasks`、`task_scheduler_setting`、`tmdb_cache_scheduler_setting`、`tmdb_media_cache`、`tmdb_settings`、`drive_accounts`、`drive_account_probe_scheduler_setting`、`resource_search_sources`、`invalid_share_links`、`share_preview_batch_cache`、`telegram_bot_session` + `__init__`。

**S3 验收对照（全部达标）**：

| 验收项 | 目标 | 实测 | 结论 |
| --- | --- | --- | --- |
| application→persistence 违规 | ≤20 | **15** | 达标 |
| import 环（SCC） | 0 | **0**（27 模块大环已拆） | 达标 |
| 路由裸 `db.commit()` | ≤10 | **0** | 达标 |
| 回归测试 | 全绿 | **454 passed, 2 skipped**（含提交归属护栏 3 项、端口验收 6 项、presenter 契约 4 项、会话回收 3 项） | 达标 |
| 事务边界单测 | 异常后无残留 | `test_unit_of_work.py`（6 项，含真实 SQLite 争锁）+ `test_route_transactions.py`（3 项） | 达标 |
| 分层门禁 | 只降不升 | violations 165→**117**，exit 0 | 达标 |

**已知例外（计入预算，不隐瞒）**：`tmdb_cache` 3 条边（写路径是 Core + 专用 writer engine，见 §10.3 决策 5）、`drama_lead_in` 4 条边（与 `task_lead_in`/`system_setting` 强耦合，待 S4 与追剧模块一起搬）、`share_preview_batch` 2 条边（`db.session` 与 `DriveAccount`，属 S4 链路重构范围）。

**为什么这一步值得**：业务层不再 import 模型，模型字段改名/拆分时编译器（门禁）能立刻指出受影响的用例；`select()` 集中后，慢查询、缺索引、`noload` 策略有了唯一修改点；后续加缓存/读写分离只需改仓储，不必逐个业务文件改。

### 10.10 提交归属总账（S3 收尾，2026-11-09）

S3 的最后一项是「提交归属」：改造前全 app 有 **146 处** `.commit()`（路由 89、服务 80、扩展 16、main/scripts/db 7），没人说得清「这条写入失败时该谁回滚」。处理结果：**路由层归零，全 app 还剩 95 处，全部登记为四类有意为之的边界**。

**四类归属**：

| 类别 | 含义 | 处置 |
| --- | --- | --- |
| `request-scoped`（请求级） | HTTP 请求内的一次业务动作 | 全部上移到路由的 `commit(db)` / `transaction(db)`；路由层裸 `.commit()` **0** |
| `app-lifecycle`（应用生命周期） | 启动 lifespan、运维脚本这类「进程级一次性写入」，没有请求上下文可依附 | 保留；在预算里登记 |
| `background-owned`（后台拥有） | 调度器 / 执行器 / Telegram 轮询线程自己就是工作单元的拥有者 | 保留；每个任务/每轮独立提交（卡片可见、崩溃可恢复） |
| `intentional-progressive`（有意渐进持久化） | 长流程里「先落一段、失败不回滚已成功部分」的记录点 | 保留；用注释写明失败语义 |

**95 处逐文件总账**（与 `test_commit_ownership.py` 的 `COMMIT_BUDGET` 一一对应）：

| 文件 | 处数 | 归属 | 为什么必须由它提交 |
| --- | --- | --- | --- |
| `app/core/runtime/tx.py` | 1 | infra | `end_read_tx()` 的实现本体：结束只读事务 |
| `app/db/session.py` | 1 | infra | `commit_with_locked_retry()` 的实现本体（提交原语） |
| `app/main.py` | 2 | app-lifecycle | lifespan：权限种子初始化、启动时执行记录清理 |
| `app/scripts/ensure_admin.py` / `init_db.py` | 2 | app-lifecycle | 运维脚本：建管理员 / 建表后落盘 |
| `app/extensions/runtime/task_scheduler.py` | 14 | background-owned | 每个定时任务一个工作单元，无请求会话可依附 |
| `app/extensions/runtime/task_executor.py` | 1 | background-owned | 自动换链每轮独立落盘：刷新卡片、下一轮网络请求不占写锁 |
| `app/extensions/runtime/telegram_bot_manager.py` | 1 | background-owned | 轮询线程启停状态落盘 |
| `app/services/telegram_bot/handlers.py` | 50 | telegram-update-unit | 每次 update 一个工作单元：入口提交 + 中间渐进持久化 |
| `app/services/telegram_bot/actions.py` | 20 | telegram-update-unit | 动作执行后立即持久化，保证卡片刷新可见 |
| `app/services/drama_lead_in.py` | 2 | intentional-progressive | 清理过期记录；`_store_result` 保存记录并隔离异常（返回 `record=None`） |
| `app/services/share_preview_batch.py` | 1 | intentional-progressive | `_short_tx` 用独立短会话，避免把调用方的读事务拖长 |

**`end_read_tx()` 与 `commit()` 的分工（容易混，单独强调）**：

1. `end_read_tx(db)` = 「提交一个没有写入的事务」，只用于**只读路径进入外部 IO 之前**（网盘、TMDB HTTP、Telegram），目的是释放 SQLite 快照、让 WAL checkpoint 能推进。它**不得**用来代替业务提交。
2. `commit(db)` / `transaction(db)` = 业务写入的提交，由**事务边界拥有者**调用（当前是路由，后续是应用用例）。它们会在锁冲突时做安全重试（`commit_with_locked_retry`）。
3. 服务层只读函数里出现 `db.commit()` 是错误写法（语义不清），应改为 `end_read_tx(db)`；本批已把 `drama_lead_in` 的 7 处、`tmdb_cache` 的 1 处全部替换。

**为什么把 `end_read_tx` 放在 `app/core/runtime/tx.py` 而不是 `app/db/session.py`**：它只是对 Session 状态机的操作，零 ORM 依赖。放在 platform 层后，application 层服务可以合法使用（`application → platform` 允许），不会新增 `application → persistence` 违规；`app/db/session.py` 仍重导出它，旧的 `from app.db.session import end_read_tx` 写法保持可用。实测这样处理后门禁 violations 维持 **117**、deferred 维持 **58**、环 **0**。

**预算护栏（`backend/regression_tests/test_commit_ownership.py`，3 项）**：

| 断言 | 作用 |
| --- | --- |
| `test_routes_have_no_raw_commit` | 路由层出现裸 `.commit()` 直接失败（防止提交归属重新散落回 HTTP 层） |
| `test_commit_counts_stay_within_registered_budget` | ① 出现未登记的文件 → 失败；② 任一文件超过预算 → 失败；③ 预算里有但文件已无提交点 → 失败（要求同步收缩）；④ 总数 > 95 → 失败 |
| `test_session_local_usage_is_whitelisted` | 直接 `SessionLocal(` 的旁路会话必须登记理由（新增会失败） |

**维护规则**：新增后台上作单元（如新的定时任务、新的轮询线程）应当允许增加提交点，但必须同时改 `COMMIT_BUDGET` 并在 PR/文档写明「为什么这个工作单元没有请求上下文可依附」。这是「只降不升」的唯一合法例外，且必须显式发生。

**验证命令**：

~~~bash
cd backend
XXM_TESTING=1 python -m pytest regression_tests/test_commit_ownership.py -q   # 3 passed
XXM_TESTING=1 python -m pytest tests regression_tests -q                     # 447 passed, 2 skipped
python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json  # violations=117, cycles=0, exit 0
~~~


### 10.11 端口接入与 schema 治理（2026-11-09，S3 尾巴收口）

**一句话**：把「画在纸上的 8 个 Protocol」变成「4 个有实现、有生产调用点、有结构验收」；把「schema 双真相」变成 CI 里会红的门禁。

#### 端口接线状态

| 端口（`app/domain/ports/`） | 实现 | 生产接线点 | 验收 |
| --- | --- | --- | --- |
| `KeyedLockPort` | `core/runtime/keyed_locks.LRUKeyedLocks` 新增 `acquire(key, timeout_seconds)` / `release(key)` / `keyed(key)` 上下文 | 既有两处消费 `get()` 不变（`drama_lead_in` 素材锁 1024、`adapter_factory` 的 256） | `isinstance(locks, KeyedLockPort)`；`keyed()` 异常也释放；`acquire(timeout)` 被占用返回 `False` 不抛 |
| `SingleFlightPort` | 新建 `core/runtime/single_flight.SingleFlight`（`_guard` + `dict[key, _Flight]`，leader 执行 factory，waiter `Event.wait()`） | `adapter_factory.create_adapter` 的「per-key 锁 + 二次查缓存」手工写法改为 `SingleFlight.run(cache_key, _build)` | 同 key 并发只调用一次 factory、共享结果；factory 抛错时 leader 与等待者拿到**同一个异常**；flight 结束即出表（不缓存结果） |
| `ClockPort` | 新建 `core/runtime/clock.SystemClock`（`now`/`now_utc`/`monotonic`/`sleep`） | `application/container.clock()` 唯一装配；`services/share_preview_batch_cache._utcnow()` 改走它（过期判断） | `isinstance(clock, ClockPort)`；`spbc.clock is container.clock` |
| `TaskRunUseCasePort` | `domain/ports/task.py` 的协议签名改为与真实用例一致：`run(task, policy, log=None, *, account_manager=None, init_account_for_task=True)` | `RunTaskUseCase` 即唯一实现；触发方（HTTP/SSE/调度器/Telegram）已全部经 `RunPolicy.for_trigger` 调用 | `isinstance(RunTaskUseCase(None), TaskRunUseCasePort)` |
| `DriveGatewayPort` | **未接线** | — | 适配器方法是 `ls_dir(pdir_fid, max_items)` / `save_file` / `create_share_link`，与端口的 `list_dir(path)` / `transfer(share_url, target_dir)` / `check_share` 不是同一抽象：前者暴露网盘 fid 细节，后者是「按路径收发文件」。**先做 S4 适配器拆分，再决定是改端口还是加一层 gateway 适配**，否则会把 fid 泄漏进用例层 |
| `NotifierPort` | **未接线** | — | `NotifyQueue.submit(title, content)` 是「入队」语义，端口 `send(title, message, *, level, meta)` 是「同步送达」语义；合并需要先决定「送达失败如何回传调用方」，属 S4/S5 通知语义专项 |
| `CachePort` | **未接线** | — | 当前缓存是 SQLite 表（跨重启要活），进程内 `cachetools` 只用于权限缓存与图片代理；把两者塞进同一个 `get/set/ttl` 接口会模糊「重启后是否还在」这个关键差别，需先拆分「进程内缓存」与「持久缓存」两种端口 |

**SingleFlight 的一条关键语义**：等待者拿到的是 leader 的**同一异常对象**（traceback 会指向 leader 线程）。因此 factory 必须自己决定「异常是失败还是可接受结果」——适配器创建路径沿用旧行为，在 factory 内部 `try/except` 记日志并返回 `None`，所以等待者拿到的是 `None`（与改造前「per-key 锁 + 二次查缓存」完全一致）。未来若让 single-flight 承载「必须把异常抛给所有调用方」的场景，要另写测试，不能复用适配器路径。

#### schema 治理门禁

1. **先修工具本身的静默失效**：`scripts/check_schema_drift.py` 原本靠 `app/db/base.py` 末尾的 `import app.models` 注册模型；S3 为破环删掉了那行，脚本随即报 `model_tables=0`、把 36 张表全判成 `db_only`——一个「看起来在跑、其实结论全错」的典型。已改为脚本内显式 `import app.models`（Alembic `env.py` 本来就是这么做的）。
2. **白名单只允许一个方向**：`--ignore-file scripts/schema_drift_allowlist.txt` 只豁免 10 张已知遗留表（`plugin_*` 2 张 + `sync_*` 8 张，逐张带注释）。`model_only`（模型有、库里没有）永远不豁免——那就是漏迁移。
3. **CI 用空库跑整条迁移链**：`.github/workflows/db-schema-gate.yml` 设 `XXM_TESTING=1` + `DATABASE_URL=sqlite:///./ci-schema.db`，先 `python -m alembic upgrade head`（48 个 revision），再 `check_schema_drift`（drift 必须为 0），最后 `migration_preflight --fail-if-pending`（必须停在 head）。这与本地实库（37 张表、10 张遗留表）形成互补：空库证明「迁移链能建出模型要的库」，实库证明「现网没有额外偏差」。
4. **本机实测**：实库 `drift=0`（26/26，忽略 10 张遗留在内共 36 张）；空库升级后 `drift=0`、`pending=0`、`tables=37`、`freelist 2.1%`，两脚本 exit 0。

#### 验证命令

~~~bash
cd backend
XXM_TESTING=1 python -m pytest regression_tests/test_ports_conformance.py -q   # 6 passed
XXM_TESTING=1 python -m pytest tests regression_tests -q                        # 454 passed, 2 skipped
python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json  # violations=115, deferred=59, cycles=0, exit 0
python ../scripts/check_schema_drift.py --ignore-file ../scripts/schema_drift_allowlist.txt  # drift=0, exit 0
python ../scripts/migration_preflight.py --fail-if-pending                      # current=head, pending=0, exit 0
~~~

#### 残余风险（明确记账）

- **端口签名没有类型检查**：`runtime_checkable` 只验方法名存在，不验参数。项目无 mypy，签名一致性靠上述集成测试与代码评审；一旦 `RunTaskUseCase.run` 再改参数，`TaskRunUseCasePort` 不会自动报错（已在测试注释里写明）。
- **CI 白名单会「过期不报」**：若某张遗留表将来被迁移删除，白名单里的条目不会失败（脚本只在 drift 非零时失败）。属可接受残留，处理时机是 R17 的「遗留表决策单」。
- **`db-schema-gate` 需要完整依赖**：workflow 走 `pip install -r backend/requirements.txt`（与镜像一致），比只装 sqlalchemy+alembic 慢；换取的是「CI 里的 app.models 与生产一致」，不引第二份依赖清单。

### 10.12 S4 进展（2026-11-09，第一批：查询链路）

S4 按「先契约、后搬迁、最后才动网盘执行」的顺序拆成 6 批。本批完成前两批 + 一个低风险生命周期修复。

| 批次 | 内容 | 状态 |
| --- | --- | --- |
| S4-1 | **响应契约 golden 先落地**：`backend/regression_tests/test_task_presenter_contract.py`（4 项）锁死 `TaskOut` 22 个字段的顺序与取值、`executions` 按 `started_at` 倒序截断 3 条、`addition`/`extra`/`adapter_snapshot` 的 JSON 解析、`lead_in` 缺省值 | ✅ |
| S4-2 | **presenter 下沉**：`backend/app/services/task_serialization.py` → `backend/app/api/presenters/task.py`（纯函数、零 IO）；旧模块**删除**，2 个路由（`api/routes/tasks.py`、`tasks_stream.py`）与 3 个测试的 import 改到新路径 | ✅ |
| S4-3 | **扫码登录会话回收**：`backend/app/services/drive_account_auth_sessions.py` 创建前清过期项、上限 512 淘汰最早到期（旧实现只在命中时删除，放弃扫码的会话连同 adapter 永久驻留）；`test_auth_session_ttl.py` 3 项 | ✅ |
| S4-4 | **`DriveGatewayPort` 实现与装配**：裁决为「端口＝薄语义层」（只吸收响应差异，不吸收执行器编排），实现落在 `infrastructure/drive/gateway.py`，在分享探测链路装配 | ✅ 见 §10.14 |
| S4-5 | **账号快照缓存**（`DatabaseAccountManager` 每次实例化全表查询）、**按账户限速**（替代实例级 sleep）、**令牌刷新 single-flight** | ✅ 见 §10.13 |
| S4-6 | **物理搬目录 + `app/_compat/` shim**：按附录 A 迁移 `services/*` → `application/`、`extensions/adapters` → `infrastructure/drive`、`extensions/runtime` → `infrastructure/scheduling`，旧路径 re-export 一个版本 | ⏳ |

#### 为什么 presenter 直接删旧模块、不留 shim

`services/task_serialization.py` 全仓库只有 2 个生产调用方（`api/routes/tasks.py:52`、`api/routes/tasks_stream.py:34`）和 3 个测试 import。若按「旧路径 re-export」保留，会新增一条 `application → presentation` 的依赖边（services 反向 import api），正好撞上分层规则；而改这 5 处 import 的成本极低。**判据**：只有当旧路径的调用方多到改不动（例如 `app.extensions.*` 被 20+ 模块引用）时，才值得付 shim 的依赖代价。

结果：分层违规 117 → **115**（`application→presentation` 10 → 8，旧模块里两处 `services → schemas` 边随搬迁消失），基线随之下调到 115，仍满足「只降不升」。

#### 本批验证

~~~bash
cd backend
XXM_TESTING=1 python -m pytest tests regression_tests -q                          # 454 passed, 2 skipped
XXM_TESTING=1 python -m pytest regression_tests/test_task_presenter_contract.py -q # 4 passed
XXM_TESTING=1 python -m pytest regression_tests/test_auth_session_ttl.py -q        # 3 passed
python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json  # violations=115, deferred=59, cycles=0, exit 0
python -c "import app.main"                                                       # import ok
grep -rn "task_serialization" app/                                                # 空（旧路径已无引用）
~~~

#### 下一步的顺序理由

1. **先 S4-5 再 S4-4**：账号快照缓存与 RateLimiter 是「执行链路外围」，改动可独立回滚；`DriveGatewayPort` 一旦装配进转存主链路，行为分叉风险立刻上升，应放在有契约快照与账号层稳定之后。
2. **S4-6 最后**：物理搬迁只改路径，不改行为；放在行为类改动之后，可以让每次搬迁的 diff 都是纯路径变更，出问题一眼定位。
3. **每批都必须留「可一键关闭」的开关**（沿用 `settings.use_*` 灰度 flag 约定），默认开启、异常时关回旧路径。

### 10.13 S4 进展（2026-11-09，第二至三批：执行链路外围）

S4-5 的三件事有一个共同点：**不碰网盘协议、不碰转存语义、不改变任何前端契约**，只把「每次任务都重算一遍」「限不住」「会互相作废」这三类问题收敛。因此它可以先于 `DriveGatewayPort` 落地，且每一项都留了可一键关闭的灰度开关。

| 子任务 | 改造前的问题（实测） | 做法 | 落地文件 | 护栏 |
| --- | --- | --- | --- | --- |
| 账号快照缓存 | `DatabaseAccountManager.__init__` 每个任务/每次预览都全表 `select(DriveAccount)`，再逐行 `parse_config_json` 两次 + `serialize_config`；调用点 15 处（`task_executor.py:122/:169`、`task_scheduler.py:226`、`tasks_drive.py:55/:157/:200`、`share_preview_batch.py:274/:390/:607/:871/:1009` 等） | `TtlCache`（30s）+ **版本号 + 表指纹双校验**；写路径 7 个服务方法 + 2 个仓储方法各 `invalidate_drive_accounts()`；返回 `deepcopy` | `backend/app/core/runtime/ttl_cache.py`、`cache_version.py`、`backend/app/extensions/runtime/account_manager.py`（重写，118 行） | `test_account_snapshot_cache.py`（10 项） |
| 按账户限速 | `base_adapter._throttle_request()` 只持**实例**锁 + `random.uniform(0.05,0.10)`；而实例缓存 TTL 180s、cookie 轮换会产生同账户多实例 → 同一账户可被并发打穿 | `KeyedRateLimiter.wait(key, min, max)`：按 `(drive_type, account)` 预约「下一个可用时间点」，不同 key 互不阻塞；key 依次回退到 cookie md5 / index | `backend/app/core/runtime/rate_limiter.py`、`backend/app/extensions/adapters/base_adapter.py` | 同上（`FakeLimiter` 断言排队 key 与区间） |
| 令牌刷新 | xunlei/aliyun 各自持实例锁、guangya 完全无锁且 `_request()` 两处自动刷新；并发刷新会让后到者作废先到的新 `refresh_token`，症状是「偶发要重新扫码登录」 | `TokenManager.refresh(key, is_valid, refresh)` 用 `SingleFlight` 合并同 key 并发刷新；guangya 的 401 路径传 `stale_token`，`is_valid` 判据是「令牌未被我看到的那个旧值」→ 不会把刚换好的新令牌再刷废 | `backend/app/core/runtime/token_manager.py`、`xunlei_adapter.py`、`aliyun_adapter.py`、`guangya_adapter.py`（实现在 `GuangyaClientLite`） | `test_runtime_components.py`（11 项） |

#### 为什么这四个组件都在 `app/core/runtime/`（platform 层）

`scripts/check_layering.py` 的矩阵里，`application`（`app/services`）与 `persistence`（`app/infrastructure/persistence`）**互相不能 import**，但两者都必须能引用这些组件：写路径（服务层和仓储层）要 `invalidate_drive_accounts()`，读路径（`app/extensions`）要用 `TtlCache`。唯一人人可 import 的层是 `platform`。放在 `app/core/runtime/` 的代价是「平台层出现了一点业务名词（`drive_accounts_version`）」，收益是**零新增分层违规**（本轮 violations 稳定在 115，未因这 4 个新模块增加）。

#### 三条实现约束（评审必看）

1. **缓存只存纯数据，不存 ORM 实例**。命中条件是「版本号相同 **且** 表指纹（`count(id)`、`max(id)`、`max(updated_at)`）相同」。版本号覆盖同进程内的所有写路径；指纹兜底「运维脚本/手工 SQL 改了库、但没走我们的服务方法」。因为指纹里有 `count`，删除一条也会失效；因为 `updated_at` 是 SQLite 秒级精度，**同一秒内的连续两次修改可能指纹相同**——所以写路径必须显式 bump 版本号，不能只依赖指纹。
2. **令牌刷新 key 按「实例」而不是按「账户」**（`token:{drive_type}:{account}:{instance_id}`）。同一账户可能有多个 adapter 实例（实例缓存 TTL、cookie 轮换），各自的 token 是内存状态；若按账户合并，等待者会被 leader 的「成功」唤醒，但它自己的 `self.token` 并没有变——这是**假成功**，比不合并更危险。跨实例共享令牌需要把令牌状态挪到共享存储（`refresh_token` 回写数据库），属 S4-5 遗留缺口。
3. **guangya 的 401 刷新必须带 `stale_token`**。若 `is_valid` 只判「是否过期」，并发两条 401 会连续刷新两次：第二次用第一次刚换来的新 `refresh_token` 去换，多数网盘会让第一次的令牌作废——这正是「偶发登录失效」的产生机制。`is_valid = bool(client.token) and client.token != stale_token` 把「别人已经换好了」识别为成功，不再重复刷新。

#### 本批验证

~~~bash
cd backend
XXM_TESTING=1 python -m pytest regression_tests/test_runtime_components.py regression_tests/test_account_snapshot_cache.py -q  # 21 passed
XXM_TESTING=1 python -m pytest tests regression_tests -q                                                    # 475 passed, 2 skipped
python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json                          # violations=115, deferred=59, cycles=0, exit 0
python -c "import app.main"                                                                                # import ok
~~~

#### 遗留与下一步

- **S4-4 `DriveGatewayPort`**：仍未动。端口语义（`list_dir/transfer/check_share`）与适配器命名（`ls_dir/save_file/create_share_link`）不一致，且转存编排散在 `task_executor.py:76`、`drama_executor.py:302-321`、`share_preview_batch.py` 5 处——需要先裁决「端口吸收编排」还是「端口只做薄封装」，再在一条真实链路（分享校验/预览）上装配。
- **S4-6 物理搬目录**：`services/*` → `application/`、`extensions/adapters` → `infrastructure/drive` 未开始，旧路径 `app/_compat/` shim 也未建。
- **令牌凭据一致性（R13）**：`set_config_saver`/`_global_config_saver` 是死代码，`refresh_token` 轮换不回写数据库；跨实例令牌共享依赖它。这是「多实例/多 worker 部署」的前置条件，本期按「单进程假设」不做，但必须记录在案。

### 10.14 S4 进展（2026-11-09，第四批：网盘网关端口）

#### 裁决：端口是「薄语义层」，不吸收执行器编排

`DriveGatewayPort` 的协议形状（`list_dir` / `create_dir` / `transfer` / `check_share` / `close`）与适配器方法名（`ls_dir` / `mkdir` / `save_file` / `create_share_link`）并不一致，落地前必须先选一条路。最终选**薄语义层**，理由三条：

1. **端口要吸收的是「各家网盘不一样」，不是「业务怎么做」**。适配器之间真正乱的只有响应结构：`fid`/`fs_id`/`file_id`、`fid_token`/`share_fid_token`/`token`、`is_dir`/`isdir`/`dir`/`file_type`。把这些归一，用例就能用同一套语义读目录、验分享；这是扩展性的真实瓶颈。
2. **转存主链路的编排不能早搬**。重试轮次、`query_task` 轮询、`save_as_top_fids` 提取、按驱动分支（`uc` 等）、执行记录入库，散在 `extensions/runtime/drama_executor.py`（`_save_items`、`_call_save_file`、`_extract_saved_fids`）与 `share_preview_batch.py` 5 处，且与「失败怎么算」强耦合。搬进端口等于把最危险的逻辑提前，收益却只是文件变短。
3. **端口先服务一条低风险链路**。分享探测（生成分享链接是否失效）只读、有分类、有既有护栏（`test_generated_share_check.py` 断言每次检测只调一次 `get_stoken`），是最好的第一个装配点。

#### 落地物

| 文件 | 内容 | 说明 |
| --- | --- | --- |
| `backend/app/domain/ports/drive.py` | 新增 `ShareProbeResult(valid, kind, message, stage)` 与 `probe_share()`；`check_share` 改为它的布尔糖 | 端口**不产出用户文案**；`stage` 让调用方选兜底文案 |
| `backend/app/infrastructure/drive/gateway.py` | `AdapterDriveGateway`：`list_dir` / `create_dir` / `probe_share` / `check_share` / `transfer` / `close`，外加 `_get_stoken` / `_get_detail` / `_save_file` / `_classify` 四个差异吸收方法 | 约 280 行；不 import 任何 `app.services` |
| `backend/app/infrastructure/drive/__init__.py` | re-export `AdapterDriveGateway` / `DriveGatewayError` / `DriveRemoteEntry` / `gateway_for_adapter` | 供 container 延迟 import |
| `backend/app/application/container.py` | `drive_gateway(adapter, *, drive_type=None)` | 函数内延迟 import 实现；application 只认端口形状 |
| `backend/app/services/generated_share_check.py` | `_probe` 改走端口；新增 `_map_kind()` 承接 kind→状态/文案映射；`_failure()` 保留为兼容入口 | **用户可见文案逐字不变**（含各 stage 的兜底句） |
| `backend/regression_tests/test_gateway_port.py` | 13 项 | 协议形状、响应归一、幂等建目录、quark/baidu quirk、三阶段失败、transfer、假第 11 家网盘 |

#### 差异怎么被吸收的（适配器方法 → 端口语义）

| 端口方法 | 实际调用 | 吸收掉的网盘差异 |
| --- | --- | --- |
| `list_dir(path)` | `ls_dir(path)` | 列表固定取 `data.list`；名字取 `file_name`/`server_filename`/`name`/`fid`；目录判定按 `is_dir`→`isdir`→`dir`→`kind`→`type`→`file_type` 优先级；大小兼容 `size`/`file_size` |
| `create_dir(path)` | `mkdir(path)` | `code`/`status` 兼容 int 与 str（0/200）；message 含「已存在」或 `exist` 视为**幂等成功**；其余抛 `DriveGatewayError` |
| `probe_share(url, passcode)` | `extract_url` → `get_stoken` → `get_detail` | 无 `pwd_id` → `stage="parse"`；无 `stoken` → `stage="verify"` 并调 `classify_share_failure`；读目录失败 → `stage="read"` 同样分类。**quark 传 `first_page=True`，baidu 传 `check_errno=True`**（漏传会把「分享已过期」判成「检测失败」） |
| `check_share(url, passcode)` | `probe_share(...).valid` | 布尔糖，不重复实现 |
| `transfer(share_url, target_dir)` | `extract_url` → `get_stoken` → `get_detail` → `save_file` | fid/fid_token 归一；缺 `fid_token` 抛 `kind="invalid"`；`file_names` 用 `inspect.signature` 探测（部分网盘必须传）；**不含重试、不含任务轮询、不写库** |
| `close()` | `adapter.close()`（存在才调） | 端口不假设适配器一定有该方法 |

#### 为什么 `transfer()` 不进转存主链路（本批的明确边界）

主链路的转存是「探测 → 建目录 → 保存 → 轮询任务 → 提取已保存 fid → 去重 → 入库 → 通知」，其中「已保存 fid 提取」在 `drama_executor` 里按驱动分支（`uc` 与其余不同），失败语义与重试次数写在执行器里。这些是**执行策略**，不是网盘差异。端口的 `transfer()` 只做「分享 → 直接保存到目标目录」这一步，用于：① 分享校验通过后的轻量转存；② 新网盘接入时的冒烟验证（假第 11 家网盘用例就是靠它跑通的）。主链路继续走 `drama_executor`，等 S5 有了执行策略的契约测试再评估是否上移。

#### 两个分层注意点

1. **container 延迟 import** → 门禁 `deferred_imports` +1（59→60），`violations` 不变（115）。这是 §10.3 决策 4 的既定代价，不是退化。
2. **已知盲区（S4-6 必须处理）**：`scripts/check_layering.py` 的 `LAYER_OF_PREFIX` 还没有 `app.application` / `app.infrastructure` 两个前缀，指向这两个包的 import 会解析成 `unknown` 层而**跳过判定**。也就是说「application 顶层 import infrastructure」现在不会红。本批仍统一走 `container.drive_gateway()` 的延迟 import 纪律；S4-6 搬目录时要把两个前缀登记进门禁，届时会显形一批既有的延迟 import，需要同步 `--update-baseline`（只改 deferred，不改 violations 预算）。

#### 本批验证

~~~bash
cd backend
XXM_TESTING=1 python -m pytest regression_tests/test_gateway_port.py -q                                                        # 13 passed
XXM_TESTING=1 python -m pytest regression_tests/test_generated_share_check.py regression_tests/test_share_failure_classify.py -q  # 7 passed（文案与调用次数未变）
XXM_TESTING=1 python -m pytest tests regression_tests -q                                                                         # 488 passed, 2 skipped
python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json                                               # violations=115, deferred=60, cycles=0, exit 0
python -c "import app.main"                                                                                                      # import ok
~~~

#### 遗留与下一步

- **S4-6 物理搬目录 + `app/_compat/` shim**：S4 唯一剩余批次。按附录 A 把 `services/*` → `application/`、`extensions/adapters` → `infrastructure/drive`、`extensions/runtime` → `infrastructure/scheduling`，旧路径 re-export 一个版本；同时把 `app.application` / `app.infrastructure` 登记进 `LAYER_OF_PREFIX` 并刷基线。
- **`NotifierPort` / `CachePort` 仍未接**（S5）：`NotifyQueue.submit` 与端口 `send` 的语义不同（前者是「入队即返回」）；`CachePort` 的现存实现是 SQLite 表，进程内 `TtlCache` 只服务账号快照与权限缓存，需要先定「端口是给进程内缓存还是给持久缓存用」。
- **`transfer()` 薄实现的生产使用面**：目前只有分享校验后的轻量转存与新网盘冒烟；主链路仍走 `drama_executor`，这条边界要在 S5 的执行策略契约测试里继续守。
### 10.15 S4-6 进展（2026-11-09）：门禁真升级 + 两批物理搬目录 + 一次事故与恢复

#### 10.15.1 门禁先「变真」，再搬文件

搬目录前必须先把门禁盲区补上，否则搬了也看不见违规：

| 动作 | 文件 | 结果 |
| --- | --- | --- |
| `LAYER_OF_PREFIX` 增加 `app.application` → application、`app.infrastructure` → infrastructure | `scripts/check_layering.py` | 此前这两个前缀解析成 `unknown` 层、违规判定直接 `continue`，等于盲区 |
| 增加 `COMPOSITION_ROOTS = {"app.main", "app.application.container", "app.application.factories"}`，允许任意层引用装配点 | 同上 | 装配点是依赖图终点，不该计入违规 |
| 拆出 `app/application/factories.py`（`task_executor_class()`） | `backend/app/application/factories.py` | 若放进 `container.py`，会形成 `container → task_executor → services(magic_regex/tmdb_cache/generated_share_check) → container` 的 15 模块新环；工厂只被用例层引用，处于末端 |

登记后立刻暴露真问题（违规 115 → 118 + 一个新环），按上表修完回到 **115 / 环 0**。

#### 10.15.2 已搬的 12 个模块（旧路径全部保留 shim）

| 旧路径 | 新路径 |
| --- | --- |
| `backend/app/thirdparty/douban_client.py` | `backend/app/infrastructure/http/douban_client.py` |
| `backend/app/thirdparty/tmdb_client.py` | `backend/app/infrastructure/http/tmdb_client.py` |
| `backend/app/extensions/runtime/task_admission.py` | `backend/app/infrastructure/scheduling/task_admission.py` |
| `backend/app/extensions/runtime/telegram_bot_manager.py` | `backend/app/infrastructure/notifications/telegram_bot_manager.py` |
| `backend/app/extensions/runtime/account_manager.py` | `backend/app/infrastructure/drive/account_manager.py` |
| `backend/app/extensions/runtime/adapter_registry.py` | `backend/app/infrastructure/drive/adapter_registry.py` |
| `backend/app/extensions/runtime/drama_executor.py` | `backend/app/infrastructure/execution/drama_executor.py` |
| `backend/app/extensions/runtime/execution_log.py` | `backend/app/infrastructure/execution/execution_log.py` |
| `backend/app/extensions/runtime/magic_rename.py` | `backend/app/infrastructure/execution/magic_rename.py` |
| `backend/app/extensions/runtime/retry_utils.py` | `backend/app/infrastructure/execution/retry_utils.py` |
| `backend/app/extensions/runtime/task_executor.py` | `backend/app/infrastructure/execution/task_executor.py` |
| `backend/app/extensions/runtime/task_scheduler.py` | `backend/app/infrastructure/scheduling/task_scheduler.py` |

shim 形态固定：旧文件只留 docstring（「新路径：…；删除时机：S4-6 完成后一个版本」）+ 显式 re-export 公开名，不复制实现。调用方全部改新路径（21 个文件），`test_commit_ownership.py` 的路径键同步改名。

验收：`pytest tests regression_tests -q` → 488 passed / 2 skipped；门禁 `modules=254 internal_imports=695 deferred_imports=62 violations=115 cycles=0`，exit 0；`import app.main` ok；旧 shim 四个代表路径可 import。

#### 10.15.3 事故：批量替换把 93 个文件写成 1 个字符（已 100% 恢复）

| 项 | 事实 |
| --- | --- |
| 现象 | 一次批量改写脚本的 `[0]` 误用，把 93 个 `.py` 写成字符串首字符（`settings.py` 内容只剩 `f`） |
| 影响面 | `app/api`、`app/application`、`app/core`、`app/db`、`app/domain`、`app/extensions/adapters`、已搬迁的 `infrastructure/*`、部分 tests/regression_tests |
| 恢复源 | ① 用户自提交 `b865b72`（2026-11-09 00:41:58，含 S3 全套与用户 WIP）整树 `git checkout -- <dir>`；② 事故后的改动从本会话 transcript 重放（300 个写/编辑/程序操作，跳过截断那一条） |
| 不可用恢复源 | VS Code Local History 0 命中；PyCharm LocalHistory 的 `changes.storageData` 不含代码正文（zlib/zstd 探测无命中） |
| 重放后修 3 处 | `keyed_locks.py` 补 `import contextlib`；`run_task.py` 改回走 `factories.task_executor_class()`（重放回的是重构前版本，带 3 条跨层违规）；`container.py` 补 `adapter_registry()` 访问器供 `services/tasks.py` 使用 |
| 最终验收 | 488 passed / 2 skipped；违规 115、环 0、deferred 62、基线已刷新；`compileall -q app` exit 0；旧 shim 可用 |

三条教训（已写进 §10.5 的完成项与团队纪律）：

1. **批量写文件必须先 dry-run 再断言产物**：改写前记录目标文件字节数/首行，写完立刻校验长度与语法，不通过就整批放弃。
2. **写操作要么幂等、要么可回滚**：已有 `git` 提交点时，批量改动前先确认「回退点是哪一个提交」，而不是假设工作区可恢复。
3. **每批写入后立刻跑测试与门禁**：本次 42 个 collection error 在第一次全量测试时即暴露，早跑早止损。

#### 10.15.4 后半批次：已完成（详见 §10.16）

1. `backend/app/services/*` → `backend/app/application/`：52 个模块已搬（含 `telegram_bot/`、`notifications/`），旧路径留 shim。
2. `backend/app/extensions/adapters/*` → `backend/app/infrastructure/drive/{factory.py,adapters/}`；`extensions/search_plugins` → `infrastructure/search/`。
3. `backend/app/_compat/`：以**清点清单**形式落地（`iter_legacy_shims()` + 5 项护栏测试），不做二次 re-export（否则只是多一层间接、白增延迟 import）。
4. `LAYER_OF_PREFIX` 的旧前缀保留：搬完后 `app.services` / `app.extensions` / `app.thirdparty` 下只剩垫片，映射到 application / infrastructure 仍然准确，且垫片只 import 新路径、不产生违规。
### 10.16 S4-6 完成（2026-11-09）：目录与目标树对齐、兼容层与验收

#### 10.16.1 本轮搬完的 5 批（累计 84 个模块，全部旧路径留垫片）

| 批次 | 旧路径 | 新路径 | 模块数 |
| --- | --- | --- | --- |
| 1 | `app/thirdparty/{douban,tmdb}_client.py`、`app/extensions/runtime/{task_admission,telegram_bot_manager}.py` | `app/infrastructure/{http,douban_client.py,tmdb_client.py}` / `scheduling/task_admission.py` / `notifications/telegram_bot_manager.py` | 4 |
| 2 | `app/extensions/runtime/{account_manager,adapter_registry,drama_executor,execution_log,magic_rename,retry_utils,task_executor,task_scheduler}.py` | `app/infrastructure/drive/` (2)、`execution/` (5)、`scheduling/` (1) | 8 |
| 3 | `app/services/**`（52 个模块，含 `telegram_bot/`、`notifications/`） | `app/application/**`（同名保留） | 52 |
| 4 | `app/extensions/adapters/**`（15 个：10 家适配器 + base + drive_auth + lead_in_support + `__init__` + `adapter_factory`） | `app/infrastructure/drive/adapters/**`；`adapter_factory.py` → `infrastructure/drive/factory.py` | 15 |
| 5 | `app/extensions/search_plugins/**`（7 个） | `app/infrastructure/search/**` | 7 |

垫片规则（三批自动化生成，批 1/2 手写）：旧文件只留 docstring + `from <新路径> import (...)` 显式列出顶层名字，**不放实现**；`app/_compat/__init__.py` 的 `iter_legacy_shims()` 扫描这些垫片的 docstring 得到「旧 → 新」清单，`regression_tests/test_compat_shims.py`（5 项）保证：清单非空、新模块可 import、旧路径可 import、无重复指向、关键类对象同一。

#### 10.16.2 搬目录过程中暴露并修掉的两个真问题

| 问题 | 现象 | 根因 | 修法 |
| --- | --- | --- | --- |
| 适配器包成环 | `import app.main` 报 `cannot import name AdapterFactory from partially initialized module` | `adapters/__init__.py` 顶层 `from ...drive.factory import AdapterFactory`，而 `factory.py` 顶层 import `adapters.base_adapter` → 包初始化重入 | `adapters/__init__.py` 改用 PEP 562 `__getattr__` 惰性导出 `AdapterFactory` / `AccountManager` |
| 搜索插件发现目录写死 | 4 个插件测试失败，日志「文件中未定义一个继承 BaseSearchPlugin 的插件」（实际扫到的是垫片） | `app/application/search_plugins.py:26` 的 `PLUGIN_DIR = Path(__file__).resolve().parents[1] / "extensions" / "search_plugins"` 用相对自身位置拼路径，文件搬走后指向垫片目录 | 改为 `parents[1] / "infrastructure" / "search"` |

教训：物理搬目录不只是改 import，**任何用 `__file__` 拼出来的资源路径都要一起改**（插件目录、模板、静态资源、SQL 脚本），并且优先改成「从包 import 常量」而不是相对路径拼字符串。

#### 10.16.3 与目标树的差异（S5 待收）

| 目标（§3.3） | 现状 | 说明 |
| --- | --- | --- |
| `infrastructure/persistence/{models,repositories,session,uow}` | `app/models`、`app/db`、`infrastructure/persistence/repositories` | models/db 仍是旧路径：它们是 26 个 ORM 模型 + 全局 Session 工厂，搬迁影响 alembic/env.py、14 处测试 `create_all`、`db/base.py` 的模型注册；建议单独一批做，且先补 schema 迁移演练 |
| `domain/{task,drive,drama}` 实体与值对象 | `app/domain/ports/*` 8 个 Protocol 已有，实体仍在各处 dict | 领域模型化属 S5 重构，不是搬目录 |
| `application/queries` 读模型 | **已完成（§10.18）**：`application/queries/{task_views,audit_logs}.py` + presenter 纯函数化 | 视图是 frozen dataclass（无 ORM/无 Session），列表查询条数与任务数无关 |
| `_compat/` 作为 shim 宿主 | `_compat` 只是清点清单，垫片仍在旧路径 | 垫片放旧路径才能让旧 import 直接命中；二次跳转会多一层导入延迟，收益为零 |

#### 10.16.4 验收（2026-11-09 实测）

~~~bash
cd backend && python -m pytest tests regression_tests -q
# 493 passed, 2 skipped in 11.10s

python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json
# modules=329 internal_imports=769 deferred_imports=63 violations=115 import_cycles=0 largest_cycle=0  (exit 0)

python -c "import app.main; print('import ok')"
# import ok
~~~

`backend/.layering-baseline.json` 已刷新为 `{"violations": 115, "deferred_imports": 63, "cycles": 0}`。
### 10.17 S5 完成（2026-11-09）：持久化搬迁、违规归零、垫片删除

**S5 收尾与固化全部落地。** 分层门禁从「预算 115」收敛到 **violations=0 / cycles=0**，
115 个 `Legacy shim` 垫片文件已删除，旧 `app/services` / `app/extensions` / `app/models` /
`app/db` / `app/thirdparty` / `app/_compat` 目录整体移除。

| 项目 | 结果 |
| --- | --- |
| 持久化搬迁 | `app/models/**`、`app/db/**` → `app/infrastructure/persistence/**`；`LAYER_OF_PREFIX` 收敛为 `app.infrastructure.persistence → persistence` |
| 请求作用域依赖 | `app/application/container.py` 顶部 re-export `SessionLocal/commit/get_db/transaction`，26 个路由改从装配点导入 |
| 基础设施装配 | container 新增 18 个访问器（adapter_factory/task_executor_cls/task_scheduler/…），替换 48 处 `app.infrastructure.*` 直连 import |
| 路由持久化下沉 | 8 个路由文件（audit_logs/dashboard/drive_accounts/system_settings/task_templates/tasks/tasks_stream/tmdb_cache）全部改走仓储，删除 model import |
| 应用层去持久化 | drama_lead_in / resource_search / share_preview_batch 改走仓储与 `db_session`；DTO 下沉 `app/application/dto/` |
| 认证拆分 | `app/application/auth_context.py` + `app/api/deps.py`，删除 `app/core/deps.py`（platform→persistence 4 条归零） |
| 分享预览下沉 | `app/application/share_preview.py::preview_share(payload, db)`；`tasks_share.py` 449→77 行 |
| 显式例外 | `scripts/check_layering.py` 新增 `DOCUMENTED_EXCEPTIONS`：`app.application.tmdb_cache → app.infrastructure.persistence`（读写混合 + writer engine，§10.3 决策 5），单独打印、不计 violations |
| 环 | `scripts/check_layering.py` 的 SCC 计算排除 composition-root 边（装配点惰性 import 不再被误判成 27 模块环） |
| 垫片 | 删除 115 个 Legacy shim + `regression_tests/test_compat_shims.py`（该护栏已完成使命） |

**验证（2026-11-09，cwd=backend，PYTHONPATH=.，XXM_TESTING=1）：**
- `python -m pytest tests regression_tests -q` → **489 passed, 2 skipped**（较 S4 的 493 少 5 项 = 删除的 `test_compat_shims.py`）。
- `python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json` → `modules=246 internal_imports=709 deferred_imports=84 violations=0 import_cycles=0`，exit 0。
- `python -c "import app.main"` → ok。
- 基线已刷新：`backend/.layering-baseline.json` = `{"violations":0,"deferred_imports":84,"cycles":0}`。

**S5-3 遗留：全部完成（2026-11-09）。** ~~application/queries 显式拆分~~ **§10.18**、
~~前端契约 golden 快照~~ **§10.19**、~~3 篇 ADR~~ **§10.20**（`docs/architecture/adr/0001-0003`）、~~开发规范文档~~ **§10.21**（`docs/development-guide.md`）。
方案侧到此收口，后续动代码只需守住 §6.1 指标、§7 风险清单与开发规范 §12 的 DoD。

**说明：** 本轮改动与用户工作区未提交文件交错，仍**未做任何 git commit**（沿用历史约定）；
`git add/commit` 仍需用户确认后再执行。

### 10.18 非阻塞小项 ①：`application/queries` 显式拆分（2026-11-09）

**目标（§4.3 验收口径）**：路由只依赖 `application/queries` 的读模型，不再自己编排「查什么、怎么拼」；
响应模型仍由 `api/presenters/` 负责，响应 JSON 逐字节不变。

| 位置 | 内容 |
| --- | --- |
| `backend/app/application/queries/__init__.py` | 出口：视图类型 + 查询函数 |
| `backend/app/application/queries/task_views.py` | `TaskExecutionView` / `TaskView` / `TaskSchedulerSettingView`（frozen dataclass，纯值，无 ORM 无 Session）；`execution_view()` / `task_view()` / `task_detail_view()` / `list_task_views()` / `list_recent_execution_views()` / `scheduler_view()` |
| `backend/app/application/queries/audit_logs.py` | `AuditLogView` + `list_audit_log_views(db, page, page_size, q, action, success)` |
| `backend/app/api/presenters/task.py` | 由「接 Session 的装配函数」改为纯函数 `task_out(view)` / `execution_out(view)` / `scheduler_out(view)`，不再 import Session / tmdb_view / drama_update_progress |
| `backend/app/api/presenters/audit_log.py` | `audit_log_item(view)` / `audit_log_list(views, page, page_size, total)` |
| 路由 | `routes/tasks.py::get_tasks` 由 15 行编排变 1 行（`[task_out(v) for v in list_task_views(db)]`）；post/patch/status 用 `task_detail_view()`；`routes/tasks_stream.py` 两处 SSE done 用 `execution_out(execution_view(execution))`；`routes/audit_logs.py::get_audit_logs` 变两行 |
| 测试改造 | `regression_tests/test_task_presenter_contract.py` 与 `tests/test_drama_lead_in.py` / `test_drama_lead_in_core.py` / `test_drama_lead_in_own_file_e2e.py` 共 6 处调用改为 `task_out(task_view(...))`，断言值一行未改 |
| 新增护栏 | `regression_tests/test_task_queries.py`（4 项）、`regression_tests/test_audit_log_query.py`（2 项） |

**保持不变（由回归测试逐字锁定）**：`TaskOut` 22 字段及其顺序、executions 固定 3 条、
`lead_in` 默认 `{"state": "none", "last_added_at": None}`、`addition/extra/adapter_snapshot` 的 JSON 容错、
SSE `done` 事件里 `execution` 的字段、`AuditLogListOut` 的 `{page,page_size,total,items}`。

**N+1 护栏怎么测**（`test_task_queries.py::test_list_task_views_query_count_is_independent_of_task_count`）：
在 tmp SQLite 上挂 `before_cursor_execute` 计 SELECT 条数，3 个任务与 6 个任务各跑一次，
断言两次条数相同且 ≤8。实测两次都是 **6 条**（任务、executions 窗口 ID、executions 行、TMDB status、
TMDB payload、引流摘要）——以后谁在列表里顺手加一次逐条查询，这条断言当场变红。
另一条断言锁定「截断归属」：查询层不截断（显式传 6 条就保留 6 条），**只由 presenter 截成 3 条**。

**验证（2026-11-09，cwd=backend，PYTHONPATH=.，XXM_TESTING=1）**

~~~bash
python -m pytest tests regression_tests -q
# 495 passed, 2 skipped in 10.13s      （489 → 495，+6 项新护栏）

python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json
# modules=250 internal_imports=719 deferred_imports=84 violations=0 import_cycles=0   (exit 0)

python -c "import app.main; print('import ok')"
~~~

- 门禁变化：modules 246→250、internal_imports 709→719（queries 包 + 两个 presenter 的合法依赖边），
  violations / cycles 保持 **0**，`backend/.layering-baseline.json` 无需调整。
- 依赖方向：`presentation → application`（presenter 只读视图）与 application 内部自引用，均为 §3.1 允许方向。

**为什么视图放 `application/queries` 而不是 `api/presenters`**：§3.1 规定 application 不得 import presentation，
所以视图不能直接是 `app.schemas` 里的响应模型；反过来 presenter 依赖 application 是允许方向。
视图因此与 `application/dto/` 同层（值对象，非 ORM），presenter 只做 1:1 映射。

### 10.19 非阻塞小项 ②：前端契约 golden 快照（2026-11-09）

**为什么做**：重构期真正的破坏往往不报错——字段改名、字段消失、SSE 少一个事件、错误体多包一层，
后端测试全绿、前端静默显示空白。本项把「契约」变成可 diff 的文件，两侧任一方改动都必须同时改 golden。

| 交付物 | 内容 |
| --- | --- |
| `backend/regression_tests/test_frontend_contract_golden.py` | 304 行 / 6 项测试：后端模型契约、前端类型契约、两侧字段对齐、SSE 线格式、`[DONE]` 哨兵、错误体 |
| `backend/regression_tests/golden/frontend_contract.json` | 8,647 B 快照：10 个后端模型的字段+类型+顺序、`frontend/src/types/tasks.ts` 的 15 个类型字段集合、SSE 逐字节文本、错误体 |

**锁定的四类契约**

1. **后端响应模型**（字段名 + 注解 + 声明顺序）：`TaskOut`（22 字段）、`TaskExecutionOut`、`TaskShareLinkOut`、
   `TaskSchedulerSettingOut`、`SharePreviewOut` / `SharePreviewItemOut`（20 字段）/ `SharePreviewBatchOut` /
   `SharePreviewBatchItemOut` / `DriveBrowseOut` / `DriveBrowseItemOut`。
2. **前端类型**（`frontend/src/types/tasks.ts` 全部 15 个 `export type` 的字段集合）：用括号深度扫描只取顶层字段，
   嵌套内联对象（`drama_update_progress`、`latest_video`）不误判。
3. **SSE 线格式**：不是读常量，而是真的跑一遍 `tasks_stream._stream_response()`（假队列 + `asyncio` 收 chunk），
   逐字节锁死 `event: init|log|stage|done\ndata: {...}\n\n`、`ensure_ascii=False`（中文原样）、空闲 `": ping\n\n"`、
   `text/event-stream` 与 `Cache-Control/X-Accel-Buffering` 头；`tasks_share` 的 `data: [DONE]` 哨兵单独锁。
4. **错误体**：`ApiError` → `{code, message, detail}` + 状态码；`RequestValidationError` → 422 + `VALIDATION_ERROR`；
   未捕获异常 → 500 且**只有** `{code, message}`。校验错误的 `detail` 是 `str(exc)`、随 pydantic 版本变，故意不锁内容只锁键顺序。

**「后端有、前端没有」的差异必须登记理由**（`DRIFT_ALLOWLIST`）：

| 前端类型 | 未消费字段 | 理由 |
| --- | --- | --- |
| `SharePreviewItem` | `filtered_by_file_filter` / `filtered_by_file_date` / `filtered_by_folder` / `filtered_by_search` / `priority_match` | 后端过筛/优先级标记，前端预览列表不渲染（按 `name_re` 着色） |
| `SharePreviewBatchItem` | `share_author_name` | 批量预览不展示分享者名，单条预览才展示 |

白名单是**精确匹配**而非「≥」：前端哪天开始消费某个字段，这条登记立刻失效并变红，逼着删掉僵尸条目。

**负向自测（改坏能被抓到，已实测 3 个场景）**

~~~text
前端删掉 TaskOut 已有字段  → "TaskItem ↔ TaskOut: 后端多出未登记字段 ['taskname']"
前端补上白名单里的字段    → "…白名单里已失效的 ['filtered_by_search']"（必须删登记）
后端删掉前端在用的字段    → "TaskItem ↔ TaskOut: 前端多出未登记字段 ['lead_in']"
~~~

**更新方式**（只在确认契约变更时）：`UPDATE_CONTRACT_GOLDEN=1 python -m pytest regression_tests/test_frontend_contract_golden.py -q`，
然后把 golden 的 diff 与后端/前端改动放在同一个提交里审。

**验证（2026-11-09，cwd=backend，PYTHONPATH=.，XXM_TESTING=1）**

~~~bash
python -m pytest tests regression_tests -q
# 501 passed, 2 skipped in 10.34s      （495 → 501，+6 项）

python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json
# modules=250 internal_imports=719 deferred_imports=84 violations=0 import_cycles=0  (exit 0)

python -c "import app.main; print('import ok')"
~~~

CI 覆盖：`docker-publish` 的 backend job 在仓库根 checkout 后以 `working-directory: backend` 跑
`pytest regression_tests`，`frontend/` 在同一次 checkout 里，所以这项跨端检查在 CI 中真实生效。

### 10.20 非阻塞小项 ③：3 篇 ADR（2026-11-09）

把「为什么这么做」从方案正文里拆成可单独引用的决策记录，放在 `docs/architecture/adr/`。
三篇都是**已发生事实的记录**（不是提案），每篇含：背景 / 决策 / 后果 / 已否决的备选 / 重新评估的触发条件 / 代码证据行号。

| ADR | 锁定的决策 | 为什么值得单独记 |
| --- | --- | --- |
| [0001 TMDB 写路径例外](architecture/adr/0001-tmdb-cache-writer-isolation.md) | `application/tmdb_cache.py` 保留在 application 层，对 persistence 的 4 条 import 作为 documented exception；不改写为纯 ORM、不整体搬迁 | 这是「分层规则」唯一的显式例外。不写清楚，下一个人会当成遗漏去"修"，而修它会破坏「写连接与读连接分离 + `BoundedSemaphore(2)` 写槽」的稳定性设计 |
| [0002 准入保留在调用点](architecture/adr/0002-task-admission-stays-at-call-sites.md) | `TaskAdmission.reserve()` 不进 `RunTaskUseCase`；6 个调用点原位保留；用例只归一 `RunPolicy` | 这是对方案 §3.5 原设计的**有意偏离**。理由是可观测契约：收进用例会把同步 409/429 变成 200+failed，破坏前端重试/toast 与 5 项准入回归测试 |
| [0003 单进程假设](architecture/adr/0003-single-process-deployment-assumption.md) | 只支持 1 个 uvicorn 进程、1 个容器副本；禁止 `--workers`/多副本；扩容走「单写者模式」 | 依赖冻结（禁 Redis/Celery）使跨进程能力不可用，12 处进程内状态因此是**正确性前提**而非缓存优化。这条必须写在部署可见处，否则一次"顺手加 --workers"就是重复转存事故 |

三篇 ADR 的共用结构：**状态 / 背景（含代码证据行号）/ 决策 / 后果（含代价）/ 备选方案与否决原因 / 重新评估的触发条件**。
「触发条件」一节是让 ADR 可被推翻的唯一入口（例如换 Postgres、执行模型改队列、要求高可用），避免决策变成不可质疑的教条。

**与方案正文的分工**：方案 §3.5 / §10.3 讲「现在长什么样」，ADR 讲「为什么不能那样改，以及什么条件下可以改」；
CI 与门禁只校验结构（`scripts/check_layering.py` 的 documented exceptions），决策理由靠 ADR 传承。

本项为纯文档改动，无代码变更；验证沿用 §10.19 的同一基线（501 passed / 2 skipped、violations 0、cycles 0、250 模块、`import app.main` ok）。

### 10.21 开发规范文档（2026-11-09）

本项不是重构动作，而是把前 20 节沉淀下来的规则**翻译成可照着干活的操作手册**，回答「明天要加一个功能/改一个模块，按什么顺序动、放哪个目录、写哪些测试」。

**交付物：`docs/development-guide.md`（547 行 / 约 30.9 KB）。**

| 章节 | 回答什么问题 |
| --- | --- |
| §0 三分钟速查 | 四条命令（测试 / 分层门禁 / schema 门禁 / 契约更新）+ 十条铁律 |
| §1 分层规则 | 七条允许 import 关系、组合根例外、documented exception、当前完成态目录树、三条硬规则 |
| §2 新增功能 7 步动线 | 模型 → 迁移 → 仓储 → container 访问器 → queries/use case → presenter + 路由 → 测试（含写端点与读端点样板） |
| §3 数据库与迁移 | 迁移必须可 downgrade、禁止 `create_all`、备份与迁移预检、JSON 列上限、索引约定、SQLite PRAGMA 现状 |
| §4 事务与提交 | 三原语（`transaction` / `commit` / `end_read_tx`）只从 container 取；五条规则；六行反模式表 |
| §5 并发与异步 | 单进程假设、场景 → 组件对照表、六条禁止事项、压测脚本 |
| §6 接入第 11 家网盘 | 一文件 + 两行注册 + 测试；11 个抽象方法；限速/令牌/close/失败分类/文案逐字保留 |
| §7 读模型与响应契约 | queries 是 frozen dataclass 且禁 N+1；presenter 纯函数；改响应 = 改契约三件套 |
| §8 配置密钥灰度 | `use_xxx` 开关三处登记、`secrets.json` 不入库、`DATABASE_URL` 仅 `XXM_TESTING=1` 可覆盖 |
| §9 可观测性 | 审计同事务、禁 `except: pass`、慢请求阈值、`run_log` 上限 |
| §10 测试要求 | `tests/` 本地 vs `regression_tests/` 进 CI；四类测试；两个真实踩坑；测不到的三个领域要手工 e2e |
| §11–12 提交与 DoD | 提交粒度、评审要点、8 条 DoD 勾选清单 |
| 附录 A/B | 五段可复制代码骨架（仓储 / 访问器 / 按 key 串行+单飞 / 后台工作单元 / 适配器骨架）；六个常见问题 |

**三份文档的分工（避免读者不知道该看哪份）：**

| 文档 | 回答的问题 | 读者 |
| --- | --- | --- |
| [架构演进方案](architecture-evolution-plan.md)（本文） | 为什么改、分几期改、指标怎么定、风险在哪 | 决策者 / 架构评审 |
| [架构分层一页纸](architecture/README.md) | 现在每层在哪、允许谁 import 谁、门禁怎么跑 | 第一次改 `backend/app/` 的人 |
| [开发规范](development-guide.md) | 具体某个需求按什么顺序落地、模板长什么样、什么算做完 | 每天写业务代码的人 |

**规范里被固化成「可检查」而非「靠自觉」的部分**（都有对应护栏，不是纸面要求）：路由零裸 `db.commit()`（`test_route_transactions.py`）、提交点预算（`test_commit_ownership.py`）、`SessionLocal` 白名单（同文件）、响应契约字段与 SSE 字节（`test_frontend_contract_golden.py`）、分层方向（`scripts/check_layering.py` + CI）、schema 漂移（`scripts/check_schema_drift.py` + CI）。

**写作过程本身的两条教训**（已写进 §10 与附录 B）：
1. 用 `String.raw` 生成含反引号的 Markdown 时，反引号的转义不会被处理，会落下「反斜杠+反引号」（本次实测 600 处、围栏计数为 0）；写文档用 heredoc 或普通字符串。
2. 文档里的路径/行号/CLI 参数必须**逐个回代码核对**——本次按代码核实修正了 5 处（已删的 `app/thirdparty`、备份实现搬到 `infrastructure/persistence/backup.py`、通知队列入队点、`DRIVE_TYPE` 生成实例属性、后台工作单元片段的 import）。

**验证**：纯文档改动，无代码变更，测试与门禁基线不变（501 passed / 2 skipped、violations 0、cycles 0）；结构自检：代码围栏 28 条成对、8 个表格块列数一致、7 个相对链接全部可达。



### 10.22 事故修复：TMDB 缓存刷新 500 与「未定义全局名」静态护栏（2026-11-09）

**现象**：用户在「TMDB 缓存」页面点刷新，前端弹「服务器内部错误」（HTTP 500）。

**根因**：`backend/app/api/routes/tmdb_cache.py` 的 `post_tmdb_cache_refresh` 末尾仍是 `row = db.scalar(lookup)`，而 `lookup` 在 S3 仓储下沉时已被删除。git 证据：提交 `e902106`（仓储下沉，2026-11-09 03:40）把

```python
lookup = select(TMDBMediaCache).where(...)
row = db.scalar(lookup)
```

的第一处换成了 `row = tmdb_media_cache_repo().get_row(...)`，**函数末尾第二处 `db.scalar(lookup)` 忘了一起换** → 执行到该行抛 `NameError: name 'lookup' is not defined` → 500。刷新走同步分支还是异步分支都会走到这一行，所以「怎么点都报错」。

**同一批自动改写还留下 5 处同类缺陷**（用标准库 `symtable` 全量扫描 `app/**/*.py` 找出来的）：

| 位置 | 未定义名 | 触发场景 | 修法 |
| --- | --- | --- | --- |
| `backend/app/api/routes/tmdb_cache.py:271`、`:358` | `commit` | 刷新缓存 / 刷新关联任务（**就是这次报 500 的第一处**，500 在到这里之前就已抛） | 从 `app.application.container` 导入 `commit` |
| `backend/app/api/routes/tmdb_cache.py:273` | `lookup` | 同上 | 改为 `tmdb_media_cache_repo().get_row(...)` 重新查一次（提交后取最新状态） |
| `backend/app/api/routes/drive_accounts.py:199`、`:236` | `drive_accounts_repo` | 账号启停 / 探测 / 扫码登录后取账号 | 从 container 导入访问器 |
| `backend/app/api/routes/drive_accounts.py:342` | `DriveAccount` | 光鸭短信登录二次 probe 失败的回退分支 | 改为 `drive_accounts_repo().get_account(...)`（顺带消掉一条 presentation→persistence 违规） |
| `backend/app/application/telegram_bot/handlers.py:442`、`:447` | `datetime` | `_display_datetime()`（任务/账号列表显示时间） | 补 `from datetime import datetime` |
| `backend/app/application/telegram_bot/handlers.py:929` | `_to_sort_ts` | 分享链接有多个目录、需要按更新时间选一个时 | 从 `...telegram_bot.actions` 导入（保持单一实现，不复制逻辑） |
| `backend/app/infrastructure/drive/adapters/aliyun_adapter.py:345`、`:351`、`:362` | `random` | 阿里云盘重试退避（`time.sleep(random.uniform(...))`） | 补 `import random` |

**为什么 501 项测试全绿却没抓到**：这些分支都没有用例；`NameError` 只有**真的执行到那一行**才会报。全量测试、`import app.main`、分层门禁、schema 门禁全部通过，用户点一下就 500。

**护栏：`backend/regression_tests/test_no_undefined_globals.py`（2 项）**

- 用标准库 `symtable` 对 `app/**/*.py` 逐个建符号表：函数作用域里「既不在本作用域赋值 / 不是本作用域 import / 不在模块顶层定义 / 不是内建名」的全局符号 = 未定义全局名，判失败。
- `ALLOWED` 例外必须写理由；`test_allowlist_entries_still_needed` 反向校验——登记了但实际已不需要的条目必须删掉，避免允许名单腐化。
- 负向自测已实测：临时放一个 `app/_probe_undefined.py` 调用未定义函数 → 测试变红并指出 `app/_probe_undefined.py: undefined name 'undefined_probe_name'`；删除后恢复绿。
- 成本：250+ 模块 0.8s，进 `regression_tests` 即随 CI 跑。

**护栏二：`backend/regression_tests/test_tmdb_cache_refresh_route.py`（2 项，锁「点刷新」这条路径本身）**

- 直接调用路由函数 `post_tmdb_cache_refresh(request, payload, current_user, db)`，绕过 HTTP 与鉴权，避开启动整套应用的重量级夹具。
- 真实临时 SQLite（只 `create_all` `TMDBSetting` / `TMDBMediaCache` / `AuditLog` 三张表）+ `monkeypatch` 掉 `app.application.tmdb_cache._fetch_tmdb_detail`，不碰网络。
- 第一项断言：库里没有该条时返回 `queued=False`、`status.configured=True`、`media_type/tmdb_id` 正确、假网络层确实被调用 —— 只要 `commit` 或 `lookup` 缺一个，这里立刻 `NameError`。
- 第二项断言：第二次调用 `status.exists=True`（覆盖「提交后重查」那一行）且审计表里正好 2 条 `tmdb.cache.refresh`。

**验证（2026-11-09，cwd=backend，`PYTHONPATH=.`，`XXM_TESTING=1`，TMPDIR 用 cygpath 转 Windows 路径）**

- `python -m pytest tests regression_tests -q` → **505 passed, 2 skipped**（501 + 4 项新护栏）。
- `python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json` → `modules=250 internal_imports=719 deferred_imports=84 violations=0 import_cycles=0`，exit 0。
- `python -c "import app.main"` → ok；`symtable` 全量扫描 → `TOTAL 0`。

**三条教训**

1. **自动改写必须配静态检查**：这类缺陷是「改了一半」的产物，测试抓不到、review 也容易漏（diff 里 `lookup` 只出现一次）。凡是批量替换 import/函数调用，结束后必须跑一次「未定义名」扫描。
2. **「import 了才叫搬完」**：搬模块/换访问器时，删除的 import 与新增的 import 必须成对核对；本次 `drive_accounts.py` 既少了访问器 import，又留着已经不该用的 ORM 模型（`DriveAccount`）。
3. **未定义名与分层违规同源**：`db.get(DriveAccount, ...)` 本身就是 presentation→persistence 的越界写法，靠「补一个 import」也能让它跑起来，但正确修法是走仓储访问器。修 bug 时优先选「既修好又更合规」的那条路。

**用户侧动作**：修复只在工作区，正在运行的后端进程仍加载旧代码，**需要重启后端**（本地 `uvicorn` 或容器）才生效。

### §10.23 WPS 搜索源：链接粘连与重复结果修复（2026-11-09）

**现象**（用户在「搜索源管理 → WPS 文档搜索 → 测试搜索」输入「嫁金钗」）：结果里出现一条 `https://pan.quark.cn/s/f74735be7943夸克和谐太快建议使用百度云` —— 中文提示被当成链接的一部分，且与同一部剧的正常夸克链接重复；标题后的来源名也直接粘在标题文字上。

**根因两处**

1. 后端 `app/application/wps_document_search.py` 的 `_share_link()` 只校验 `path.startswith('/s/')`，任何跟在共享码后面的中文都会被保留；文档里同一段文字既提供了 `mark.href`（干净）又提供了正则抽取的 URL（粘连），于是同一分享产出两条记录。
2. 前端 `frontend/src/views/extensions/SearchSourcesView.vue` 的测试结果把 `item.source`（插件名）与标题放在同一个行内块里，没有间距与分隔——看起来像标题被追加了「WPS 文档搜索」。

**修复**

- `_share_link()` 改为**按共享码重建地址**：`SHARE_PATH = re.compile(r'^/s/([A-Za-z0-9_-]+)')` 取 id，只保留 `PASSCODE_QUERY = {'pwd','password','passcode'}` 且值匹配 `[A-Za-z0-9]{1,16}` 的查询参数（`parse_qsl` + `urlencode` + `urlunsplit`），丢弃粘连文字、其它参数与 fragment。归一化后重复链接自然落到 `seen` 去重。
- 前端结果头部改为 `div.test-result__head`（flex + gap），来源名渲染为 `.test-result__source` 药丸标签。

**证据（真实文档，非构造数据）**

- 修复前对线上文档（4608 个 block）实测：`parse_document` 得到 1901 行，含那条粘连链接。
- 修复后：1900 行；用旧实现做基线逐条比对——**旧实现独有 1 条，正是粘连那条；新实现没有新增任何 URL，也没有丢失任何非 pwd 参数**（说明归一化没有误伤 1900 条里的其它分享）。

**护栏**：新增 `backend/regression_tests/test_wps_link_normalize.py`（4 项）——粘连中文剥离、`pwd` 保留而其它参数丢弃、尾随标点/非分享路径拒绝、同段文字的两种 URL 变体折叠为一行。

**验证**：`python -m pytest tests regression_tests -q` → **509 passed, 2 skipped**；`check_layering.py --fail-on 0` → `violations=0 cycles=0`；前端用 `@vue/compiler-sfc` 编译该 SFC（template + scoped style）通过——本机只有 Node 18，`vite build`（vite 8 需 Node 20+ 的全局 `CustomEvent`）无法在此环境跑通，需在 Node 20+ 环境或 `pnpm dev` 下确认。

### §10.24 TMDB 刷新「假成功」：写锁争用被当成「别人正在刷」（2026-11-09）

**现象**（用户原话 m05511：「刚才我点刷新没有更新，但是现在好了，什么也没有更改」）

在「TMDB 缓存」页面点刷新：有时点完数据没有变化，但页面弹绿色「已刷新」；过一会儿什么都没改再点一次又正常了。间歇性、自愈、无代码变更 —— 典型的**写锁争用被静默吞掉**。

**复现（真库副本 + 外部写锁，判定「一次都没拉」）**

复制 `backend/data/app.db` 到 `backend/.tmp`，用第二个连接 `BEGIN IMMEDIATE` 占住写锁（并 `UPDATE tmdb_media_cache SET fail_count = fail_count`），然后调 `refresh_tmdb_detail_sync(db, media_type="tv", tmdb_id=279923, ..., force=True)`：

- 耗时 0.63s，**没有抛错**；
- `_fetch_tmdb_detail` **一次都没有被调用**（网络根本没出去）；
- 库内 `fetched_at` / `fail_count` / `last_error` 全部不变；
- 函数**静默返回旧缓存**。

**根因链（三处「吞掉」叠加）**

1. `backend/app/application/tmdb_cache.py` 的 `_cache_writer()` 用独立 writer 引擎，`connect_args={"timeout": 0.5}` —— 写锁被占超过 0.5s 时 `_claim_refresh` 抛 `OperationalError`。
2. `_cache_write_busy()` 把它判成 `True`，于是 `_claim_refresh` **`return False`**，语义是「别人正在刷，我不刷了」—— 但真实原因是**没人刷、只是写锁被占**，于是这一轮刷新被无声丢弃。
3. `refresh_tmdb_detail_sync` 看到 claim 失败直接返回旧缓存 → 路由 200 + 旧 status → 前端 `TMDBCacheManageView.vue` 只看 HTTP 成功就 `ElMessage.success('已刷新')`。同理 `_complete_refresh` 写回失败也 `return False`：**网络已经拉了数据，但库里没更新，也没人知道**。

空闲时没有别的写入 → 一切正常，所以表现为「刚才不行、现在好了」。

**修复（区分「用户触发」与「后台触发」两套语义）**

| 位置 | 改动 |
| --- | --- |
| `backend/app/application/tmdb_cache.py` | 新增 `_WRITER_CONNECT_TIMEOUT_SECONDS=2.0`、`_WRITE_SLOTS_TIMEOUT_SECONDS=3.0`、`_CLAIM_ATTEMPTS=2`、`_CLAIM_RETRY_DELAY_SECONDS=0.2`；writer 连接 `timeout` 0.5→2.0、写槽 `acquire` 1→3.0；新增 `_claim_refresh_with_retry()`（失败 sleep 后重试，重试时刷新 `now`）；新增 `class TMDBCacheBusy(RuntimeError)` 与 `_busy_message(row)`；`_complete_refresh` 的重试之间加 sleep；`refresh_tmdb_detail_sync` / `get_tmdb_detail_cached` 新增 `raise_on_busy: bool = False` |
| `backend/app/api/routes/tmdb_cache.py` | 刷新端点的同步分支传 `raise_on_busy=True`；`except TMDBCacheBusy` → `ApiError(code="TMDB_CACHE_BUSY", http_status=503, message=_busy_message)`；**失败时不写 audit、不 commit** |
| `backend/app/application/media_discovery.py` + `backend/app/api/routes/media_discovery.py` | `tmdb_detail(..., raise_on_busy=False)` 透传；路由按 `raise_on_busy=bool(force)` 调用，强制刷新撞锁 → 503（不能把旧缓存当「已刷新」返回） |
| `frontend/src/views/extensions/TMDBCacheManageView.vue` | `refreshRow` / `quickRefresh` 包 try/catch；`status.last_error` 存在 → 失败弹窗并显示后端错误；`status.refresh_in_progress` → 「刷新未完成，请稍后重试」；仅真正成功才 `ElMessage.success('已刷新')` |

**关键取舍：默认 `raise_on_busy=False`**

后台定时刷新（`refresh_expired_cache`）、追剧自动更新、任务执行器都必须保持「锁争用就跳过、下轮再来」，**不能因为一次写锁冲突让整条转存任务失败**。所以「抛错」的开关只由**用户显式触发的请求**打开（刷新按钮、强制刷新详情页）。这条规则已写进代码注释与测试断言。

**护栏：`backend/regression_tests/test_tmdb_refresh_busy.py`（4 项，4.7s）**

| 用例 | 断言 |
| --- | --- |
| `test_busy_write_lock_raises_instead_of_silently_skipping` | 持锁 + `raise_on_busy=True` → 抛 `TMDBCacheBusy`，且 `_fetch_tmdb_detail` **未被调用**（不白发网络请求）、行未变 |
| `test_user_retry_succeeds_after_lock_released` | 释放锁后同一调用成功：fetch 调 1 次、`display_title` 更新、`refresh_in_progress=False` |
| `test_background_callers_keep_silent_legacy_behaviour` | `raise_on_busy=False`（后台路径）不抛、静默返回旧缓存 —— 锁死「不许把后台路径改坏」 |
| `test_refresh_route_returns_503_when_cache_busy` | 路由层 `post_tmdb_cache_refresh(...)` → `ApiError` `code=TMDB_CACHE_BUSY` `http_status=503` |

**端到端复核（真 37 表库副本，不用构造 schema）**

```text
app read: 30                                   # 副本可读
holder write transaction ok                    # 用应用引擎的第二个连接占住写锁
BUSY: ApiError code=TMDB_CACHE_BUSY http=503   # 用户触发 → 明确 503，不再假成功
fetch calls while busy: 0                      # 没白发网络请求
AFTER: queued=False fetched_at=2026-11-09 12:15:36 title=VERIFY-NEW err=None
fetch calls total: 1                           # 锁释放后同一次点击成功落库
```

**验证（2026-11-09，cwd=backend，`PYTHONPATH=.`，`XXM_TESTING=1`）**

- `python -m pytest tests regression_tests -q` → **513 passed, 2 skipped**（509 + 4 项新护栏）。
- `python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json` → `modules=250 internal_imports=722 deferred_imports=84 violations=0 import_cycles=0`，exit 0。
- `python -c "import app.main"` → ok；前端 SFC 用 `@vue/compiler-sfc` 编译通过（本机 Node 18 跑不了 `vite build`）。
- 顺带删掉 `tmdb_cache.py` 里重复定义的 `_close_read_transaction`（原 :70-82 与 :84-96 两份，保留后者）。

**四条教训**

1. **「HTTP 200」不等于「业务做成了」**：刷新这类「触发型」接口必须回传**结果状态**（`last_error` / `refresh_in_progress`），前端不能只看请求成功。已在 §4.3 前端契约清单里补一条：*触发型接口的按钮提示必须读业务结果字段*。
2. **`except 锁冲突: return` 是隐形丢数据**：把「写锁被占」和「别人正在做同一件事」混为一谈，就会出现「既没人做、也没人知道」。凡是 `return False` 的 busy 分支，都要能回答「用户怎么知道这次没做」。
3. **用户路径与后台路径的错误语义要分开**：同一个函数用 `raise_on_busy` 开关区分——用户路径**必须显式失败**（503 + 文案，让用户重试），后台路径**必须静默跳过**（不能拖垮任务）。默认值取「静默」是为了不破坏既有调用方。
4. **锁争用要留重试窗口**：0.5s 的连接超时对「另一个请求正在提交」这种短冲突太短，2.0s + 2 次 claim 重试足以吃掉大部分瞬时冲突，又不会让用户等太久。

**用户侧动作**：改动只在工作区，**正在运行的实例（Docker 镜像 / 本地 uvicorn）需要重建或重启才生效**；若服务器出不去 TMDB，现在会明确报 `TMDB_CACHE_BUSY`/错误文案，而不是弹「已刷新」。

## 10.25 工作台「清理失败记录」后仍显示失败任务：检测失败挂在**成功**记录上

**现象**（用户报障）

> 在工作台点击清理失败日志之后，上面还会显示失败任务

**复现（真库副本 + 合成数据，确定性）**

复制 `backend/data/app.db` 到 `backend/.tmp/dash.db`，插入 1 条 `status=failed` 的执行记录 + 1 条 `status=success` 且 `adapter_snapshot.generated_share_check.status="invalid"` 的执行记录：

```text
BEFORE: recent_failures=2（stage=generated_share_check + stage=end）; summary total=20 success=19 failed=1
delete_failed_drama_executions(db, days=0) -> deleted=1
AFTER : recent_failures=1（stage=generated_share_check）; summary total=19 success=19 failed=0
```

与用户所见完全一致：失败记录**已删**，但「近期失败」列表里还剩一条。

**根因：`recent_failures` 有两个来源，清理只覆盖其中一个**

| 来源 | 代码 | 清理是否覆盖 |
| --- | --- | --- |
| `status == "failed"` 的执行记录 | `backend/app/application/dashboard_drama.py` 主循环 | ✅ `delete_failed_drama_executions` 删除 |
| **成功记录**里 `read_share_check(snapshot)["status"] in {"invalid","error"}` 派生的「分享链接检测失败」 | 同上，`if len(recent_failures) < 12:` 分支 | ❌ 失败信息挂在 `status=success` 的行上，按状态删失败记录永远删不到 |

**为什么不用「改写快照 / 删成功记录」**

| 方案 | 后果 |
| --- | --- |
| 把快照状态改成 `valid` 或删掉 `generated_share_check` | 任务列表徽章（`frontend/src/components/tasks/GeneratedShareStatus.vue`）会从「已失效」变回「待检测」——**掩盖真实状态**；且 `last_definitive` 去重被破坏，同一失效链接会**重复通知**（`backend/app/application/notifications/task_notify.py:47`） |
| 连成功执行记录一起删 | 丢转存历史（用户想清的是「失败日志」，不是「成功记录」） |
| 引入新的 `status` 值表示「已清理」 | 前端徽章用固定 labels 字典（valid/invalid/error）渲染，未知值直接 `undefined` 崩模板 |
| ✅ **清理水位（采用）** | 只影响仪表盘**读模型**：清理后不再把已清理的检测失败当「近期失败」提醒；徽章仍如实显示分享链接真实状态，通知去重逻辑不受影响 |

**实现（4 个文件）**

| 位置 | 改动 |
| --- | --- |
| `backend/app/application/dashboard_drama.py` | 新增 `DRAMA_FAILURES_CLEARED_KEY="dashboard.drama_failures_cleared_at"`、`_parse_naive_datetime()`、`read_failures_cleared_at()`、`mark_failures_cleared()`（水位**只增不减**）、`clear_drama_failures(db, *, days=0, now=None)`；`build_drama_overview()` 读水位后，检测失败派生的条目仅当 `cleared_at is None or checked_dt is None or checked_dt > cleared_at` 才展示 |
| `backend/app/api/routes/dashboard.py` | 清理端点改为调用应用层 `clear_drama_failures(db, days=days)`（仍在 `with transaction(db)` 内，审计与提交归属不变）；用例 import 起别名 `clear_drama_failures as clear_drama_failures_use_case`——**同名 handler 会自我递归** |
| `backend/app/infrastructure/persistence/repositories/task_executions.py` | `delete_failed_drama_executions(db, *, days=0, before=None)`：`before` 由应用层给出时以其为准，保证「删除范围」与「清理水位」用**同一个 cutoff**；顺带去掉 `datetime.utcnow()`（原来与本地朴素 `started_at` 差 8 小时，且触发 DeprecationWarning） |
| `backend/regression_tests/test_generated_share_check.py` | 该测试自建 schema，补 `SystemSetting.__table__`（仪表盘现在会读水位表），断言不变 |

**关键取舍**

1. **水位只增不减**：先「清理最近 30 天前」再「清理全部」时，水位不能被回拨，否则已隐藏的旧失败会重新冒出来。
2. **解析不出时间的仍展示**：宁可多报一条，也不把真实失败藏起来。
3. **清理范围与水位共用一个 cutoff**：否则会出现「记录删了但水位更早」或反之，导致「清完仍显示 / 没清却消失」两类不一致。
4. **只影响展示**：快照、通知去重、任务列表徽章、转存历史全部不动；用户看到的「已清理」语义仅限仪表盘「近期失败」这个小卡片。

**护栏：`backend/regression_tests/test_dashboard_clear_failures.py`（4 项）**

| 用例 | 断言 |
| --- | --- |
| `test_clear_hides_share_check_failure_stored_on_success_execution` | 清理前 2 条（`end` + `generated_share_check`）→ `clear_drama_failures(days=0)` 返回 1 → 清理后 `recent_failures==[]`、`execution_failed==0`；**成功记录仍在且快照状态仍是 invalid**（徽章不被改写） |
| `test_failure_newer_than_watermark_is_still_shown` | 水位只屏蔽历史：清理后新增的失败记录与更新的检测失败都照常出现（2 条） |
| `test_clear_with_days_only_hides_entries_before_cutoff` | `days=30`：40 天前的失败记录被删、40 天前的检测失败被屏蔽；10 天前的两条保留（`deleted==1`，剩 3 行） |
| `test_watermark_only_moves_forward` | 用更早的 cutoff 再写入不会把水位回拨 |

**验证（2026-11-09，cwd=backend，`PYTHONPATH=.`，`XXM_TESTING=1`）**

- `python -m pytest tests regression_tests -q` → **517 passed, 2 skipped**（513 + 4 项新护栏）。
- `python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json`（cwd=仓库根）→ `modules=250 internal_imports=721 deferred_imports=84 violations=0 import_cycles=0`，exit 0。
- `python -c "import app.main"` → ok。

**三条教训**

1. **「删了记录」不等于「清掉了视图」**：读模型只要有一个字段来自别处（快照/缓存/关联表），清理就必须同时处理这个来源，否则用户看到的是「清理无效」。
2. **派生展示项要标明出处**：`recent_failures` 两类来源的 stage 不同（`end` vs `generated_share_check`），事故现场靠 `stage` 才能一眼分辨；新增派生条目时应保持这个可辨识性。
3. **同名函数遮蔽是静默死循环**：路由 handler 与用例同名时，`from ... import` 被 `def` 覆盖，函数体内的同名调用会递归——**这是 `import app.main` 检查不出来的问题**，靠护栏测试才暴露（第一次跑测试即 `RecursionError` 级别失败）。

**用户侧动作**：改动只在工作区，**正在运行的后端需要重启（本地 uvicorn）或重建镜像（Docker）才生效**；生效后点「清理失败日志」，卡片里的历史检测失败会一起消失，但任务列表的「已失效」徽章仍如实保留。

## 10.26 TMDB「后面几集没数据，刷新也刷不出来」：中文记录缺排期，只有 en-US 记录带排期

**现象**（2026-11-09，用户 m05949）：两部在播剧在 TMDB 网站（zh-CN）的第 12 / 13 集卡片上能看到播出日期，但本站刷新后这些集依然没有播出日期、名称只有「第 12 集」这种占位名，于是判断「刷新没有用」。

**先证明刷新到底写没写**：库副本只读 + audit 双证据——`tmdb_media_cache` 的 `fetched_at` 与用户点击时间一致（18:00:28），`audit_logs` 有对应 `tmdb.cache.refresh / force=True async=False` 记录，`fail_count=0`、`last_error` 空。**刷新链路是通的**，问题在上游数据或我们的取数方式。

**再证明上游到底有没有**（2026-11-09 18:0x 直接打真实 TMDB API，剧 279155 谷雨，第 1 季 30 集）：

| 请求 | 第 11 集 | 第 12 集 | 第 13 集 | 第 14 集 |
| --- | --- | --- | --- | --- |
| `/tv/279155/season/1?language=zh-CN` | air_date=None | None | None | None |
| `/tv/279155/season/1?language=xx`（现有 `BASE_LANGUAGE`） | None | None | None | None |
| `/tv/279155/season/1?language=en-US` | 2026-11-09 | 2026-11-09 | 2026-11-10 | 2026-11-10 |
| TMDB 网站 zh-CN 页面 HTML | — | 2026 年 11 月 09 日 | 2026 年 11 月 10 日 | 2026 年 11 月 10 日 |

对照组（在播剧 307853，24 集）：`language=xx` 与 `language=en-US` 的 air_date **完全一致**（ep4 均为 11-07、ep9-10 均为 11-10），说明「基础记录 xx」并不总是等于 en-US；**谷雨这类中文剧的基础记录同样缺排期，排期只存在于 en-US 记录里**。

**根因**：`_fetch_tmdb_detail` 只拉 `zh-CN` + `language=xx` 两份季记录，两份对未播出的集都是空 `air_date` → 本站永远拿不到 TMDB 网站展示的那份排期。刷新每次都真的重新拉了，只是拉回来的还是空的。

**修复**（`backend/app/application/tmdb_cache.py`，3 个函数 + 1 个常量）：

| 位置 | 改动 |
| --- | --- |
| 模块常量 | 新增 `TMDB_SCHEDULE_LANGUAGE = "en-US"`（只用于补排期） |
| `_season_missing_air_dates(season)` | 判断这一季里是否还有集缺 `air_date` |
| `_fill_missing_air_dates(season, schedule)` | 用 en-US 季记录**只补空值**，返回补齐条数；不动名称/简介/其它事实字段 |
| `_fetch_tmdb_detail` 季循环 | xx 合并后若仍缺日期 → 再拉一次 en-US 季记录并只补空值；日期已完整的季不发这次请求 |

**为什么只补 air_date，不做整份 en-US 替代**：

- en-US 不是「更全的那份」：307853 的 en-US **详情**记录 `next_episode_to_air` 为空、`last_air_date` 还是旧值 11-07，而 zh-CN/xx 是 11-08 ——整份替换会把详情层弄坏；季级只补空值则两边都对。
- 中文名称/简介不能被 en-US 的「Episode 11」覆盖。
- 请求预算：只有「仍缺日期」的季才多 1 次请求（已完整排期的 285625 / 292307 不多打），`TMDBClient` 自身还有 600s 请求缓存。

**护栏**（`backend/tests/test_tmdb_locale_merge.py`，3 项）：

| 测试 | 断言 |
| --- | --- |
| `test_fetch_tv_detail_fills_missing_air_dates_from_en_us_schedule` | ep11/ep12 补成 2026-11-09；ep10 已播日期不被覆盖；名称仍是「第 11 集」；en-US 季请求只发 1 次 |
| `test_fill_missing_air_dates_only_fills_blank_values` | 只补 `None`/缺字段；`schedule=None` 不抛错 |
| `test_no_en_us_season_request_when_dates_are_complete` | 日期完整时不发 en-US 季请求 |

**端到端验证**（真 37 表库副本 + 真实 TMDB API，2026-11-09 18:13）：`get_tmdb_detail_cached(..., force_refresh=True)` → `ok=True`，`seasons_full[0]` 的 ep11=2026-11-09、ep12=2026-11-09、ep13=2026-11-10、ep14=2026-11-10，名称仍是中文「第 11 集」，payload 12,155 → 12,267 B，`update_weekdays` 由残缺样本恢复为 7 天。全量回归 **520 passed / 2 skipped**；门禁 `violations=0 / cycles=0 / 250 模块`。

**顺带的正向效果**：`infer_tv_update_weekdays` 与前端 `frontend/src/utils/dramaSchedule.ts` 都以 `seasons_full` 的 `air_date` 为准 → 追剧日历/「下次更新」的预测同时变准。

**三条教训**：

1. **「刷新没用」要分成两问**：先证明刷新**写没写**（`fetched_at`/audit），再证明**上游有没有**（直接打 API 三种语言）；两者都正常才是产品问题，否则是上游缺数据。
2. **TMDB 同一集有三份记录（翻译 / 基础 xx / en-US），是字段级互补而非包含关系**：合并策略必须「字段级只补空值」，任何整份择一都会在别的剧上翻车。
3. **「网站有、API 没有」不等于我们错了**：截图里的日期是 TMDB 网站侧渲染，占位句「暂无英文版的简介…」本身就是 TMDB 在说没数据；但网站能显示的排期有时只在 en-US 记录里，所以要先查三种语言再下结论。

**用户侧动作**：重启后端（本地 uvicorn 重启 / Docker 重建镜像）后再点刷新，第 11 集起就有播出日期与「下次更新」排期；**名称仍是「第 N 集」、没有简介/剧照**是 TMDB 真的没发布这些字段（en-US 也只有 "Episode 11" 占位名），不是本站在丢数据。

## 10.27 TMDB「刷新不同步官网日期」：季列表整季停在旧排期，单集记录才是事实源

**现象**（2026-11-09，用户 m06129）：「今天是太阳雨」在本站第 9 集的播出日期是 11-10，而 TMDB 官网显示 11-09；点刷新也不会变。缓存证据：`tmdb_media_cache` id=31（tv:307853 / zh-CN）`fetched_at=2026-11-09 17:59:32`、`fail_count=0`、`last_error` 空——**刷新确实写了库**，写进去的值和官网不一致。

**证据链一：官网到底显示什么**（`tools.web_fetch https://www.themoviedb.org/tv/307853/season/1?language=zh-CN`，HTTP 200；卡片用 `data-episode-number` + 「2026 年 11 月 09 日」文本提取）：

| 集 | 官网 | 季列表（xx / en-US / zh-TW） | zh-CN 季列表 |
| --- | --- | --- | --- |
| 1–3 | 11-06 | 11-06 | 11-06 |
| 4–5 | 11-07 | 11-07 | 11-06 / 11-07 |
| 6–7 | 11-08 | 11-08 | 11-07 / 11-08 |
| 8 | 11-09 | 11-09 | 11-08 |
| **9** | **11-09** | **11-10** | None |
| 10–11 | 11-10 | 11-10 | None |
| 12–13 | 11-11 | 11-11 | None |
| 14–15 | 11-12 | 11-12 | None |
| 16 | 11-13 | 11-13 | None |
| 17 | 11-14 | 11-14 | None |
| 18–19 | 11-15 | 11-14 / 11-15 | None |
| 20–22 | 11-16 | 11-15 / 11-15 / 11-16 | None |
| 23 | 11-17 | 11-17 | None |

**证据链二：单集端点与官网逐集一致**。`/tv/307853/season/1/episode/{n}?language=xx` 拉 1..24 集，**没有一处与官网不同**；同一批请求改用 `language=zh-CN`，出现多个 None（ep9/13/15/16/18/21/23）与 ±1 天偏差（ep4=11-06 而官网 11-07、ep11=11-11 而官网 11-10、ep20=11-15 而官网 11-16）。→ **官网集卡片是按单集记录渲染的，季列表只是聚合快照，会整份过期**。

**对照组（防止回退 §10.26）**：279155「谷雨」的 xx 季列表 ep9–16 = 11-08 / 11-08 / 11-09 / 11-09 / 11-10 / 11-10 / 11-11 / 11-11，与单集端点、与官网**完全一致** → 单集校正对它是 no-op。

**根因**：`_fetch_tmdb_detail` 的事实源是「xx 季列表（再经 en-US 补空值）」。当 TMDB 的季列表整季停在旧排期时，本站把旧值当成事实写进 `seasons_full`，而官网按单集记录渲染 → 用户看到「刷了也没变」。`backend/app/application/tmdb_cache.py:375-389` 的 `_merge_episode_with_base` docstring 里「基础记录才是 TMDB 网站显示的那份」这个假设，对 307853 不成立。

**修复**（2 个文件 + 3 个常量）

| 位置 | 改动 |
| --- | --- |
| `backend/app/infrastructure/http/tmdb_client.py` | 新增 `get_tv_episode(tv_id, season_number, episode_number, *, language=None)` → `/tv/{id}/season/{n}/episode/{m}`，沿用既有 language 分支写法 |
| `backend/app/application/tmdb_cache.py` 常量 | `_EPISODE_DATE_VERIFY_LIMIT = 20`（每次刷新最多 20 次单集请求）、`_EPISODE_DATE_VERIFY_WINDOW_DAYS = 45`、`_EPISODE_DATE_VERIFY_STOP_AFTER = 6` |
| `backend/app/application/tmdb_cache.py` | 新增 `_correct_episode_air_dates(season, client, tmdb_id, season_number, *, budget=20, stop_after_no_change=6, today=None) -> int`：候选 = 缺 `air_date` 或落在今天 ±45 天内的集，按距今天数由近到远取前 budget 个，用 `language=TMDB_BASE_LANGUAGE` 拉单集记录，**只覆盖 `air_date`** |
| `_fetch_tmdb_detail` 季循环 | en-US 补空值之后调用校正；`season` 为空/无 episodes 时跳过 |

**关键取舍**

1. **只改 `air_date`**：名称/简介/剧照仍用本地化记录——单集记录同样只有「Episode 9」占位名，整份替换会把中文标题冲掉。
2. **固定 `language=xx` 拉单集**：zh-CN 单集端点有 None 与 ±1 天偏差（见证据链二），基础记录才是干净的那份。
3. **窗口 ±45 天 + 预算 20**：已播很久的季（如完结剧）没有候选集 → 一次请求都不发；老剧刷新成本不增加。
4. **连续 6 集一致就收手**：季列表没跑偏的剧（谷雨这类）只打 6 次请求，而不是把整季翻一遍；实测谷雨刷新耗时 11.59s → 4.26s。
5. **单集请求异常只跳过该集**：个别集失败不能让整次刷新失败（否则一个 404 就让缓存整体过期不了、或把 `fail_count` 打满）。
6. **不改 `_merge_episode_with_base` 的历史注释**：它描述的是「详情层 last/next_episode_to_air」的合并策略，仍然正确；季级排期由新函数兜底，两者分工写进新函数 docstring。

**护栏**（`backend/tests/test_tmdb_locale_merge.py`，新增 5 项；`FakeLocaleClient` 补 `get_tv_episode` 返回与基础记录一致的值）

| 测试 | 断言 |
| --- | --- |
| `test_stale_season_listing_air_dates_are_corrected_from_episode_records` | 季列表按「每晚 2 集」的旧排期、单集记录是「每晚 1 集」→ `seasons_full` 采信单集记录；名称仍是本地化「第 2 集」；校正只请求 `language=xx` |
| `test_episode_verify_skips_seasons_that_aired_long_ago` | 全季在 45 天窗口外 → `episode_calls == []` |
| `test_episode_verify_failure_does_not_break_refresh` | 单集端点全抛错 → 保留季列表原值，整体刷新照常成功 |
| `test_episode_verify_budget_and_window_limit_requests` | `budget=2` 时只校验第 5（缺日期，最优先）、第 4 集；窗口外的第 99 集不请求、值不被改 |
| `test_episode_verify_stops_after_repeated_matches` | 连续 6 集一致即收手：20 集的季只打 6 次请求 |

**端到端验证**（真 37 表库副本 + 真实 TMDB API，2026-11-09 20:2x）

~~~text
=== tmdb_id 307853 ok True spent 8.65 s
  returned:  {6: '2026-11-08', 7: '2026-11-08', 8: '2026-11-09', 9: '2026-11-09',
              10: '2026-11-10', 11: '2026-11-10', 12: '2026-11-11', 13: '2026-11-11',
              14: '2026-11-12', 15: '2026-11-12', 16: '2026-11-13', 17: '2026-11-14',
              18: '2026-11-15', 19: '2026-11-15'}
  persisted: 同上（payload_json 与返回值一致）
=== tmdb_id 279155 ok True spent 4.26 s
  returned:  {10: '2026-11-08', 11: '2026-11-09', 12: '2026-11-09', 13: '2026-11-10',
              14: '2026-11-10', 15: '2026-11-11'}   ← 与 §10.26 修复后一致，未回退
~~~

- 307853 的 ep8/ep9 = 11-09、ep10/11 = 11-10、ep18/19 = 11-15，与官网 HTML 逐集对齐；返回值和落库 payload 一致（写回没丢）。
- 全量回归 **525 passed / 2 skipped**（520 + 5 项新护栏）；门禁 `modules=250 internal_imports=721 deferred_imports=84 violations=0 import_cycles=0`，exit 0；`import app.main` ok。

**四条教训**

1. **同一集有四份记录，不是三份**：翻译记录、基础记录（xx）、en-US 记录、**单集记录**。前三个是「季级聚合」，会整份过期；单集记录最细，网站按它渲染 → 需要精确排期时只能问单集端点。
2. **「季列表说 A、官网说 B」要先怀疑聚合层**：本次两侧都没有报错、`fetched_at`/`fail_count` 全部正常，坏的是「我们选了哪个事实源」。
3. **上游数据不可信要用成本三件套兜住**：窗口（只查近 45 天）+ 预算（≤20 次请求）+ 早收手（连续 6 集一致即停），否则日常刷新会被 30 集×多次的请求拖慢。
4. **对照组是回归保险**：改数据源前先找一部「排期本来就对」的在播剧（谷雨），修完立刻验证它没被改坏；§10.26 的 en-US 补空值仍然有效，两者是叠加关系不是替代。

**用户侧动作**：重启后端（本地 uvicorn 重启 / Docker 重建镜像）后，点「强制刷新」即可看到与官网一致的逐集日期。代价：正在连载、近 45 天内多集待播的剧，手动强制刷新会多花几秒（最多 20 次单集请求；季列表没跑偏时 6 次就收手），后台定时刷新不受影响。

---

## 10.28 「已删除的分享仍显示有效」：检测链路漏掉了分享页错误（2026-11-10）

**现象**：用户拿本系统生成的百度分享 `https://pan.baidu.com/s/1liYN70njkafHvRKCyXZNIA?pwd=6666`（`pwd=6666` 是 `create_share_link` 的硬编码，确系本系统产物）在浏览器打开，百度页标题是「百度网盘-链接不存在」；但任务列表里「有效」徽章一直是绿色，点「执行」之后依然显示有效。

**证据链（四级，全部实测）**

| 步骤 | 观测 | 结论 |
| --- | --- | --- |
| 1. 百度侧事实 | 死链页面 `<title>百度网盘-链接不存在</title>`（12,727 B）；对照有效链接 `https://pan.baidu.com/s/1FDxqzujCQy_sZIss3NS3Qw?pwd=1111` 是 `<title>百度网盘 请输入提取码</title>`（15,645 B） | 分享确实已不存在，不是提取码错、也不是网络问题 |
| 2. `get_stoken` | 百度 `share/verify` 对已删除的分享同样返回 `{"errno": 0, "err_msg": "", "randsk": "..."}`，`get_stoken` 随后无条件返回 `stoken = f"{pwd_id}:{passcode}"` | **验证阶段天然不可靠**：`check_errno=True` 也拦不住（errno 就是 0） |
| 3. 分享页 JSON | `_api_shared_paths()` 解析出的 `yunData.setData`：死链 `errno=-7, share_page_type="error", file_list=[]`；有效链接 `errno=0, file_list=[嫁丨金钗（4K）]` | 失效信号**只**出现在分享页这一层 |
| 4. `get_detail` | 旧代码只读 `file_list`，空列表时直接 `return {"code": 0, "message": "success", "data": {"list": []}}`，把 `errno=-7` 丢掉 | `gateway.probe_share` 看到 `code=="0"` 且 `list` 是 list（空列表也是 list）→ 判「有效」 |

**两层修复（都必要，缺一层就还有死链能漏过去）**

1. **适配器层透传分享页错误**（`backend/app/infrastructure/drive/adapters/baidu_adapter.py`）
   - 新增 `_shared_page_error(shared_data)`（:682）与分享页 errno 文案表 `_SHARED_PAGE_ERRNO_MESSAGES`（:674）：`share_page_type == "error"` 或 `errno` 非 0 即返回 `{"code", "message"}`。**`-7` 在分享页是「分享不存在/已被取消」**，与 `ERROR_CODES[-7]`（分享接口的「等待审核中」）语义不同，单独映射，避免把死链说成「审核中」。
   - `get_detail`（:986-996）拿到 `shared_data` 后先查 page error，命中直接返回 `code=errno` + 文案，不再把空列表当成功。
   - `save_file`（:1180-1181）同样前置拦截：死链**不进入转存 API**，直接返回错误，避免「没转存任何文件却算成功」。
2. **端口层纵深防御**（`backend/app/infrastructure/drive/gateway.py:167-181`）
   - `probe_share`：`code=="0"` 且目录**非空**才算 `valid`；`code=="0"` + 空列表 → `ShareProbeResult(valid=False, kind="invalid", message="分享目录为空或链接不存在")`。这一层与具体网盘无关，任何网盘返回「成功但空目录」都会被判不可用。

**为什么不是「直接把 -7 加进 SHARE_INVALID_ERRNOS」**：`-7` 在不同接口语义相反（分享页=不存在/取消，文件接口=审核中）。加进 `SHARE_INVALID_ERRNOS` 会让「真的在审核」的分享被判失效；本次做法是「分享页这一层给出准确文案」，`classify_share_failure` 靠文案里的「不存在」自然归到 `invalid`（message 分支），审核中的 `-7` 原样仍是 `pending`。

**护栏（`backend/regression_tests/test_baidu_share_page_errno.py` 5 项 + `test_gateway_port.py` 1 项）**

| 测试 | 断言 |
| --- | --- |
| `test_get_detail_propagates_share_page_errno` | 分享页 `errno=-7` → `get_detail` 的 `code` 必须是 -7，文案含「不存在/取消」，列表为空 |
| `test_get_detail_treats_share_page_type_error_as_invalid` | 只有 `share_page_type=error`（无 errno）→ 也判失败 |
| `test_get_detail_keeps_normal_page_success` | 无失效信号的空目录仍返回 `code=0`（不误杀；可用性由端口层判定） |
| `test_save_file_refuses_dead_share` | 死链调 `save_file` 直接返回错误，不发转存请求 |
| `test_gateway_probe_marks_dead_baidu_share_invalid` | 真 `BaiduAdapter` + 端口 → `valid=False, kind="invalid", stage="read"` |
| `test_probe_share_rejects_empty_list_even_when_code_is_zero`（网关护栏） | 任意网盘 `code=0` + 空列表 → 不可用 |

**两个测试夹具的旧假设一并修正**（本次最有价值的发现之一）：`test_gateway_port.py` 的假适配器默认返回 `{"code": 0, "data": {"list": []}}`、`test_generated_share_check.py` 的假 `detail` 也返回空列表，并且都断言「探测成功」——**测试夹具本身在固化这个 bug**。改成返回「有一集视频」的目录后，语义才和真实分享一致。

**真实链接验收（本机真账号，2026-11-10 11:2x）**

~~~text
[Baidu] 分享页标记失效 errno=-7：https://pan.baidu.com/s/1liYN70njkafHvRKCyXZNIA
DEAD: valid=False kind=invalid stage=read message=分享链接不存在或已被取消
GOOD: valid=True  kind=valid   stage=read message=
~~~

修复后全量 **531 passed, 2 skipped**（525 + 6 项新护栏）；门禁 `modules=250 internal_imports=721 deferred_imports=84 violations=0 import_cycles=0`，exit 0；`import app.main` ok。

**影响面与回退**

- 无灰度开关：这是判定正确性修复，不是性能/行为优化。回退方式 = 还原 `get_detail` 的 page-error 分支。
- 对外契约不变：`ShareProbeResult` 仍是 `(valid, kind, message, stage)`；前端 `GeneratedShareStatus.vue` 只按 `valid/invalid/error` 上色，`message` 进 tooltip → 死链现在显示「已失效」（红），悬停看到「分享链接不存在或已被取消」。
- 行为变更点（预期内）：**「成功的空目录分享」不再判有效**。本系统只在目录非空时创建分享（`create_share_link` 先列目录校验），自有链路无影响；外部空目录分享对转存本来也无价值。
- 用户侧动作：重启后端（或重建镜像）后，对相关任务**再点一次「执行」**，徽章才会刷新成「已失效」；历史执行记录里的旧快照不会自动纠正。
- 遗留（已补上）：`create_share_link` 创建后不自检的问题已在下一节补掉 → 见 §10.29。

**四条教训**

1. **「验证通过」不等于「分享存在」**：百度 `share/verify` 对已删除分享也回 errno=0，任何只依赖 verify 的判定都会漏；必须落到「能不能列出内容」这一层。
2. **空集合不能当成功**：`list: []` 被 `isinstance(..., list)` 判真，是最典型的假阳性写法；凡「有内容才算成功」的地方都要显式判非空。
3. **测试夹具会固化 bug**：夹具默认空目录却断言成功，等于把 bug 写进了护栏；修 bug 时必须回头检查夹具是不是「照着 bug 写的」。
4. **同名错误码在不同接口要分开映射**：`-7` 在分享页与文件接口语义相反，一张全局错误码表兜不住。

---

## 10.29 生成分享后立刻自检：不再把死链交到用户手里（2026-11-10）

**需求**（用户原话）：「生成完立刻用同一 Cookie 验一遍，失败就报错而不是返回死链。」

§10.28 修的是「已有分享被判有效」，这一节补的是源头：`create_share_link` 创建成功后**没有任何验证**，百度侧即便返回了链接、后面又变成死链（或创建时就被限流/风控），用户点「生成分享」拿到的就是一个打不开的地址。历史死链 `1liYN70njkafHvRKCyXZNIA` 极可能就是这种产物。

**实现（`backend/app/infrastructure/drive/adapters/baidu_adapter.py`）**

```text
create_share_link()                        # :1458
  ├─ _api_list / ls_dir 校验目录非空        # 原有：空目录不创建分享
  ├─ POST share/set（先 period=0，失败退 7 天）# 原有
  └─ _verify_created_share(share_page_url, passcode)   # :1408 新增
       ├─ _api_access_shared → _api_shared_paths       # 同一个 Cookie 读分享页
       ├─ _shared_page_error(shared_data)              # 复用 §10.28 的分享页错误识别
       └─ 三态：valid / invalid / unknown
```

关键设计是**只对「确定性证据」报错**（否则会把网络抖动变成用户可见的失败）：

| 三态 | 判定依据 | 调用方行为 |
| --- | --- | --- |
| `valid` | 分享页无错误且 `file_list` 非空 | 返回链接（正常路径） |
| `invalid` | 分享页 `errno ∈ {-1, -7, -9, 105, 145}`（`_SHARE_VERIFY_FATAL_CODES`，:1406）或 `share_page_type=error` | **抛 `RuntimeError`**：`分享创建后自检未通过：分享链接不存在或已被取消…`（:1522-1534），路由转成 `TASK_SHARE_FAILED` 错误提示，链接不写入任务 |
| `unknown` | 读页面抛异常、解析不到数据、或「无错误但暂时空列表」 | **放行**并 `logger.warning`（:1535-1538）——读不到不能证明链接死了 |

**为什么要重试**：百度侧新建分享有时要几秒才可读（这也是为什么不能「一次读不到就报失败」）。`_SHARE_VERIFY_ATTEMPTS = 3`、`_SHARE_VERIFY_DELAY_SECONDS = 0.8`（退避 0.8s/1.6s，共约 2.4s）。重试期间任何一次读到 `valid` 立即返回；只有「重试后仍是 invalid」才报错。

**真实链接实测（本机真账号 id=2「百度」，`attempts=1` 直调自检函数，未创建新分享）**

~~~text
GOOD -> ok=True  state=valid   msg=
DEAD -> ok=False state=invalid msg=分享链接不存在或已被取消
~~~

**护栏（`backend/regression_tests/test_create_share_self_check.py`，9 项）**

| 分组 | 断言 |
| --- | --- |
| 三态语义 | 有内容→valid；分享页 -7→invalid；读页面抛异常→unknown **且放行**；无错误但空列表→unknown；第 3 次才可读→valid（验证重试，`call_count==3`） |
| 创建链路 | 自检通过→返回 `?pwd=6666` 链接，且自检访问的是**不带提取码参数**的分享页地址；自检判死链→`RuntimeError` 含「自检未通过」+「不存在」；自检读不到→仍返回链接；`share/set` 本身失败→**不做自检**（`_api_shared_paths.assert_not_called`） |

同时把 `backend/tests/test_baidu_task_share.py` 的 `setUp` 补了自检所需的假分享页（`_api_access_shared` + `_api_shared_paths`），4 项原有测试语义不变。

**代价与影响面**

- 每次「生成分享」多 1~2 次 HTTP GET（分享页），失败路径最多 3 次、约 +2.4s；成功路径通常 1 次、几百毫秒。
- 对外契约变化（预期内）：以前「创建成功但链接不可读」返回 200 + 死链，现在返回错误提示。前端 `生成分享` 按钮已有失败提示分支，无需改动。
- 回退方式：删掉 `create_share_link` 里那一行 `_verify_created_share(...)` 调用即可，自检函数本身不影响其它链路。

**遗留**

- 只做了百度。夸克/其它网盘的 `create_share_link` 走的是各自的分享接口（`backend/app/infrastructure/drive/adapters/quark_adapter.py:426`），目前没有「生成即死链」的证据，暂不动；要加时按同样三态模式实现即可。
- 自检失败时，百度侧其实**已经创建了分享**（挂在账号里，30 天后才被清理或一直占位）。我们在日志里留了 `share=<url>` 便于排查，但没做自动清理（取消分享需要额外接口 + 风控风险）。若后续发现残留过多，可在失败分支补一次 `share/cancel`。

---

## 10.30 追剧任务「不到运行日期不能点执行」：一处判定、三处同源（2026-11-10）

**需求**（用户原话）：「不到设定的日期不能点击执行」→「追剧任务里面的」。
即：追剧任务列表里今天**不在「运行星期」**范围内时，「执行」按钮置灰；即便绕过前端，后端两个手动执行入口也要拒绝。

> **口径已修正（2026-11-10 12:40，用户澄清 m07014）**：上面这条只实施了半小时就被推翻——用户原话是
> 「**我的意思是希望不到设定的日期也能点击执行，并且执行**」。现行语义：**手动执行无视运行星期**
> （按钮永远可点、路由不 409、执行器不 SkipTask），运行星期只约束 APScheduler 定时轮询。
> 修正细节见 **§10.30.8**；本节 §10.30.1–10.30.7 保留为第一版实现的历史记录。

### 10.30.1 为什么这不是「加个 if」

改造前，「今天能不能跑」这套判定**只存在于执行器内部**（`DramaExecutor.execute()` 的 `validate_schedule` 段，原 `backend/app/infrastructure/execution/drama_executor.py:669-716`），
而且是在**已经占了准入槽、已经起了执行线程之后**才失败，用户看到的是「点了执行 → 日志里出现一条 SkipTask」。同时：

- 列表页拿不到这套判定（响应里没有该字段），前端只能让按钮永远可点；
- 「执行全部」按 `enabled` 过滤，不按运行星期过滤，等于把判断全推给后台；
- 判定口径有两套可选输入（`allow_once` 兼容开关、auto 模式用 TMDB 更新日），散落在 if/else 里，任何一处改都会和其它入口漂移。

所以做法是**把判定抽成一份纯函数，三个入口 + 列表读模型全部调它**，执行器只保留日志文案。

### 10.30.2 判定口径（唯一事实源：`backend/app/application/drama_schedule.py`）

| 情形 | allowed | reason | 前端提示（message） |
| --- | --- | --- | --- |
| 非追剧任务（transfer/share 等） | 是 | `not_drama` | 普通任务不受运行日期限制 |
| `extra.allow_once = true`（历史兼容字段，前端不再写入） | 是 | `allow_once` | 任务已设置运行一次，忽略运行日期限制 |
| manual + `runweek` 为空 | 是（按每天） | `no_runweek` | 未设置运行星期，按每天运行 |
| manual + `runweek` 配置但全部非法 | 否 | `invalid_runweek` | 运行星期设置为空或无效，已阻止执行 |
| manual + 今天命中 | 是 | `due` | 今天是运行日期：周二（周一、周二…） |
| manual + 今天未命中 | 否 | `off_day` | 今天（周二）不在设定的运行日期（…)内 |
| auto + 未绑定 TMDB 电视剧 | 否 | `auto_not_tv` | 运行日期为自动模式，需要任务绑定 TMDB 电视剧 |
| auto + TMDB 未配置 API key | 否 | `tmdb_not_configured` | TMDB 未配置，无法自动识别更新日 |
| auto + 未识别到更新日 | 是（按每天） | `no_update_day` | 未识别到更新日，按每天运行 |
| auto + 单集播出日/整季更新日未命中 | 否 | `off_day` | 今天（周二）不在设定的运行日期（周五）内 |

关键点：

1. **auto 模式的更新日只读本地缓存**（`tmdb_media_cache.payload_json` / `update_weekdays_json`），走 `get_latest_schedule_row` 单表单行读取，**不发任何 TMDB 请求**——列表接口不能因为判定就把 10 个任务打成 10 次外部 HTTP。
2. **读不到缓存 → 按每天放行（fail-open）**：宁可多跑一次（执行器自己还有 `saved_latest` 去重），也不能因为缓存缺失把用户的按钮全禁掉。反过来，只有「读到确定的更新日且今天不在其中」才禁用。
3. 单集播出日优先于整季更新日（`infer_tv_episode_weekdays_from_details`，与 §10.27 的口径一致），取不到再回落 `update_weekdays_json`。

### 10.30.3 落地结构（分层归属）

| 位置 | 职责 |
| --- | --- |
| `backend/app/application/drama_schedule.py`（新增，12.7 KB） | `RunSchedule` frozen dataclass + `resolve_run_schedule()`（纯函数，可传 `now`）+ `resolve_item_run_schedule()`（读缓存分支）+ `ensure_run_allowed()`（不允许时抛 409） |
| `backend/app/application/dto/task.py` | 新增 `DramaRunScheduleOut(allowed, mode, weekdays, reason, message)` |
| `backend/app/application/queries/task_views.py` | `TaskView.run_schedule`；`list_task_views` **一次**批量算好（`load_tmdb_weekday_map` + 惰性 `is_tmdb_configured`），任务数不影响查询条数 |
| `backend/app/application/tmdb_view.py` | 新增 `load_tmdb_weekday_map(db, items, payload_map=...)`：先用已有 payload 算，缺的再 1 条 SQL 回落 |
| `backend/app/application/tmdb_settings.py` | 新增 `is_tmdb_configured(db)`，用 `db.info` 做会话级缓存（同一请求只读一次设置） |
| `backend/app/infrastructure/persistence/repositories/tmdb_media_cache.py` | 新增 `load_update_weekday_rows`（批量）与 `get_latest_schedule_row`（单行，不整行加载 payload） |
| `backend/app/api/presenters/task.py` | `task_out` 输出 `run_schedule`（缺省 `DramaRunScheduleOut()`） |
| `backend/app/schemas/task.py` | `TaskOut` 末尾新增 `run_schedule` 字段（追加在末尾，保持既有字段顺序不变） |
| `backend/app/infrastructure/execution/drama_executor.py` | 原 `validate_schedule` 段整段改调 `resolve_run_schedule()`，**日志文案逐字保留**（「运行一次: 忽略按星期运行限制」「未配置运行星期: 默认允许运行」「未识别到更新日，按每天执行」「跳过: 星期 N 不在 [...]」），不允许时 `raise SkipTask(decision.skip_reason)` |
| `backend/app/api/routes/tasks.py` / `tasks_stream.py` | 两个手动入口在 `reserve()` 之后、起线程之前调 `ensure_run_allowed`，不允许直接 409 |

**409 契约**：`code = "TASK_RUN_NOT_SCHEDULED"`、`http_status = 409`、`message = "{判定原因}，已阻止执行。如需立即执行，请编辑任务的「运行星期」。"`。
错误体沿用统一 `{code, message, detail}`，前端 `readTaskRunError` 能把这句话直接弹出来。

**预览端点不拦**：`POST /api/tasks/{id}/run/stream`（按 payload 的试运行，`RunPolicy.for_trigger('preview')`）保持可用——它不写执行记录、只是预览目录，属于「先看看再决定」的入口，拦住反而挡住用户排查。

### 10.30.4 前端

- `frontend/src/types/tasks.ts`：`TaskItem.run_schedule?: TaskRunSchedule | null` + `export type TaskRunSchedule = { allowed; mode; weekdays; reason; message }`。
- `frontend/src/views/tasks/DramaTaskView.vue`：三处「执行」按钮（表格两分支 + 移动卡片）统一 `:disabled="!isRunAllowed(row)"` + `:title="runBlockedReason(row)"`；
  `handleRun` 开头再做一次兜底（键盘/脚本触发也不会发请求）；「执行全部」改为只提交 `allowed` 的任务，并在确认框里说明「本次将执行 N 个任务，另有 M 个任务今天不在运行日期、已自动跳过」。

**（现行，见 §10.30.8）** 上述禁用逻辑已全部回退：按钮永远可点、`handleRun` 不再拦截、「执行全部」恢复按 `enabled` 过滤；
`run_schedule` 字段仅保留为展示信息（列表仍能看到「今天不在运行日期」），不再作为禁用依据。

### 10.30.5 真实数据验证（不是只跑单测）

把 `backend/data/app.db` 用 sqlite3 backup API 复制到 `.tmp`（不动生产库），`XXM_TESTING=1 DATABASE_URL` 指向副本后实测：

| 场景 | 结果 |
| --- | --- |
| 线上 10 个追剧任务（全部 manual、运行星期 = 周一…周日） | 10/10 `allowed=True, reason=due`，文案「今天是运行日期：周二（…）」 |
| 把某任务 `runweek` 改成「除周二外」后重跑列表读模型 | `allowed=False, reason=off_day`，文案「今天（周二）不在设定的运行日期（周一、周三…）内」 |
| 同上调 `ensure_run_allowed`（第一版的路由前置拦截，**已于 §10.30.8 删除**） | 曾抛 `ApiError`：`TASK_RUN_NOT_SCHEDULED` / 409 / 完整中文提示 |
| 改成 auto 模式 + 真实缓存 `tv:121876`（`update_weekdays_json=[5]`，周五更新） | `allowed=False, reason=off_day, weekdays=[5]`，文案「今天（周二）不在设定的运行日期（周五）内」——**证明 auto 分支读的是真实缓存且没发网络请求** |

### 10.30.6 一个被「假通过」掩盖的坑（重要）

第一轮改完跑测试是 551 绿，我一度以为收工了；实际上 `TaskOut` 里的 `run_schedule` 字段**没写进去**（字符串替换静默 no-op），而 `task_out(run_schedule=...)` 仍然「成功」——
因为 **pydantic v2 默认 `extra="ignore"`**，多传的构造参数会被静默丢掉，单侧契约测试（`model_dump()` 快照）也跟着「通过」。

真正把它抓出来的是**双侧对齐测试** `test_frontend_contract_golden.py::test_frontend_and_backend_field_sets_are_aligned`（前端类型有 `run_schedule`、后端 `TaskOut.model_fields` 没有 → 报「前端多出未登记字段」）。
教训：

1. 「响应模型多了/少了字段」只能靠**读 `model_fields` 的双侧对齐**发现，`model_dump()` + pydantic 默认 `extra=ignore` 会把问题吃掉；
2. 改文件后必须**回读校验**（这次已在 `tasks.py`/`tasks_stream.py`/`schemas/task.py` 三处分别补验）；
3. 契约变更后重建 golden（`UPDATE_CONTRACT_GOLDEN=1`）并把 diff 一起提交，否则下一次改动的红/绿都不可信。

### 10.30.7 验证结果

| 项 | 结果 |
| --- | --- |
| 全量测试 | **552 passed, 2 skipped**（第一版：新增 `backend/regression_tests/test_drama_run_schedule.py` 12 项，含「两个手动入口都 import 并调用同一个 guard」的静态断言；现行 13 项见 §10.30.8） |
| 分层门禁 | violations **0**（基线 0）、cycles **0**、modules 251、deferred 90、`exit 0` |
| 前端类型 | `npx vue-tsc -b` → `EXIT=0` |
| 契约 golden | `test_task_presenter_contract.py` 字段顺序末尾追加 `run_schedule`；`golden/frontend_contract.json` 重建 |

**回退方式（第一版）**：前端把三处 `:disabled` 去掉即可恢复「永远可点」；后端删掉两个 `ensure_run_allowed(...)` 调用即回到「执行器内部 SkipTask」的旧行为。判定模块本身无副作用，可独立保留。

### 10.30.8 口径修正：手动执行「无视运行星期」（2026-11-10 12:40，用户澄清后重做）

**用户原话**（m07014）：「我的意思是希望不到设定的日期也能点击执行，并且执行」。

第一版把需求理解成「拦」，实际要的是「放」：**运行星期只约束自动轮询，不约束人点按钮**。判定模块（`drama_schedule.py`）保留，改为「供展示与自动轮询使用」；拦截链路整条撤掉。

| 场景 | 第一版（已废弃） | 现行 |
| --- | --- | --- |
| 列表「执行」按钮 | `allowed=false` 时置灰 + tooltip | 永远可点（`run_schedule` 仅展示说明） |
| `POST /api/tasks/{id}/run` | 前置 `ensure_run_allowed` → 409 `TASK_RUN_NOT_SCHEDULED` | 不拦截，直接执行 |
| `POST /api/tasks/{id}/run/stream` | `_prepare()` 内 409，占槽前拒绝 | 不拦截，直接执行 |
| 执行器（手动：http/sse/telegram/preview） | `SkipTask("不在允许运行的星期范围内")` | **越过日期类拦截**，日志「手动执行: 忽略运行日期限制（…）」，继续正常转存 |
| 执行器（APScheduler 定时轮询） | 跳过 | **不变**，仍按运行星期跳过 |
| 自动模式下「未绑定 TMDB TV / TMDB 未配置 key」 | 跳过 | **不变**（手动也跳过：配置缺失，放行只会空跑） |

**改动清单**

| 文件 | 改动 |
| --- | --- |
| `backend/app/application/drama_schedule.py` | 删 `ensure_run_allowed()` + `RUN_NOT_SCHEDULED_CODE` + `ApiError` import；新增 `MANUAL_OVERRIDE_REASONS = {off_day, invalid_runweek}`、`manual_override_allows(decision)`、`requires_skip(decision, manual_run)`（执行器唯一跳过口径，避免执行器自己再拼一遍条件） |
| `backend/app/application/use_cases/run_task.py` | `RunPolicy` 新增 `manual_run: bool = True`：真值表 `scheduler=False`，`http/sse/telegram/preview=True`；统一路径与 `_legacy_execute()` 两条路径都把它传给执行器 |
| `backend/app/infrastructure/execution/task_executor.py` | `run_task()` 新增 `manual_run: bool = False` 参数；drama 分支写 `task_data["manual_run"] = bool(manual_run)` |
| `backend/app/infrastructure/execution/drama_executor.py` | `validate_schedule` 段改用 `requires_skip(decision, manual_run)`；手动放行分支打日志「手动执行: 忽略运行日期限制（{message}）」；旧日志文案（运行一次 / 未配置运行星期 / 未识别到更新日 / 跳过: 星期 x 不在 y）全部保留 |
| `backend/app/api/routes/tasks.py`、`tasks_stream.py` | 删 `from app.application.drama_schedule import ensure_run_allowed` 与调用点，恢复原流程（准入 → 取任务 → 执行） |
| `frontend/src/views/tasks/DramaTaskView.vue` | 三处按钮撤 `:disabled`/`:title`；删 `isRunAllowed()`/`runBlockedReason()` 与 `handleRun` 兜底；`confirmRunAll()` 恢复「只按 enabled 过滤」，确认文案改为「共 N 个任务（手动执行不受运行星期限制）」 |
| `frontend/src/types/tasks.ts` | `run_schedule` 注释改为「仅用于说明今天不在运行日期；手动执行不受其限制」 |
| `backend/regression_tests/test_drama_run_schedule.py` | 删「两个入口都调 guard」的静态断言；新增 `manual_override_allows` 越权范围、`requires_skip` 真值、`resolve_item_run_schedule` 行级判定、以及「策略→执行器→drama 执行器」接线（含 `ensure_run_allowed`/`TASK_RUN_NOT_SCHEDULED` 必须消失）共 13 项 |
| `backend/regression_tests/test_run_task_use_case.py` | 真值表加第三列 `manual_run`；两条路径的假执行器实参断言同步更新 |

**保留 `run_schedule` 字段的理由**：① 列表页仍要能告诉用户「今天不在运行日期」（否则用户不知道为什么定时轮询没跑）；② 定时轮询的跳过仍走它；③ 前端契约 golden 已冻结该字段，删掉是更大的契约变更。

**为什么配置类缺失手动也不放行**：`auto_not_tv` / `tmdb_not_configured` 是「数据没准备好」，不是「今天不该跑」。放行只会走到执行器深处空跑（没有 TMDB 就没有单集排期可比对），对用户是更差的体验。

**执行器级真机探针**（假适配器 + 今天=2026-11-10 周二 + 任务 runweek=[1] 周一）：

~~~text
today iso weekday: 2
manual_run= None -> SkipTask: 不在允许运行的星期范围内 | log: ['跳过: 星期 2 不在 [1]']
manual_run= False -> SkipTask: 不在允许运行的星期范围内 | log: ['跳过: 星期 2 不在 [1]']
manual_run= True  -> RuntimeError: get_fids failed（假适配器没有该方法） | log: ['手动执行: 忽略运行日期限制（今天（周二）不在设定的运行日期（周一）内）']
~~~

第三行说明：跳过逻辑**确实被越过**，代码继续走到了分享解析后的取文件列表阶段（假适配器缺方法才报错），不是「换了个地方再 Skip」。

**验证结果**

| 项 | 结果 |
| --- | --- |
| 全量测试 | **553 passed, 2 skipped**（`--basetemp=.tmp/pytest`，cwd=`backend`，`PYTHONPATH=.`，`XXM_TESTING=1`） |
| 分层门禁 | violations **0**、cycles **0**、modules 251、internal_imports 732、deferred 90、`exit 0`（应用层只新增判定函数，无新跨层边） |
| 前端类型 | `npx vue-tsc -b` → `EXIT=0` |
| 契约 golden | 未变（`run_schedule` 字段与取值语义不变，只是不再驱动禁用） |

**回退方式（现行）**：`RunPolicy.for_trigger("http").manual_run = False` 一行即可让手动执行重新受运行星期约束（所有入口共用同一真值表），无需改执行器。

---

## 附录 A：现状 → 目标目录映射（可直接当搬迁清单）

| 现状路径 | 目标路径 | 所属阶段 | 备注 |
| --- | --- | --- | --- |
| `backend/app/models/`（26 个） | `app/infrastructure/persistence/models/` | S5（逻辑上 S1 即归 persistence） | 先修 `db/base.py:7` 的 SCC |
| `backend/app/db/session.py` | `app/infrastructure/persistence/session.py` | S3 | 保留 `get_db()` 依赖签名 |
| `backend/app/services/task_serialization.py` | `app/api/presenters/task.py` | S4 | 纯函数，移到表现层 |
| `backend/app/services/tasks.py`（执行部分） | `app/application/use_cases/run_task.py` | S2 | 保留 shim |
| `backend/app/services/{drive_accounts,resource_search,share_preview_batch,...}` | `app/application/use_cases/` 或保留在 services 但只依赖端口 | S3/S4 | 按模块判断 |
| `backend/app/extensions/runtime/task_admission.py` | `app/core/runtime/admission.py` | S1 | 旧路径 shim |
| `backend/app/extensions/runtime/task_executor.py` | `app/application/use_cases/` 的执行器实现 + `infrastructure/drive/` 调用 | S2 | 拆分编排与适配 |
| `backend/app/extensions/runtime/task_scheduler.py` | `app/infrastructure/scheduling/scheduler.py` | S4 | 只调用用例 |
| `backend/app/extensions/adapters/*`（10 家，13 文件） | `app/infrastructure/drive/adapters/*` | S4（可延后） | 每网盘一个 PR |
| `backend/app/extensions/adapters/adapter_factory.py` | `app/infrastructure/drive/factory.py` | S4 | 改注册表 |
| `backend/app/thirdparty/{douban,tmdb}_client.py` | `app/infrastructure/http/` | S4 | 包端口 |
| `backend/app/services/notifications/` | `app/infrastructure/notifications/` | S4 | 发送侧 |
| `backend/app/services/telegram_bot/`（handlers 2364 行） | `app/infrastructure/telegram/` + `app/application/use_cases/` | S4/S5 | 先按 §3.5 改为调用用例 |
| `backend/app/core/{settings,errors,logging,metrics,...}` | `app/core/`（原地） | — | 已符合 platform 层 |
| `backend/app/core/deps.py`（违规 4 处） | 拆为 `app/api/deps.py`（认证/权限依赖）+ 权限查询走端口 | S3 | 消除 platform→persistence |

## 附录 B：分层违规证据与复现命令

~~~bash
cd C:/Users/34918/Desktop/综合自动转存/cloud-save-max
python scripts/check_layering.py --json > backend/.tmp/layering.json
python scripts/check_layering.py            # 人类可读：按层对汇总 + 违规明细(file:line) + SCC
python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json
~~~

当前输出摘要（2026-11-08 实测，最近一次）：`modules=206, internal_imports=630, deferred_imports=35, violations=162, import_cycles=1, largest_cycle=27`。逐层对：application→persistence 60、presentation→persistence 38、application→infrastructure 30、presentation→infrastructure 23、application→presentation 10、platform→persistence 4。最大环 27 个模块，根因 `backend/app/db/base.py:7` 文件尾部 `import app.models`。

## 附录 C：`check_layering.py` 用法与 CI 接入

**规则来源**：脚本内 `ALLOWED` 字典即 §3.1 表格的代码化；`LAYER_OF_PREFIX` 是 §3.2 映射表的代码化。修改架构规则 = 改这两处，评审时一目了然。

**CI 片段**（新增文件，不改动现有 `docker-publish.yml`）：

~~~yaml
# .github/workflows/backend-ci.yml
name: backend-ci
on: [push, pull_request]
jobs:
  backend:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: '3.13' }
      - run: pip install -r backend/requirements.txt
      - name: 分层预算（只降不升）
        run: python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json
      - name: 后端回归
        working-directory: backend
        env: { XXM_TESTING: '1' }
        run: python -m pytest tests regression_tests -q
~~~

**本地前置检查**（不引入 pre-commit 依赖，用 git hook 脚本即可）：

~~~bash
# .git/hooks/pre-push  （chmod +x，可选）
#!/bin/sh
python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json || exit 1
~~~

---

## 附录 D：需求对照（逐条回应）

| 用户要求 | 对应章节 | 结论 |
| --- | --- | --- |
| 查看后端代码后，**依旧使用当前技术栈** | §8、§3.4 | 全部用 FastAPI/SQLAlchemy/Alembic/APScheduler/threading/cachetools/标准库实现，**零新依赖** |
| 痛点 1：分层混乱、业务与基础设施强耦合、改一处引发连锁 Bug | §1.1–§1.5、§2、§3.1–§3.3、§4、S2/S3 | 165 处违规已量化并给出预算归零路径；6 份执行副本收敛为 1 个用例；`RunPolicy` 消除入口间行为分叉（§3.5）；事务边界统一到 UoW（§3.6） |
| 痛点 2：扩展性受限、缺抽象、新功能接入成本高 | §3.4 ports、§3.8、§4.2、S4 | 5 个 Protocol 端口 + 注册表；新增网盘从「改 factory + 多处判断」变为「1 个文件 + 1 行注册」 |
| 痛点 3：核心链路缺规范缓存/锁/异步 | §3.6、§3.7、§4.1 | 缓存/锁/准入/单飞 4 类组件统一；事务三规则；`is_sqlite_locked_error` 接成重试 |
| 要求 1：渐进式路线图（平滑过渡、不影响现有业务） | §5（S0–S5 + 每期回滚开关）、§9 | 每期独立上线/回滚；单进程内 flag 控制；主分支随时可发布 |
| 要求 2：规范目录结构与分层设计 | §3.1–§3.3、附录 A | 四层 + 横切层 + 组合根；完整目标目录树与逐模块映射 |
| 要求 3：最关键技术指标与风险规避策略 | §6（4 组 40+ 项指标）、§7（19 项风险 R1–R19） | 每项指标含基线/目标/采集/门禁；每项风险含信号/概率/规避/兜底 |


---

## 附录 E：配套审计报告（证据底稿）

主方案里的每个数字与每个 `file:line`，都能回溯到下面三份并行审计。它们**不进主文档正文**，用于评审与被质疑时对证；主文档只保留结论与决策。

| 审计文档 | 规模 | 覆盖内容 | 主要支撑章节 |
| --- | --- | --- | --- |
| [audit-api-domain.md](architecture/audit-api-domain.md) | 308 行 / 33.2 KB | 路由越界 R1–R7、commit 分布（路由 89 / 服务 81 / 扩展 16）、三份领域规则、schema 与前端契约黑洞、top 8 模块「现状→目标分层」映射、S0–S6 拆分顺序 | §1.2、§1.3、§1.7、§4.1、§4.3、附录 A |
| [audit-runtime-concurrency.md](architecture/audit-runtime-concurrency.md) | 320 行 / 33.9 KB | 线程模型与上界、连接池数值模型、25 个锁清单、15 项进程内状态、SQLite 4 套重试实现、幂等/单飞矩阵、异常吞噬 9 处、多 worker 7 个阻塞点、22 条风险 R01–R22 | §1.4、§2 第 7 条、§3.7、§6.3、§7（R13–R18） |
| [audit-data-observability.md](architecture/audit-data-observability.md) | 290 行 / 32.7 KB | 实库 37 表 vs 元数据 26 表、10 张孤儿表、迁移守卫与自动迁移风险、JSON 无界、可观测性只覆盖 HTTP 线程、备份/回滚缺位、前端契约硬字段 | §1.8、§3.6（规则 4/8）、§4.3、§7（R17–R19） |

### E.1 口径统一声明（评审时以此为准）

| 事项 | 本方案口径 | 易混淆口径 |
| --- | --- | --- |
| 分层违规 | **165 条 = 逐条 import 边**（层对 60/38/30/23/10/4） | 168 = 去重模块对（审计报告口径，含存疑项） |
| 环 | 1 个 SCC / 27 个模块，根因 `backend/app/db/base.py:7` | 不要表述成「26 个 model 模块各自违规」 |
| 执行副本 | 6 处直连：3 活 + 1 死（`services/tasks.py:228-234`）+ 2 旁路（SSE 预览、Telegram） | 「6 个活跃入口」不准确 |
| 准入调用点 | **6 处**（含 `tasks_stream.py:255` 的 `reserve(None)` 预览预演） | 5 处（漏了预览预演） |
| 测试基线 | 404 = `tests` 299 + `regression_tests` 105（排除 2 个陈旧微博测试） | 129 项 = 第二轮 Docker 发布门禁的子集 |
| 端点 | 133 个，其中仅 84 个声明 `response_model` | 「全部有 response_model」 |
| 锁 | 25 把 / 18 个文件（含 2 RLock、2 BoundedSemaphore） | 用窄正则会漏计成 19 把 / 13 文件 |
| 异常吞噬 | 473 = 250 处裸 `except Exception:` + 223 处 `except Exception as e` | 253 是旧统计口径 |

### E.2 三份审计共同确认的三条硬约束

1. **单进程假设是硬编码，不是偏好**：加 `--workers` 会重复迁移、重复调度、重复轮询、重复登录网盘、同一任务多进程并发（详见 R6 与 §8 边界段）。
2. **行为等价必须先于结构改造**：执行链路的 6 份副本已有参数级分叉（`share_check_always_notify`），S2 前必须先用真值表锁定行为。
3. **幂等键与「云盘侧事实源」必须先于自动重试**：`task_scheduler.py:266-273` 的注释已自认「重试时云盘可能已成功」，这是本项目最危险的一条捷径。
