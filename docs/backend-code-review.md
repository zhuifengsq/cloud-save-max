# 后端代码质量评审报告

> 评审对象：`backend/`（FastAPI + SQLAlchemy + SQLite）
> 评审方式：全量静态阅读 + AST 度量扫描 + pytest 实跑 + CI/迁移/依赖配置核对
> 评审日期：2026-11-16　评审性质：只读评审，未修改任何业务代码

---

## 一、结论摘要

### 1.1 总体判断

综合评分 **7 / 10**。这是一套**设计意图明确、边界意识良好**的后端，而不是"堆到能跑"的脚本：有分层门禁、有真实的端口抽象、有针对 SQLite 锁冲突的回归测试、有教科书级的 SSRF 防护。

扣分集中在三处工程债：**巨型函数**（56 个 ≥80 行，最差 374 行）、**手写 service locator**（56 个返回 ModuleType 的工厂函数架空了静态类型）、**灰度开关与旧实现长期并存**（同一逻辑两份实现）。此外存在 4 个应当尽快处理的安全/正确性缺陷。

一句话概括：**这套代码的 owner 懂工程，欠的是"把大块拆小、让工具替你守规矩"这最后一步。**

### 1.2 分项评分

| 维度 | 评分 | 主要依据 |
| --- | --- | --- |
| 架构设计 | 8 / 10 | 分层门禁 0 违规、无循环依赖、域端口层真实被 14 个模块引用、SSE 与 SSRF 实现专业 |
| 可维护性 | 5 / 10 | 56 个 ≥80 行函数、container 56 个工厂函数、116 条延迟 import 且预算未生效、双实现并存 |
| 安全性 | 6 / 10 | 无注入/无命令执行/无 `verify=False`；但网盘 cookie 明文入库、登录无失败限流 |
| 正确性 | 7 / 10 | 锁重试、事务边界、SSE 线程唤醒都正确；但时区处理混乱、准入控制依赖单进程前提 |
| 测试 | 7 / 10 | 857 个用例（含 54 个回归测试文件），覆盖锁冲突/安全边界；但 CI 仅在打 tag 时执行 |
| 工程化 | 4 / 10 | 无 ruff / mypy / pyproject.toml / pytest.ini，993 处 `Any` 注解 |

### 1.3 三个最该动的点

1. **引入 ruff + 渐进式 mypy**（半天成本，收益最大，是后续所有重构的安全网）。
2. **拆解 `preview_share` / `run_task`，把 container 换成 Protocol + 构造注入**（端口层已存在，路已经铺好一半）。
3. **删除 `_legacy_execute` 与 `legacy_notify`**（灰度开关已全 True 很久，留着只会让 bug 修一半）。

---

## 二、项目画像

### 2.1 规模

| 指标 | 数值 |
| --- | --- |
| Python 文件数 | 294 |
| 后端总行数 | 47,307 |
| HTTP 端点（`@router.*`） | 142 |
| Alembic 迁移版本 | 49 |
| 测试文件 | tests 28 + regression_tests 54 |
| pytest 收集用例 | 857 |

### 2.2 分层规模分布

| 目录 | 行数 | 占比 |
| --- | ---: | ---: |
| `app/infrastructure` | 22,397 | 47.4% |
| `app/application` | 17,507 | 37.0% |
| `app/api` | 4,166 | 8.8% |
| `app/schemas` | 1,172 | 2.5% |
| `app/core` | 1,167 | 2.5% |
| `app/domain` | 393 | 0.8% |
| `app/middlewares` | 171 | 0.4% |

域层只有 393 行（9 个端口共 334 行），基础设施层接近总行数的一半——**这是典型的"适配器驱动"形状**：业务复杂度被网盘差异吃掉，而不是被领域规则吃掉。这在多网盘同步类项目里是合理的，但代价是 `drive/adapters` 独占 14,321 行（约 30%），且是为移植代码。

### 2.3 最大的十个文件

| 行数 | 文件 | 主要职责 |
| ---: | --- | --- |
| 2,312 | `infrastructure/drive/adapters/cloud189_adapter.py` | 天翼云盘适配器（74 个方法） |
| 2,145 | `infrastructure/drive/adapters/baidu_adapter.py` | 百度网盘适配器（65 个方法） |
| 1,299 | `infrastructure/drive/adapters/guangya_adapter.py` | 光雅适配器 |
| 1,251 | `infrastructure/drive/adapters/aliyun_adapter.py` | 阿里云盘适配器 |
| 1,249 | `infrastructure/drive/adapters/quark_adapter.py` | 夸克适配器 |
| 1,215 | `application/tmdb_cache.py` | TMDB 元数据缓存 |
| 1,065 | `application/telegram_bot/actions.py` | TG 机器人动作分发 |
| 1,039 | `application/share_preview_batch.py` | 批量分享预览 |
| 857 | `infrastructure/execution/drama_executor.py` | 追剧执行引擎 |
| 847 | `application/notifications/legacy_notify.py` | 遗留通知实现 |

### 2.4 动态验证

```
pytest (backend)  →  857 collected / 822 passed / 2 skipped / 35 errors / 15.5s
```

35 个 error 全部为环境问题：仓库内残留目录 `backend/pytest-of-qiuqu` 导致的 `PermissionError: [WinError 5]`，与代码无关。但它暴露了一个真实缺陷——**测试依赖系统默认临时目录且没有 conftest 清理**（见 P2-7）。

分层门禁实际运行结果：

```
modules=294  internal_imports=925  deferred_imports=116  violations=0  import_cycles=0  exit 0
```

---

## 三、做得好的地方（建议保留，不要"顺手重构"掉）

### 3.1 SQLite 并发处理是真正理解过的

`infrastructure/persistence/session.py` 做了 WAL + `synchronous=NORMAL` + `busy_timeout=30000` + `temp_store=MEMORY` + `foreign_keys=ON`，连接池 `QueuePool(pool_size=5, max_overflow=10, pool_timeout=30, pool_recycle=3600)`。

更有价值的是它把"锁冲突"这件事的**语义**讲清楚了：`retry_on_locked` / `commit_with_locked_retry` / `transaction()` 的 docstring 明确写着——flush 阶段遇到锁冲突时事务已经回滚，此时重试 commit 只会提交一个空事务，结果是 **HTTP 200 + 数据静默丢失**。这个坑有专门的回归测试兜底（`regression_tests/test_unit_of_work.py::test_lock_conflict_is_not_masked`）。

能踩到并写清这个坑的项目，通常不是靠运气长到 4.7 万行的。

### 3.2 执行引擎的事务边界设计（全项目最值得保留的设计）

`application/use_cases/run_task.py` 采用三段式：**detached read plan → 外部引擎执行（不持事务、不做网络 IO 在事务内）→ 短写回**。`_release_read()` 在进入网络传输前显式结束读事务（`core/runtime/tx.py::end_read_tx`）。

文件顶部还留了一张**触发方真值表**注释，逐点列出 http / sse / scheduler 三种触发在 `share_check_always_notify`、`persist_execution`、`manual_run` 上的取值和调用点，并说明准入控制留在调用点而非 use case 内。这种"把设计决策写成可核对的表格"的做法，在个人项目里非常少见。

### 3.3 错误模型统一

`core/errors.py` 用 `@dataclass ApiError(code, message, http_status=400, detail)` + `bad_request` / `unauthorized` / `forbidden` / `not_found` 构造器；`core/exception_handlers.py` 三个 handler 把 `ApiError`、`RequestValidationError`、兜底 `Exception` 统一收敛为 `{code, message, detail}`。前端只需认一种错误形状，`code` 还能作为稳定的机器可读标识。

### 3.4 SSRF 防护达到安全团队水准

`application/proxy_image_cache.py` 的防护链：

1. `validate_image_url`：只允许 `douban.com` / `doubanio.com` 及其子域；禁 userinfo、非标准端口、fragment、控制字符。
2. `_resolve_image_ip`：用 `ipaddress` 校验 `is_global` / `is_multicast` / `is_reserved` / teredo / 6to4 / ipv4_mapped。
3. `_download_image`：**把连接钉在已校验的数字 IP 上，同时保留 Host 头与 TLS SNI**（彻底防 DNS rebinding），`redirect=False`、15s 总超时、Content-Type 白名单、`Content-Encoding` 必须为 identity、`max_bytes` 上限。

有配套的 `regression_tests/test_proxy_image_security.py`。大多数项目的图片代理就一句 `requests.get(url)`，这里的差距是量级上的。

### 3.5 SSE 实现专业

`api/routes/tasks_stream.py`：`_SseQueue` 用 `deque(maxlen=256)` 做背压、`threading.Lock` + `asyncio.Event` + `call_soon_threadsafe` 完成跨线程唤醒、`asyncio.timeout(15)` 周期性发 `: ping` 心跳、客户端断开后置 `stopped` 丢弃后续事件。任务本体跑在独立线程，不占用 anyio 线程池配额。

### 3.6 调度器参数配置正确

`infrastructure/scheduling/task_scheduler.py` 中四个 job 全部带 `max_instances=1, coalesce=True, misfire_grace_time=60`，避免了"慢任务堆积"和"停机后补跑风暴"这两个 APScheduler 经典陷阱。

### 3.7 安全卫生整体干净

全仓扫描结果：

| 检查项 | 结果 |
| --- | --- |
| `shell=True` | 0 处 |
| `eval` / `exec` / `os.system` | 0 处 |
| `verify=False`（关闭 TLS 校验） | 0 处 |
| `pickle` / 不安全 `yaml.load` | 0 处 |
| 字符串拼接 SQL | 0 处 |

`/api/metrics` 的 token 比对使用 `hmac.compare_digest`（防时序攻击），未配置 token 时降级为"登录 + `audit:read` 权限"双路鉴权。

### 3.8 认证与密钥管理

- `application/auth.py`：refresh token 轮换实现正确（每次刷新置 `revoked_at` 并签发新 token，sha256 + pepper 存哈希，httponly cookie）。
- `application/auth_context.py`：权限缓存带 30s TTL、容量上限 1024、支持主动失效。
- `core/settings.py`：`secrets.json` 首次运行自动生成 `jwt_secret_key` / `refresh_token_pepper`，`chmod 0600` + 临时文件原子替换。

---

## 四、问题清单

> 编号规则：P0 = 安全/正确性，建议尽快；P1 = 可维护性，长期成本最高；P2 = 清理项，随时可做。

### P0-1　网盘 cookie 明文入库

- **位置**：`app/infrastructure/persistence/models/drive_account.py`
- **现象**：`cookie: Mapped[str] = mapped_column(Text, nullable=False)`、`config_json` 同为明文 Text 列，无字段级加密。
- **影响**：`data/app.db` 一旦泄露（备份文件、误提交、共享给他人排障），全部网盘账号同步失守，且用户往往无感知。项目对 `secrets.json` 做了 0600 保护，却把真正的高危凭据裸放在库里，保护强度与风险等级倒挂。
- **建议**：引入一层 `SecretBox`（基于 `cryptography.fernet`，密钥放进 `secrets.json`），对 `cookie` / `config_json` 做写入加密、读取解密；或至少把 `cookie` 单独存为文件并 0600。迁移期可加一个 `enc_version` 列，双读兼容。

### P0-2　时区处理混乱，靠部署环境掩盖

- **位置**：`app/core/security.py:26`、`app/application/auth.py:17`，以及全项目 44 处 `datetime.now()`。
- **现象**：
  - `create_access_token` 用 `datetime.now()`（本地时间）而非 UTC。
  - `auth.py` 里 `_utcnow()` 名字是 UTC，实际返回 `datetime.now()`；还配了 `_normalize_dt` 把 aware 时间转成 naive 本地时间。
  - 统计：`datetime.now()` 44 处、`utcnow` 13 处、`timezone.utc` 仅 7 处。
  - 数据库列声明 `DateTime(timezone=True)`，但 SQLite 并不保存时区。
- **影响**：目前靠 Dockerfile 的 `TZ=Asia/Shanghai` 掩盖。一旦迁移到 UTC 容器、或遇到夏令时地区、或宿主时区变更，令牌过期判断与任务时间窗会整体偏移，属于"上线三个月后突然出现"的隐蔽 bug。
- **建议**：全局统一 `datetime.now(timezone.utc)` 并在边界层做展示转换；`_utcnow` 要么改名为 `_now_local`，要么改实现。先在 `core/` 建一个 `clock.py`（端口层已有 `domain/ports/clock.py`，直接复用），禁止业务代码直接调用 `datetime.now()`，用一条 ruff 自定义规则或 `flake8-datetimez` 拦住新增用法。

### P0-3　任务准入控制只在本进程内有效

- **位置**：`app/infrastructure/scheduling/task_admission.py:64`
- **现象**：`task_admission = TaskAdmission(max_active=4)` 是纯内存实现（`Lock` + `_active` 计数 + `_task_ids` 集合）。
- **影响**：`max_active=4` 这一限制**仅在单 uvicorn 进程部署下成立**。一旦加上 `--workers`、或横向扩容多容器，同一任务可被并发执行两份，配额限制、幂等约束全部失效；重启后准入状态也直接丢失。当前 Dockerfile 确实是单进程（`python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`，无 `--workers`），但**这个前提没有任何注释声明**，属于迟早被踩的隐形地雷。
- **建议**：短期内至少在模块顶部写清"本实现依赖单进程部署，禁止加 --workers"；中期把准入状态落到数据库（`task_admission` 表 + 唯一约束）或复用已有的 `locks` 端口做跨进程互斥。

### P0-4　登录接口无失败限流

- **位置**：`app/api/routes/auth.py` 的 `login` 直接调用 `authenticate_user`。
- **影响**：唯一的限流器 `core/runtime/rate_limiter.py` 是给网盘 API 用的，登录口可被无限次爆破。JWT + bcrypt 虽不至于一击即破，但缺少失败计数与账号锁定是明显的攻击面。
- **建议**：加"同一 IP + 用户名维度"的失败计数（内存 + 退避即可，先解决有无问题），连续 N 次失败后锁定 M 分钟并记审计日志。

### P1-1　56 个函数 ≥80 行，最差 374 行

- **统计**：`backend/app` 中函数体 ≥80 行的共 **56 个**。

| 行数 | 位置 | 函数 |
| ---: | --- | --- |
| 374 | `application/share_preview.py:33` | `preview_share` |
| 254 | `infrastructure/execution/task_executor.py:105` | `run_task` |
| 252 | `application/share_preview_batch.py:541` | `preview_share_batch` |
| 238 | `application/use_cases/task_lead_in.py:266` | `_preview` |
| 205 | `application/lead_in/settings.py:123` | `update_drama_lead_in_settings` |
| 188 | `application/share_preview_batch.py:351` | `fetch_share_file_list_grouped` |
| 174 | `infrastructure/drive/adapters/cloud189_adapter.py:2086` | `save_file` |
| 172 | `application/resource_search.py:244` | `fetch_task_suggestions_result` |
| 171 | `infrastructure/drive/adapters/complete_listing.py:152` | `_enumerate` |
| 166 | `application/drama_share_autoupdate.py:249` | `resolve_drama_shareurl_update` |
| 162 | `application/share_preview_batch.py:795` | `validate_share_links_streaming` |
| 158 | `application/dashboard_drama.py:109` | `build_drama_overview` |
| 150 | `application/use_cases/task_lead_in.py:505` | `_confirm` |
| 142 | `application/telegram_bot/editor.py:323` | `dispatch_browser` |
| 139 | `infrastructure/drive/adapters/cloud115_adapter.py:447` | `save_file` |

- **典型案例**：`preview_share`（374 行）在同一函数体内完成了：网盘类型探测 → 账号选择 → adapter 初始化 → `get_stoken` / `get_detail` → TMDB 配置读取 → 魔术正则编译 → 14 个视频扩展名白名单判断 → 与目标目录做文件名 diff → 写数据库。函数内部还嵌了 `_is_video_name` / `_to_ts` 两个局部函数，以及多处 `except Exception: ok = None` 的静默降级——**任何一处失败都不会产生明确的错误码，只会表现为"预览结果少了几个文件"**。
- **影响**：这类函数无法单测（要构造完整的网盘账号上下文）、无法复用、改动风险与长度成正比。项目里出现"同一个 bug 需要在两个地方修"，根源多半在这里。
- **建议**：按"编排 / 数据获取 / 纯计算 / 持久化"四段拆：`preview_share` 保留 20~30 行编排，把 adapter 交互抽成 `_resolve_share_context()`、把文件过滤与 diff 抽成纯函数（可单测，输入 `raw_items + pattern + video_exts`，输出 `preview_list`）。静默降级要改成收集 warning 列表一并在响应里返回。

### P1-2　手写 container 架空了静态类型

- **位置**：`app/application/container.py`（397 行，**56 个返回 `ModuleType` 的工厂函数**）。
- **现象**：`tasks_repo` / `users_repo` / `adapter_factory` / `task_executor_cls` 等全部在函数体内 `import`，以规避分层检查。
- **影响**：
  - 所有仓储/执行器调用丢失类型信息与 IDE 跳转，方法名或参数写错只在运行时炸。
  - 直接证据：分层门禁报 `deferred_imports=116`，而 `.layering-baseline.json` 中记录的预算 90 **从未被校验**（`scripts/check_layering.py:315-319` 的 `--baseline` 只比较 `violations` 字段），实际已增长 29% 而无人感知。
  - 灰盒测试、覆盖率工具、依赖分析全部失效。
- **建议**：端口层已经存在（`domain/ports/` 9 个文件 334 行，被 14 个模块引用，`run_task.py` 已在用 `TaskRunStorePort` / `TaskExecutionEnginePort`）——**说明这条路走得通，只是没走完**。把 container 里高频的几个（repo、executor、notifier）换成 Protocol + 构造注入，并在 `check_layering.py` 中把 `deferred_imports` 纳入 baseline 比较，让预算真正生效。

### P1-3　灰度开关与旧实现长期并存

- **位置**：`app/core/settings.py`、`app/application/use_cases/run_task.py`、`app/application/notifications/legacy_notify.py`。
- **现象**：`use_unified_run_use_case` / `use_notify_queue` / `use_keyed_locks` / `use_account_snapshot_cache` 默认全部为 `True`，但：
  - `run_task.py` 仍保留约 210 行的 `_legacy_execute`，逐字复刻旧的 6 个调用点；
  - `legacy_notify.py`（847 行）**仍然是通知的实际实现**，`infrastructure/execution/task_run_notifier.py:12` 的分支是：
    ```python
    fn = legacy_notify.send_async if settings.use_notify_queue else legacy_notify.send
    ```
- **影响**：修复一个通知 bug 必须改两处，且没人敢删"另一份"；新人也无法判断哪份是权威实现。随时间推移，两份会持续漂移。
- **建议**：如果灰度已稳定（超过一个发布周期），直接删除 legacy 分支与开关字段——**这比保留它更安全**。如需保守，可先在 CI 加一条"legacy 分支必须被至少一个测试覆盖"的约束，再逐步摘除。

### P1-4　零 lint / 零类型门禁

- **现象**：仓库中不存在 `pyproject.toml`、`pytest.ini`、`ruff.toml`、`mypy.ini` 中任何一个；`Any` 注解 993 处，`type: ignore` 13 处。
- **CI 现状**：
  - `layering-gate.yml`：每个 PR 跑分层检查（有效）；
  - `db-schema-gate.yml`：每个 PR 空库 `alembic upgrade head` + schema drift + 迁移预检（很扎实）；
  - `docker-publish.yml`：**仅在打 tag 时**执行 `pytest tests regression_tests`。
- **影响**：主分支日常合并没有任何测试与静态检查把关；4.7 万行规模下，纯靠人眼守分层、守类型、守死代码是不可能的。这是当前最大的工程风险。
- **建议**：
  1. 建 `pyproject.toml`，接入 `ruff`（先只开 `E,F,W,I`，`--fix` 自动修 import 顺序）；
  2. `mypy` 用 `--ignore-missing-imports` 起步，先在 `app/domain`、`app/core` 两个小目录开到 strict，再逐步扩大；
  3. CI 增加 `test.yml`：PR 跑 `pytest tests regression_tests -q` + `ruff check`。

### P1-5　适配器层是移植代码的标本

- **位置**：`app/infrastructure/drive/adapters/`（14,321 行，约占总代码 30%）。
- **现象**：
  - `quark_adapter.py:12` 有 `sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))` 这类路径 hack；
  - `import requests` 出现在方法体内 22 次（`re` / `random` / `datetime` 也常写在函数里）；
  - 中文注释与英文 docstring 混杂。
- **正面**：HTTP 调用普遍带 timeout（如 `guangya_adapter.py` 的 `requests.post` 均为 `timeout=20`），未发现无超时的裸调用——这一点比很多同类项目好。
- **建议**：不要求重写。可先做两件低风险的事：(1) 抽一个共享的 `http_session`（统一 timeout / 重试 / UA / 日志），让 22 处方法内 import 消失；(2) 把五个适配器共有的"分享 → stoken → 列目录 → 翻页"流程抽成模板方法，各适配器只实现差异点。

### P2　清理项清单

| 编号 | 问题 | 位置 | 建议 |
| --- | --- | --- | --- |
| P2-1 | `/health` 每次请求 fork 两次 `git` 子进程（`timeout=5`，无缓存无认证），且 `Path(__file__).resolve().parents[4]` 依赖目录深度 | `api/routes/health.py:16`、`:33` | 启动时取一次并缓存在模块级变量，或读环境变量 `GIT_SHA` |
| P2-2 | 灰度开关字段整块重复定义两遍（`use_unified_run_use_case` / `use_notify_queue` / `use_keyed_locks` 各出现 2 次） | `core/settings.py` | 删除重复块（与 P1-3 一并处理） |
| P2-3 | 模块 `import` 写在 docstring 之前，导致整个文件的 `__doc__`（"拆分说明"整段）变成无意义字符串表达式 | `api/routes/tasks.py` 顶部 | 调整顺序（`tasks_stream.py` 中位置是正确的，可对照） |
| P2-4 | `/auth/me` 重复装载权限（`get_current_user` 已装载，又调一次 `load_user_roles_permissions`），仅靠 30s 缓存兜住 | `api/routes/auth.py` | 删除第二次调用 |
| P2-5 | 首次初始化管理员存在 TOCTOU 竞态：并发请求都能通过 `is_initialized(db)` 检查 | `api/routes/setup.py:31-40` | 加行级锁或依赖 username 唯一约束并捕获 IntegrityError 返回明确错误码 |
| P2-6 | JWT 写入 `"perms": permissions` 但 `authenticate()` 从不读取该 claim，每次请求仍从缓存/DB 重载 | `core/security.py:29` | 要么删掉该 claim，要么真的从 token 取（推荐删，避免权限变更不生效） |
| P2-7 | 测试依赖系统默认临时目录，仓库残留 `backend/pytest-of-qiuqu` 导致 35 个用例报 `PermissionError` | 仓库根 / `pytest` 配置 | `conftest.py` 设置 `--basetemp`，并清理残留目录 |
| P2-8 | `schema_drift_allowlist.txt` 中 10 张历史表标注"临时豁免"但无清理期限 | `scripts/schema_drift_allowlist.txt` | 每项加注释说明表用途与计划，或在 CI 中加"豁免数量不得增加"的断言 |
| P2-9 | `.layering-baseline.json` 的 `deferred_imports` 预算未生效（实际 116 vs 预算 90） | `scripts/check_layering.py:315-319` | 把 `deferred_imports` 纳入 baseline 比较 |
| P2-10 | git 历史无有效信息：最近 12 个 commit message 全部为"自用" | `git log` | 建议采用 `type(scope): subject` 约定，至少让 `fix` 与 `feat` 可区分 |

---

## 五、改进路线图

### 阶段一：止血（1～2 天）

| 任务 | 验收标准 |
| --- | --- |
| 网盘 cookie / config_json 加密（P0-1） | 新写入的 `drive_account` 记录中 `cookie` 为密文；老数据可读 |
| 登录失败限流（P0-4） | 连续 5 次失败后返回 429/锁定提示，并写审计日志 |
| `task_admission` 补充单进程约束注释（P0-3 短期） | 模块 docstring 明确写出前提与失效后果 |
| 清理 P2 全部 10 项 | 无重复配置字段、`__doc__` 生效、`/health` 不再 fork |

### 阶段二：立规矩（3～5 天）

| 任务 | 验收标准 |
| --- | --- |
| 建立 `pyproject.toml` + ruff + 渐进 mypy（P1-4） | CI 中 `ruff check` 通过；`app/domain`、`app/core` 达到 strict |
| CI 增加 PR 级 pytest（P1-4） | 每个 PR 都能看到测试结果，不再只在 tag 时跑 |
| `check_layering.py` 支持 `deferred_imports` 预算（P2-9） | 该数字上涨时 CI 失败 |
| 测试 basetemp 隔离（P2-7） | `pytest` 在任意机器上 0 error |

### 阶段三：还技术债（1～2 周，可与功能并行）

| 任务 | 验收标准 |
| --- | --- |
| 拆分 `preview_share`（P1-1） | 单函数 ≤60 行；文件过滤与 diff 逻辑成为纯函数并有单测 |
| 拆分 `run_task` / `preview_share_batch` / `_preview` | 同样 ≤60 行，编排层只做调用与错误映射 |
| 删除 `_legacy_execute` 与 `legacy_notify`（P1-3） | 通知与执行各只剩一条实现路径，测试全绿 |
| container 逐步替换为 Protocol + 构造注入（P1-2） | 新增代码不允许使用 container；`deferred_imports` 单向下降 |

---

## 六、附录

### 附录 A：评审方法

1. **结构扫描**：统计文件数、行数、分层占比、最大文件与函数长度分布（AST 解析，非正则）。
2. **动态验证**：`pytest` 全量执行；`scripts/check_layering.py` 全量执行。
3. **安全扫描**：全仓检索 `shell=True`、`eval`/`exec`/`os.system`、`verify=False`、`pickle`、字符串拼接 SQL、`datetime` 用法分布。
4. **配置核对**：CI workflow、Alembic 迁移链、`schema_drift_allowlist`、依赖钉版本情况。
5. **关键路径精读**：认证与令牌轮换、任务执行引擎、SSE 流、图片代理（SSRF）、SQLite 会话与锁重试、调度器。

### 附录 B：本次扫描的统计口径

| 指标 | 口径 |
| --- | --- |
| 文件数 / 行数 | `backend/app/**/*.py`，排除 `__pycache__` |
| 长函数 | AST 中 `FunctionDef`/`AsyncFunctionDef` 的 `end_lineno - lineno + 1 ≥ 80` |
| `Any` 注解 | 匹配 `: Any` / `-> Any` / `[Any]` / `Any]` |
| 端点数量 | 匹配 `@router.get` / `.post` / `.put` / `.delete` / `.patch` 装饰器 |
| 时区统计 | 匹配 `datetime.now()`、`utcnow`、`timezone.utc` |

### 附录 C：已知未覆盖的部分

以下内容本次未深入阅读，评审结论未包含其判断：

- `application/queries/task_views.py`（据注释为 O(1) 查询设计）；
- `infrastructure/scheduling/task_scheduler.py` 中 `run_drama_tasks` 主循环的分支细节；
- `application/telegram_bot/actions.py`（1,065 行）的交互状态机；
- `application/tmdb_cache.py`（1,215 行）的并发控制细节；
- 五个网盘适配器的协议实现正确性（仅抽查了 `save_file` 与错误处理模式）。

---

## 七、一句话总结

> 这套后端在"设计"这一层是称职甚至优秀的——它知道事务边界在哪、知道 SSRF 要怎么防、知道 SSE 要如何跨线程唤醒；但在"组织"这一层欠账明显——用 374 行的函数承载核心流程，用手写容器换掉类型系统，用两套实现换来一个早已稳定的开关。补齐组织层的成本远低于补齐设计层，这正是现在动手最划算的原因。
