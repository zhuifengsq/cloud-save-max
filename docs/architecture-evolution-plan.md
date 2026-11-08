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

### 10.1 已落地（S0 + S1 + S2 + S3 地基）

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

### 10.2 量化结果（2026-11-08 实测，可复现）

| 指标 | 改造前基线 | 当前 | 命令 |
| --- | --- | --- | --- |
| 测试总数 | 404 passed / 2 收集失败 | **438 passed, 2 skipped**（+34 项新护栏） | `python -m pytest tests regression_tests -q` |
| 分层违规 | 165 条（60/38/30/23/10/4） | **117 条（38/27/23/15/10/4）**，净减 48 | `python ../scripts/check_layering.py --fail-on 0 --baseline .layering-baseline.json` |
| 模块 / import 边 | 188 / 610 | 227 / 659（deferred 33→58，均为仓储访问的延迟 import） | 同上 |
| 路由层裸 `db.commit()` | 89 处 / 18 文件 | **0 处 / 0 文件**（13 个路由文件全部归零，89 处全收口） | `grep -rc "db\.commit()" backend/app/api/routes` |
| 全 app 提交点 | 146 处（路由 45 / 服务 80 / 扩展 16 / main 2 / scripts 2 / db 1） | **101 处**（服务 80 / 扩展 16 / main 2 / scripts 2 / db 1） | 递归统计 `db\.commit()`；`commit(db)`/`transaction(db)` 不在其中 |
| 锁冲突下的写入语义 | **静默丢数据**（flush 已回滚，重试提交空事务并成功） | 抛原始锁错误（5xx），行不落库 | `pytest regression_tests/test_unit_of_work.py -k lock_conflict -q` |
| TMDB 批量刷新读事务 | 最多 200 次外部请求共用一个读快照（checkpoint 无法推进） | 每项外部请求前后结束调用方读事务（`close_read_tx=True`）；执行器路径默认不变 | `pytest regression_tests/test_tmdb_refresh_read_txn.py -q` |
| 环 | 1 个 SCC / 27 模块 | **0 个 SCC**（`app/db/base.py` 末尾的 `import app.models` 已删，Alembic 显式导入） | 同上 |
| 应用启动 | — | `import app.main` OK | `python -c "import app.main"` |
| application→persistence 违规 | 60 条 | **15 条**（S3 验收阈值 ≤20） | `.layering-baseline.json` 层对明细 |
| 执行入口副本 | 6 处直连（含 1 处死代码） | **1 个用例**（`grep -rn "TaskExecutor(" backend/app` 只剩用例与执行器自身） | 见附录 B |
| 锁表增长 | 素材名维度无界 dict | `LRUKeyedLocks` 上限 1024/256 | 单测断言上限与淘汰 |

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

### 10.5 尚未完成（S3 接线 / S4 / S5，按优先级）

1. **服务层与扩展层提交归属上移**（S3 主体剩余）：路由层已归零（§10.1），剩余 101 处集中在 `app/services` 80 处（`telegram_bot/handlers.py` 50、`telegram_bot/actions.py` 20、`drama_lead_in.py` 10）与 `app/extensions` 16 处（`task_scheduler.py` 14、`task_executor.py` 1、`telegram_bot_manager.py` 1），另有 `main.py` 2、`scripts` 2、`db/session.py` 1（自身实现）。原则：先写行为对照表（提交时机、失败回滚、响应体、是否在事务内做 IO），再把「一次业务动作一次提交」上移为调用方的事务边界；调度/后台任务路径保留自身事务（无请求作用域）。
2. **端口接入**（S3 尾巴/S4）：仓储已落地（22 个仓储模块、`container` 唯一装配点），但 `app/domain/ports/` 的 8 个 Protocol **仍是 0 引用**——目前是「服务直接调 container 拿仓储」，还没做到「面向 Protocol 编程」。下一步先让 `TaskRunUseCase` 依赖 `TaskRepositoryPort`/`ClockPort` 的桩实现，再让 `adapter_factory` 走 `DriveGatewayPort`；做完后 `services` 才真正不依赖 SQLAlchemy 的 `Session` 类型。
3. **物理搬目录 + shim**（S4）：按附录 A 迁移 `services/*` 到 `application/`、`extensions/adapters` 到 `infrastructure/`，旧路径保留 re-export 一个版本。
4. **迁移与 schema 治理进 CI**（S3 尾巴）：`check_schema_drift` 加 `--ignore` 白名单进 CI；`migration_preflight` 作为部署前检查；10 张遗留表出「删除/归档」决策单。
5. **前端契约护栏**（S5）：把 `executions` 固定 3 条、SSE `data:` + `[DONE]`、错误体 `{code,message,detail}`、23 字段 SharePreview 固化成 golden 测试。
6. **基线维护**：`deferred_imports` 现为 **58**（基线 33）——这是「container + 函数内延迟 import」方案的**预期代价**，不是退化，本轮已用 `--update-baseline` 刷新（同时把 `violations` 预算从 165 下调到 **117**、`cycles` 从 1 改 0，做到「只降不升」）；`modules` 188→227、`internal_imports` 610→659 属新增代码正常增长。
7. **风险 R13–R19 的落实**：幂等键、WAL 膨胀监控、通知送达率、调度漏跑、schema 双真相、自动迁移不可逆。「护栏入库」已按用户「精确放行」处理：`.gitignore` 移除 `backend/tests`、`docs` 整行忽略，改为只忽略 `docs/*.{html,png,jpg,jpeg,gif,zip}`（预览垃圾）；并按用户要求把 12 篇历史文档（`docs/backend-optimization-*.md`、`docs/superpowers/`、`docs/WPS*.md`、`docs/追剧任务引流文件*.md`）加回忽略。当前待推送 61 个未跟踪文件 + 25 个已跟踪修改；其中必须推送的是 17 个新架构代码、6 个新 regression test、4 个 scripts、workflow、baseline、26 个 `backend/tests`、5 篇架构文档。

### 10.6 对应「第几周」

- 原计划：S0 今天 → S1 本周 → S2 下周 → S3 第 3–4 周 → S4/S5 第 5–9 周。
- 当前：**S0 + S1 + S2 全部完成，S3 主体完成**（§10.9 验收表：application→persistence 60→15、cycles 1→0、路由 commit 89→0、回归全绿、事务边界单测），相当于**第 4 周末**的位置；剩余 S3 尾巴（服务层 80 处 + 扩展层 16 处提交归属上移、端口接入、schema 治理进 CI）与 S4 物理搬迁未开始。

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
2. **验收三元组**：全量测试（当前 438 passed, 2 skipped）+ 分层门禁（162 条 ≤ 基线 165）+ `import app.main`；写路径另加「行数确实变化」的行为验证（见 §10.7）。
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
| 回归测试 | 全绿 | **438 passed, 2 skipped** | 达标 |
| 事务边界单测 | 异常后无残留 | `test_unit_of_work.py`（6 项，含真实 SQLite 争锁）+ `test_route_transactions.py`（3 项） | 达标 |
| 分层门禁 | 只降不升 | violations 165→**117**，exit 0 | 达标 |

**已知例外（计入预算，不隐瞒）**：`tmdb_cache` 3 条边（写路径是 Core + 专用 writer engine，见 §10.3 决策 5）、`drama_lead_in` 4 条边（与 `task_lead_in`/`system_setting` 强耦合，待 S4 与追剧模块一起搬）、`share_preview_batch` 2 条边（`db.session` 与 `DriveAccount`，属 S4 链路重构范围）。

**为什么这一步值得**：业务层不再 import 模型，模型字段改名/拆分时编译器（门禁）能立刻指出受影响的用例；`select()` 集中后，慢查询、缺索引、`noload` 策略有了唯一修改点；后续加缓存/读写分离只需改仓储，不必逐个业务文件改。
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
