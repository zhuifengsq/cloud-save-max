# 后端代码质量评审报告

> 评审对象：`backend/`（FastAPI + SQLAlchemy + SQLite）
> 评审方式：静态阅读 + AST 度量扫描 + pytest 实跑 + CI/迁移/依赖配置核对
> 评审日期：2026-11-16
> 版本：**v3**（对齐第二批 A–H 工作包；v2→v3 的改动见附录 D–2）

---

## 一、结论摘要

### 1.1 总体判断

综合评分 **7.4 / 10**（= 1.2 节分项按权重加权，计算过程见该节末；v1 回算 6.40，v2 为 6.45）。这是一套**设计意图明确、边界意识良好**的后端：有分层门禁、有真实的端口抽象、有针对 SQLite 锁冲突的回归测试、有接近安全团队实践的图片代理防护，还有一份写得很扎实的单进程 ADR。

v3 复核的是第二批 A–H 工作包。三处 P0/P1 已真实修复：登录失败限流、初始化竞态，以及生产无调用点的兼容层（run() / _legacy_execute）连同重复的开关字段一起删除。preview_share 从 374 行拆成 27 行编排 + 266 行纯计算；通知的 23 个渠道整体搬迁到 channel_delivery.py，代码里已经没有 legacy_notify。

扣分仍集中在两处工程债：**巨型函数**（仍 56 个 ≥80 行，最差 254 行——拆分把一个 374 行换成「27 行 + 232 行纯函数」，数量没降）、**手写装配层**（container.py 406 行、58 个函数，22 个返回 ModuleType、33 个无返回注解）。安全上唯一没动的 P0 是网盘凭据明文入库：方案文档已写好，代码零实施。

### 1.2 分项评分

| 维度 | 权重 | 评分 | 主要依据 |
| --- | --- | --- | --- |
| 架构设计 | 25% | 8.5 / 10 | 分层门禁升级为逐条语句指纹基线、0 违规、无循环依赖；分享预览按「端口 + 只读快照 + 纯计算」拆分；ADR 0003 把单进程约束写死 |
| 可维护性 | 25% | 6.5 / 10 | ≥80 行函数仍 56 个，但最差从 374 降到 254；兼容层、重复开关、factories.py 已删；装配层仍是 58 个函数 |
| 安全性 | 20% | 7 / 10 | 登录限流 + 代理头收紧 + uvicorn 只听回环已落地；网盘凭据仍明文入库（唯一 P0） |
| 正确性 | 15% | 7.5 / 10 | 初始化改为 SQLite 哨兵 + 单事务；锁重试、事务边界、SSE 线程唤醒正确；时区口径未统一（P1-2，已有盘点与方案） |
| 测试 | 10% | 8 / 10 | 1,014 passed / 2 skipped；新增初始化并发、登录限流、通知边界、分层预算、五类触发方行为基线等回归 |
| 工程化 | 5% | 7 / 10 | pyproject + ruff + mypy（端口层）进 CI，基线可挡回归；但 ruff 只开 3 条规则、mypy 只覆盖 11 个文件、无格式化 |

**加权计算**：8.5×0.25 + 6.5×0.25 + 7×0.20 + 7.5×0.15 + 8×0.10 + 7×0.05 = 2.125 + 1.625 + 1.40 + 1.125 + 0.80 + 0.35 = **7.425 → 7.4**。

权重按「维护 4.7 万行代码的长期成本」分配：可维护性与架构各占 25%，安全性 20%，正确性 15%，测试与工程化合计 15%。同一套权重回算：v1 为 **6.40**（工程化按 4 分），v2 为 **6.45**（工程化 5 分），v3 为 **7.425**。v1 正文写的「7 / 10」是未经计算的印象分——这也是 v1→v2 只涨 0.05、观感却差一档的原因。

### 1.3 建议的处理顺序

1. **凭据加密单独立项**（唯一未动的 P0）：先定密钥存放、轮换、备份与恢复口径，再动手加密。库和密钥同机时加密只防「拷走单个 db 文件」，这一点要写进设计而不是含糊带过。
2. **把门禁变宽**：ruff 现在只开 F821/F822/F823，mypy 只覆盖 app/domain/ports 的 11 个文件——对 4.7 万行几乎没有约束力。先扩规则（E,F,W,I），再按目录扩 mypy。
3. **继续拆 254 行的 run_task 与 252 行的 preview_share_batch**，直接复用 E 包验证过的手法（端口 + 只读快照 + 纯计算）。验收口径是「≥80 行函数数量不增加」，不是行数硬指标。
4. **时区按 G 包方案分期实施**（先认证、后执行历史），不要全局替换成 UTC——旧数据没同步转换会引入整体偏移。

## 二、项目画像

### 2.1 规模

| 指标 | 数值 |
| --- | --- |
| Python 文件数 | 300 |
| 后端总行数 | 47,473 |
| HTTP 端点（`@router.*`） | 142 |
| Alembic 迁移版本 | 48 |
| 测试文件 | tests 28 + regression_tests 62 |
| pytest 收集用例 | 1,014 |
| 分层违规 / 循环依赖 | 0 / 0 |

### 2.2 分层规模分布

| 目录 | 行数 | 占比 |
| --- | ---: | ---: |
| `app/infrastructure` | 22,547 | 47.5% |
| `app/application` | 17,509 | 36.9% |
| `app/api` | 4,147 | 8.7% |
| `app/schemas` | 1,179 | 2.5% |
| `app/core` | 1,164 | 2.5% |
| `app/domain` | 422 | 0.9% |
| `app/scripts` | 211 | 0.4% |
| `app/middlewares` | 171 | 0.4% |

域层只有 422 行（11 个端口共 363 行），基础设施层接近总行数的一半——这是**适配器驱动**的形状：业务复杂度被网盘差异吃掉，而非被领域规则吃掉。对多网盘同步类项目这是合理取舍，代价是 `drive/adapters` 独占 14,331 行（16 个文件，约 30%），且为移植代码。E 包新增的 `domain/ports/share_preview.py` 让端口层第一次真正被「业务边界」而非「数据访问」驱动。

### 2.3 最大的十个文件

| 行数 | 文件 | 主要职责 |
| ---: | --- | --- |
| 2,312 | `infrastructure/drive/adapters/cloud189_adapter.py` | 天翼云盘适配器（74 个方法） |
| 2,145 | `infrastructure/drive/adapters/baidu_adapter.py` | 百度网盘适配器（65 个方法） |
| 1,267 | `infrastructure/drive/adapters/guangya_adapter.py` | 光雅适配器 |
| 1,251 | `infrastructure/drive/adapters/aliyun_adapter.py` | 阿里云盘适配器 |
| 1,249 | `infrastructure/drive/adapters/quark_adapter.py` | 夸克适配器 |
| 1,215 | `application/tmdb_cache.py` | TMDB 元数据缓存 |
| 1,065 | `application/telegram_bot/actions.py` | TG 机器人动作分发 |
| 1,039 | `application/share_preview_batch.py` | 批量分享预览 |
| 1,016 | `infrastructure/drive/adapters/pan123_adapter.py` | 123 云盘适配器 |
| 990 | `infrastructure/drive/adapters/xunlei_adapter.py` | 迅雷云盘适配器 |

上一版榜单里的 `notifications/legacy_notify.py`（847 行）已随 F2 包更名为 `notifications/channel_delivery.py`（799 行），掉出前十。

### 2.4 动态验证

```
pytest (backend)      →  1,014 collected / 1,014 passed / 2 skipped / 55.1s
ruff 0.14.4           →  app: All checks passed
mypy 1.18.2 --strict  →  app/domain/ports: Success, no issues in 11 source files
check_layering.py     →  modules=300  internal_imports=934  deferred_imports=110
                         violations=0  import_cycles=0  exit 0
```

pytest 环境：`XXM_TESTING=1 DATABASE_URL=sqlite:// RUN_MIGRATIONS=0 SCHEDULER_ENABLED=0`，`--basetemp=.tmp/pt2`。本机 `backend/pytest-of-qiuqu` 残留目录仍在，指定 `--basetemp` 后不再出现 PermissionError；CI 用干净环境，不受影响。

分层门禁已改为**逐条语句指纹基线**（`approved_deferred_edges` / `approved_deferred_imports` / `approved_relative_imports`）：未经批准的延迟导入或新增相对导入都会失败，违规与循环依赖永远不能被基线「批准」，`--baseline` 缺失或损坏时 exit 2。deferred 从上一版的 116 降到 110，说明 F1 删掉兼容层后延迟导入确实少了一些。

---

## 三、做得好的地方（建议保留，不要「顺手重构」掉）

### 3.1 单进程约束有正式 ADR（v1 漏判，v2 补充）

`docs/architecture/adr/0003-single-process-deployment-assumption.md` 把"只支持 1 个 uvicorn 进程 + 1 个副本"写成硬约束，并列出 **12 项进程内状态**及其被破坏后的后果（权限缓存、适配器实例缓存、账号快照、令牌 single-flight、任务准入、按 key 锁、通知队列、TMDB 写槽、APScheduler、TG 轮询、扫码会话、启动期迁移备份）。还给出了将来扩容的"单写者模式"路径与重新评估的触发条件。

这是全项目文档化程度最高的一份材料，也直接说明 `task_admission` 的内存实现**不是疏漏，而是被明确接受的取舍**。

### 3.2 SQLite 并发处理是真正理解过的

`infrastructure/persistence/session.py`：WAL + `synchronous=NORMAL` + `busy_timeout=30000` + `temp_store=MEMORY` + `foreign_keys=ON`，连接池 `QueuePool(5/10/30s/3600)`。

`retry_on_locked` / `commit_with_locked_retry` / `transaction()` 的 docstring 讲清了语义：flush 阶段锁冲突已回滚事务，此时重试 commit 只会提交空事务，结果是 **HTTP 200 + 数据静默丢失**。有回归测试兜底（`regression_tests/test_unit_of_work.py::test_lock_conflict_is_not_masked`）。

### 3.3 执行引擎的事务边界设计

`application/use_cases/run_task.py` 采用三段式：**detached read plan → 外部引擎执行（事务外）→ 短写回**，`_release_read()` 在网络传输前显式结束读事务。文件顶部有触发方真值表注释（http / sse / scheduler 的 `share_check_always_notify`、`persist_execution`、`manual_run` 取值与调用点）。

### 3.4 错误模型统一

`core/errors.py` 的 `@dataclass ApiError(code, message, http_status=400, detail)` + 四个构造器；`core/exception_handlers.py` 把 `ApiError`、`RequestValidationError`、兜底 `Exception` 统一为 `{code, message, detail}`。前端只需认一种错误形状。

### 3.5 图片代理的 SSRF 防护接近安全团队实践

`application/proxy_image_cache.py`：白名单子域 + 禁 userinfo/非标端口/fragment + `ipaddress` 校验 `is_global/is_multicast/is_reserved/teredo/6to4/ipv4_mapped` + **连接钉在已校验数字 IP 上而保留 Host/TLS SNI**（防 DNS rebinding）+ `redirect=False` + 15s 总超时 + Content-Type 白名单 + `Content-Encoding` 必须 identity。有 `test_proxy_image_security.py` 配套。

### 3.6 SSE 实现专业

`api/routes/tasks_stream.py`：`deque(maxlen=256)` 背压、`threading.Lock` + `asyncio.Event` + `call_soon_threadsafe` 跨线程唤醒、15s `: ping` 心跳、断开置 `stopped` 丢弃事件。任务跑独立线程，不占 anyio 线程池。

### 3.7 调度器参数正确

`infrastructure/scheduling/task_scheduler.py` 四个 job 均带 `max_instances=1, coalesce=True, misfire_grace_time=60`，避免慢任务堆积与停机补跑风暴。

### 3.8 CI 已有 PR 级测试与迁移门禁（v1 误判，v2 更正，v3 补充）

`.github/workflows/docker-publish.yml` 在 **main 推送与 PR** 时执行完整后端测试（`pytest tests regression_tests -q`，环境变量 `XXM_TESTING=1 / DATABASE_URL=sqlite:// / RUN_MIGRATIONS=0 / SCHEDULER_ENABLED=0`），随后做 `alembic upgrade → downgrade → upgrade` 的迁移往返冒烟；Docker 构建 `needs: backend`，测试失败则不会产出镜像。

另两条门禁：`layering-gate.yml`（分层预算，**v3 起 PR 与 push main/master 都跑**）、`db-schema-gate.yml`（空库建表 + schema drift + 迁移预检）。v3 新增的 `ruff check app` 与 `mypy --strict app/domain/ports` 两步也在同一条工作流里。

**测试门禁与「死代码预算」都不缺，缺的是覆盖足够宽的 lint/type 门禁**——ruff 只开了 3 条规则、mypy 只覆盖 11 个文件，详见 P1-7。

### 3.9 安全卫生整体干净

| 检查项 | 结果 |
| --- | --- |
| `shell=True` | 0 处 |
| `eval` / `exec` / `os.system` | 0 处 |
| `verify=False`（关闭 TLS 校验） | 0 处 |
| `pickle` / 不安全 `yaml.load` | 0 处 |
| 字符串拼接 SQL | 0 处 |

`/api/metrics` 用 `hmac.compare_digest` 比对 token；未配置时降级为"登录 + `audit:read`"双路鉴权。

### 3.10 认证与密钥管理

- `application/auth.py`：refresh token 轮换正确（置 `revoked_at` + 签发新 token，sha256+pepper 存哈希，httponly cookie）。
- `application/auth_context.py`：权限缓存 30s TTL、容量 1024、支持主动失效。
- `core/settings.py`：`secrets.json` 首次运行生成，`chmod 0600` + 原子替换。
- `core/security.py:35`：`int(now.timestamp())`，naive `datetime.now()` 的 `.timestamp()` 按本地时区解释得到的正是正确 Unix 时间戳，**JWT 有效期不随时区偏移**（v1 此处判断有误）。

---

## 四、问题清单

### P0-1　网盘 cookie 明文入库（v3：设计完成，代码未实施）

- **位置**：`app/infrastructure/persistence/models/drive_account.py`
- **现象**：`cookie: Mapped[str] = mapped_column(Text, nullable=False)`、`config_json` 同为明文 Text。
- **影响**：`data/app.db` 泄露（备份、误提交、共享排障）即全部网盘账号失守。项目对 `secrets.json` 做了 0600 保护，却没有保护风险更高的凭据。
- **建议**：加密可行，但**必须作为独立设计处理**：密钥存放位置（与库分开）、密钥轮换、备份与恢复、旧数据迁移口径要一起定。若密钥与数据库同处一台机器且同权限，加密只防「拷走单个 db 文件」这一种场景，收益有限——这一点要在设计文档里写清楚，而不是简单「加个 Fernet」。
- **v3 现状**：`docs/backend-credential-protection.md` 已给出可实施方案——独立 `credentials` 表（credential UUID / schema_version / revision / key_id / nonce / ciphertext+tag）、AES-256-GCM、AAD 绑定实例 UUID 与 `drive_type`、96bit 随机 nonce 加唯一约束、密钥与 `secrets.json` 分开放、v0 明文 → v1 密文双读并用条件写防「旧刷新覆盖新扫码」、轮换时新 key_id 只写不读旧 key。文档同时承认：旧程序不能读写迁移后的库，迁移需要维护窗口。**代码零实施，明文落库与 API 明文返回都还在，P0 成立。**

### P0-2　登录接口无失败限流（v3：已修复）

- **原问题**：`app/api/routes/auth.py` 的 `login` 直接调用 `authenticate_user`，唯一的限流器 `core/runtime/rate_limiter.py` 只服务网盘 API，登录口可被无限次爆破。
- **修复内容**：`app/application/login_throttle.py`（100 行）做 IP + 用户名双维度计数（默认 5 次 / 30 次、窗口 600s、冷却 900s、容量 10,000）；`attempt()` 是 contextmanager，锁外跑 bcrypt，同一组合在途直接 429；`authenticate_user` 改为上下文管理器，路由以 `with` 调用；`ApiError` 新增 `headers`，异常处理器透传 `Retry-After`。回归测试 `test_login_throttle.py`、`test_login_route_security.py`。
- **配套收紧**：仓库根 `default`（nginx）把 `X-Forwarded-For` 从 `$proxy_add_x_forwarded_for` 改为覆盖 `$remote_addr`；`Dockerfile:55` 的 uvicorn 只听 `127.0.0.1:8000` 且 `--forwarded-allow-ips=127.0.0.1`，伪造头不再影响计数键。
- **残留**：`login_throttle.py:61-62` 每次调用都 `_sweep` 两个字典，被立刻拒绝的请求也要扫约 2 万条，拒绝路径存在 CPU 放大；失败登录同步写审计并 `commit`，被爆破时会放大 SQLite 单写者压力；同一组合的并发请求直接 429（`Retry-After: 1`），双标签页体验不佳。另外若部署在 Cloudflare Tunnel 或二级反代之后，nginx 看到的 `$remote_addr` 是上游代理，按 IP 的计数会把所有客户端并成一个桶。

### P1-1　首次初始化存在并发窗口（v3：已修复）

- **原问题**：`app/api/routes/setup.py:31-40` 先 `is_initialized(db)`（底层 `users_repo().has_any_user(db)`）再 `create_initial_admin`，两步之间无锁；两个并发请求用**不同用户名**都能通过检查，结果是两个初始管理员，username 唯一约束挡不住。
- **修复内容**：`infrastructure/persistence/repositories/system_settings.py:17` 的 `claim_initial_admin` 用 SQLite `insert(...).on_conflict_do_nothing(index_elements=[key])` 抢 `system.initial_admin` 哨兵，`rowcount == 1` 才算抢到；`application/use_cases/initialize_admin.py:29` 在一个 `transaction(db)` 里完成「检查已初始化 → `db.rollback()` 结束读快照 → 抢哨兵 → 建用户 → 授角色 → 发令牌 → 审计」一次提交。`api/routes/setup.py` 从 40 行降到 29 行。
- **验证**：`regression_tests/test_setup_concurrency.py` 用真实文件 SQLite + `Barrier` + 独立会话，断言并发结果为 `[200, 409]`；另覆盖历史库无哨兵、保留键、DB 故障 503、无关 IntegrityError 可重试。
- **残留**：`claim_initial_admin` 用 `sqlalchemy.dialects.sqlite` 的 insert，等于把方言锁死在 SQLite（与现状一致，但换库要改）；`set_value` 对保留键抛 `ValueError`，经兜底处理器会变成 500 而非 4xx。

### P1-2　时区口径不统一（JWT 无问题，数据层有问题）

- **位置**：`app/application/auth.py:17` 的 `_utcnow()`、`_normalize_dt`，以及全项目 44 处 `datetime.now()`。
- **v1 误判更正**：JWT 的 `iat/exp` 用 `int(now.timestamp())`，naive 时间按本地解释后得到的是正确 epoch，**不会因部署时区天然偏移 8 小时**。实测令牌有效期与配置的 `access_token_expires_seconds` 一致。
- **真实问题**：
  1. 数据库里存的是 **naive 本地时间**，历史记录在更换部署时区后解释会变化；
  2. `_utcnow()` 名字是 UTC，实际返回本地时间，属于名不符实，容易被后人误用；
  3. 统计上 `datetime.now()` 44 处、`utcnow` 13 处、`timezone.utc` 仅 7 处，缺乏统一口径。
- **建议**：先定"存量数据是什么时区"的解释口径与转换脚本，再统一到带时区的写法。**不要直接全局替换成 UTC**——未同步转换旧数据会在切换瞬间引入整体偏移。

### P1-3　56 个函数 ≥80 行，最差 254 行（v3：最差降了，数量没降）

| 行数 | 位置 | 函数 |
| ---: | --- | --- |
| 254 | `infrastructure/execution/task_executor.py:105` | `run_task` |
| 252 | `application/share_preview_batch.py:541` | `preview_share_batch` |
| 238 | `application/use_cases/task_lead_in.py:266` | `_preview` |
| 232 | `application/share_preview_plan.py` | `plan_preview_items` |
| 205 | `application/lead_in/settings.py:123` | `update_drama_lead_in_settings` |
| 188 | `application/share_preview_batch.py:351` | `fetch_share_file_list_grouped` |
| 174 | `infrastructure/drive/adapters/cloud189_adapter.py:2086` | `save_file` |
| 172 | `application/resource_search.py:244` | `fetch_task_suggestions_result` |

- **v3 变化**：`preview_share`（上一版 374 行）已按「编排 / 读取 / 纯计算 / 端口」拆开——`application/share_preview.py` 只剩 27 行编排（只读会话守卫 → 取 reader → `end_read_tx` → `reader.read()` → `plan_preview_items()` → 组装 DTO），读取在 `infrastructure/drive/share_preview_reader.py`（72 行，只 deepcopy 账号配置、不持 Session），纯计算在 `application/share_preview_plan.py`（266 行），端口在 `domain/ports/share_preview.py`（`SharePreviewReaderPort` / `PreviewRenamerPort`）。
- **同时修掉的真实缺陷**：旧代码目标目录适配器初始化失败时 `except Exception: ok = None`，然后当成「没有已存在文件」静默继续；现在返回 `DRIVE_ACCOUNT_UNAVAILABLE`。旧代码只校验 `detail.get('code')`，现在还校验 `data.list` 是 list 且每项是 dict，畸形响应不再被当空目录。调试日志里的文件名也去掉了。
- **拆分只是搬了位置**：等价性已逐行核对（`pick_fid` / `bool_is_dir` / `pick_updated_at` 等仍用 `drive_payload` 的同一套解析器，筛选顺序不变），所以 ≥80 行的函数仍是 56 个——一个 374 行换成了「27 行 + 232 行」。
- **仍未拆的更大头**：`run_task`（254 行）、`preview_share_batch`（252 行）、`_preview`（238 行）。手法可以直接复用 E 包。
- **验收口径**：不以行数硬指标度量，改为「新增代码不得增加 ≥80 行函数的数量」（当前基线 56）。

### P1-4　装配层类型薄弱（58 个函数，22 个返回 `ModuleType`）

- **位置**：`app/application/container.py`（406 行）。
- **精确统计**：顶层函数 **58 个**，其中 **22 个** 返回 `ModuleType`（无任何成员类型信息），**33 个完全没有返回注解**（如 `clock` / `adapter_registry` / `drive_gateway` / `task_run_store`）。
- **影响**：
  - 静态分析、IDE 跳转、类型检查在调用点全部退化（**注意：pytest 与覆盖率仍正常工作，v1 此处表述夸大**）；
  - 仓储方法名/参数写错只到运行时才暴露。
- **v3 变化**：`deferred_imports` 已从 116 降到 110，且基线改为逐条语句指纹——未经批准的延迟导入会让 CI 失败，逃生舱第一次被真正计数。E 包新增的 `share_preview_reader` / `share_preview_renamer` / `share_preview_min_bytes` 三个访问器都带返回类型（前两个返回 `SharePreviewReaderPort` / `PreviewRenamerPort`），新代码方向是对的。
- **建议**：不必立刻重写整套装配。给高频入口（repo、executor、notifier）补返回注解，比换 DI 框架便宜得多；`task_executor_cls` 这类已无引用的工厂随死代码一起删（v3 已删）。

### P1-5　命名与现状脱节：`legacy_notify` 其实是唯一实现（v3：已改名）

- **原问题**：`application/notifications/legacy_notify.py`（847 行）名为 legacy，实际是唯一发送实现；`send_async()` 只是入队入口，消费者最终仍调 `send()`；`use_notify_queue` 是「同步 / 入队」运行模式开关，不是灰度开关。
- **v3 处理**：23 个渠道实现 + 同步扇出 + 配置快照锁整体搬到 `application/notifications/channel_delivery.py`（799 行），关键词过滤抽到 `filtering.py`；`sender.py` 负责同步测试发送、运行时过滤与排队、唯一消费者装配。代码里已无 `legacy_notify` 引用，只剩文档提及。
- **搬迁质量（已核对）**：CR 归一后与旧文件逐行比对，差异只有「+1 行模块 docstring」和「删除尾部 queue 单例 / `send_async` 那 48 行」，**23 个渠道函数 0 行改动**；`queue.py` 只有注释措辞变化。说明这轮是干净的重命名 + 搬迁，没有夹带行为改动。
- **残留**：`use_notify_queue` 这个名字仍在（语义上更像 `notify_async`）；`TaskRunNotifier.send()` 现在把「被关键词过滤」和「队满」都返回 False，调用方 `use_cases/run_task.py:102` 不读返回值所以无影响，但将来若要用这个信号，需要区分两种 False。

### P1-6　孤立兼容层：`run()` / `_legacy_execute`（v3：已删除）

- **原问题**：`application/use_cases/run_task.py` 的 `run()` 与 `_legacy_execute` 在生产没有任何调用点（唯一调用者是回归测试），而开关 `use_unified_run_use_case` 还在 `settings.py` 里重复定义两次，让读者误以为存在两条现役路径。
- **v3 处理**：`run()`、`_legacy_execute`、`executor_cls` 注入缝、`use_unified_run_use_case` 字段、`application/factories.py`、`container.task_executor_cls` 全部删除，`settings.py` 的第二份重复开关块一并消失。生产与测试都只剩 `execute(command, policy)` 一条路径。
- **验证方式值得保留**：删除前先建了「五类触发方 × 分享链接有效 / 无效」的行为基线（`regression_tests/test_execute_trigger_behavior.py`，固定非运行日，断言是否转存、是否跳过、分享预览是否零执行记录、通知次数精确），删除后基线全绿。这是「先建安全带再动刀」的正确做法。

### P1-7　lint / 类型门禁（v3：已建立，但很窄）

- **v3 现状**：新增 `backend/pyproject.toml`（10 行）：`[tool.ruff]` 只开 `F821/F822/F823`（未定义名字），`[tool.mypy]` 只覆盖 `app/domain/ports` 且 `strict = true`。CI 增加 `ruff check app` 与 `mypy --strict app/domain/ports` 两步，实跑均通过。
- **仍然很窄**：ruff 三条规则挡不住 import 顺序、未使用导入、裸 `except`、可变默认参数；mypy 只覆盖 11 个文件（363 行）；`Any` 注解 1,167 处、`type: ignore` 13 处；没有格式化工具，也没有 `pytest.ini`。
- **建议**：ruff 扩到 `E,F,W,I`（先 `--fix` 跑一遍再提交，diff 虽大但一次到位），mypy 按 `app/domain` → `app/core` → `app/application` 逐目录加，每加一个目录先修完再提交；`pytest.ini` 里固定 `--basetemp`，顺手消掉本机那批历史 error。

### P1-8　适配器层是移植代码

- **位置**：`app/infrastructure/drive/adapters/`（14,321 行，约 30%）。
- **现象**：`quark_adapter.py:12` 有 `sys.path.insert(...)` 路径 hack；`import requests` 出现在方法体内 22 次；中英注释混杂。
- **正面**：HTTP 调用普遍带 timeout（`guangya_adapter.py` 的 `requests.post` 均 `timeout=20`），未见无超时裸调用。
- **建议**：不要求重写。先做两件低风险的事：(1) 抽共享 `http_session`（统一 timeout / 重试 / UA / 日志）；(2) 把"分享 → stoken → 列目录 → 翻页"抽成模板方法，各适配器只实现差异点。

### P2　清理项

| 编号 | 问题 | 位置 | 状态 |
| --- | --- | --- | --- |
| P2-1 | 灰度开关字段整块重复定义 | `core/settings.py` | **v3 已删**（第二份重复块连同 `use_unified_run_use_case`） |
| P2-2 | 模块 import 写在 docstring 之前，文件 `__doc__` 失效 | `api/routes/tasks.py` 顶部 | 待修 |
| P2-3 | `/auth/me` 重复装载权限 | `api/routes/auth.py` | 待修 |
| P2-4 | JWT 写入 `perms` 但 `authenticate()` 从不读取 | `core/security.py:35` | 待修 |
| P2-5 | `/health` 缺 `BUILD_*` 时回退 fork git | `api/routes/health.py:18,33` | **v3 已加 `@cache` + 首次填充锁**，无环境变量时也只执行一次 |
| P2-6 | 残留 `backend/pytest-of-qiuqu` 导致本机用例 PermissionError | `backend/` | 目录仍在；`--basetemp` 可绕过，建议删除并在 `pytest.ini` 固定 basetemp |
| P2-7 | `schema_drift_allowlist.txt` 中 10 张历史表「临时豁免」无期限 | `scripts/schema_drift_allowlist.txt` | 待定（建议加「数量不得增加」断言） |
| P2-8 | `.layering-baseline.json` 的 `deferred_imports` 预算未生效 | `scripts/check_layering.py` | **v3 已生效**：逐条语句指纹，未经批准即失败 |
| P2-9 | git 历史无有效信息（最近 12 个 commit 都是「自用」） | `git log` | 待改（`type(scope): subject`） |
| P2-10 | `backend/` 下残留 8 个 `pip-metadata-*` / `pip-unpack-*` 临时目录 | `backend/` | 未跟踪也没进 `.gitignore`（`.dockerignore` 已排除） |
| P2-11 | 任何新增相对导入都会让分层门禁失败（基线只批准 11 条） | `scripts/check_layering.py` | 设计意图是「新增要过评审」，但报错信息应提示「请更新基线并说明原因」 |

---

## 五、改进路线图

v2 的路线图按「公网前 → 门禁 → 业务边界 → 独立立项」排序。v3 逐项打了状态：**阶段一已清空，阶段二只剩「加宽」，阶段三做到一半，阶段四仍是 0**。

### 阶段一：公网暴露前（v3 状态：已完成）

| 任务 | 验收标准 | 状态 |
| --- | --- | --- |
| 登录失败限流（P0-2） | 连续 5 次失败返回 429，带 `Retry-After` | **已完成**：`login_throttle.py` + 路由 `with` + 回归测试 |
| 初始化并发保护（P1-1） | 并发请求只有一个成功，另一个收到 409 | **已完成**：`claim_initial_admin` 哨兵 + 单事务 + 并发测试 |

### 阶段二：补门禁（v3 状态：骨架已建，覆盖很窄）

| 任务 | 验收标准 | 状态 |
| --- | --- | --- |
| `pyproject.toml` + ruff + 渐进 mypy（P1-7） | CI lint job 通过 | **部分完成**：ruff 只开 `F821/F822/F823`，mypy 只覆盖 `app/domain/ports`；下一步扩到 `E,F,W,I`，mypy 按目录逐个加 |
| 消除重复配置字段（P2-1） | 每个开关只出现一次 | **已完成** |
| `deferred_imports` 纳入预算（P2-8） | 数字上涨时 CI 失败 | **已完成**，且改为逐条语句指纹（比单纯计数更严） |
| 清理残留目录与文档小问题（P2-2/3/4/6） | 本机 `pytest` 0 error；`__doc__` 生效 | **待做**：`backend/pytest-of-qiuqu` 仍在，`tasks.py` 的 docstring 仍被 import 挤到后面 |

### 阶段三：业务边界（v3 状态：最脏的一个函数拆完了，更大的三个还在）

| 任务 | 验收标准 | 状态 |
| --- | --- | --- |
| 拆 `preview_share`（P1-3） | 编排层 ≤60 行，筛选与 diff 是纯函数并有单测 | **已完成**（374 → 27 行编排 + 232 行纯计算 + 72 行读取 + 端口），且顺手修掉「适配器初始化失败被当空目录」的静默降级 |
| 拆 `run_task` / `preview_share_batch` / `_preview` | 同上 | **待做**：254 / 252 / 238 行，手法可直接复用 E 包 |
| 装配层补类型（P1-4） | 高频入口有返回注解或 Protocol | **部分完成**：新增 3 个带注解访问器，删掉 `task_executor_cls`；仍有 22 个 `ModuleType` + 33 个无注解 |
| 处理孤立兼容层（P1-6） | `run()` 与 `_legacy_execute` 删除 | **已完成**：连同 `factories.py`、`use_unified_run_use_case` 一起删除，删前先建行为基线 |
| 收拾缺省异常（P0-1 相关文档卫生） | 静默降级改为返回 warning 列表 | **待做** |

### 阶段四：独立立项（v3 状态：两份设计已成稿，代码 0 实施）

| 任务 | 说明 | 状态 |
| --- | --- | --- |
| 凭据加密（P0-1） | 先出设计：密钥存放、轮换、备份恢复、存量迁移；再实施 | **设计完成**（`docs/backend-credential-protection.md`），**代码未动**，明文落库仍在 |
| 时区口径统一（P1-2） | 先定存量数据解释口径与转换脚本，再改代码 | **设计完成**（`docs/backend-time-policy.md`）+ 认证路径已用注入时钟验证，**数据层未动** |
| 适配器抽象（P1-8） | 共享 `http_session` + 模板方法，按需推进 | **未动**（只抽出 `listing_protocol.py` 的 43 行常量与响应判定） |

### 如果只能做三件事

1. **把凭据加密真正落地**（阶段四第一项）——设计已经有了，剩下的只是排期；这是唯一一个「文档再漂亮、泄露时也救不了」的问题。
2. **把 ruff/mypy 的覆盖面拉起来**（阶段二）——4.7 万行没有类型检查，任何重构都只能靠 1,014 个用例兜底。
3. **按 E 包的手法再拆三个长函数**（阶段三）——`run_task`、`preview_share_batch`、`_preview`；E 包已经证明这条路可行且能顺手挖出真实缺陷。

---

## 六、附录

### 附录 A：评审方法

1. **结构扫描**：文件数/行数/分层占比/文件与函数长度分布（AST 解析，非正则）。
2. **动态验证**：`pytest` 全量执行（1,014 passed / 2 skipped）；`scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json`；`ruff check app`（0.14.4）；`mypy --strict app/domain/ports`（1.18.2）。
3. **安全扫描**：`shell=True`、`eval`/`exec`/`os.system`、`verify=False`、`pickle`、字符串拼接 SQL、`datetime` 用法分布。
4. **配置核对**：CI workflow 触发条件与步骤、Alembic 迁移链、`schema_drift_allowlist`、依赖钉版本。
5. **关键路径精读**：认证与令牌轮换、任务执行引擎、SSE 流、图片代理、SQLite 会话与锁重试、调度器、通知队列。

### 附录 B：统计口径

| 指标 | 口径 |
| --- | --- |
| 文件数 / 行数 | `backend/app/**/*.py`，排除 `__pycache__` |
| 长函数 | AST 中 `FunctionDef`/`AsyncFunctionDef` 的 `end_lineno - lineno + 1 ≥ 80` |
| `Any` 注解 | 匹配 `: Any` / `-> Any` / `[Any]` / `Any]` |
| 端点数量 | `@router.get` / `.post` / `.put` / `.delete` / `.patch` 装饰器 |
| 装配层函数 | `container.py` 顶层 `FunctionDef`，区分 `returns` 是否为 `ModuleType` 或缺失 |
| 时区统计 | 匹配 `datetime.now()`、`utcnow`、`timezone.utc` |

### 附录 C：已知未覆盖

- `application/queries/task_views.py`（据注释为 O(1) 查询设计）；
- `infrastructure/scheduling/task_scheduler.py` 中 `run_drama_tasks` 主循环分支细节；
- `application/telegram_bot/actions.py`（1,065 行）的交互状态机；
- `application/tmdb_cache.py`（1,215 行）的并发控制细节；
- 五个网盘适配器的协议实现正确性（仅抽查 `save_file` 与错误处理模式）；
- 前端与后端的契约一致性（另见 `regression_tests/test_frontend_contract_golden.py`）。

### 附录 D：v1 → v2 修订记录

| # | v1 说法 | 实际情况 | 证据 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | pytest 只在打 tag 时运行 | PR 与 main 推送都跑完整测试 + 迁移往返，Docker 构建依赖测试通过 | `.github/workflows/docker-publish.yml:3-32` | 已更正为 3.8；"工程化"评分 4→5 |
| 2 | container 有 56 个返回 `ModuleType` 的函数 | 56 个工厂函数中 22 个返回 `ModuleType`，34 个无返回注解 | AST 统计 `backend/app/application/container.py` | 已更正为 P1-4 的精确数字 |
| 3 | container 导致测试、覆盖率工具全部失效 | 动态装配削弱静态分析，不影响 pytest/覆盖率 | 857 用例正常收集执行 | 已改为"静态分析/跳转/类型检查退化" |
| 4 | `legacy_notify` 是可删的旧实现，存在双实现 | 它是唯一发送实现；`send_async` 只是入队入口，消费者仍调 `send()` | `notifications/sender.py:35,94`；`__init__.py` re-export；`queue.py:83` | 已改写为 P1-5（命名问题） |
| 5 | 单进程前提完全无声明 | ADR 0003 明确记录，并列出 12 项进程内状态与后果 | `docs/architecture/adr/0003-single-process-deployment-assumption.md` | 已从 P0 移出，改为 3.1 的优点 |
| 6 | `/health` 每次必 fork 两次 git | 仅在缺少 `BUILD_TAG`/`BUILD_SHA` 时回退；Dockerfile 已注入 | `Dockerfile:22-25`；`health.py:66-67` | 已降级为 P2-5 |
| 7 | `_legacy_execute` 约 210 行 | 22 行；`run()` 在生产无调用点，仅回归测试调用 | AST 统计 `run_task.py`（159/189 行处） | 已改写为 P1-6 |
| 8 | JWT 因本地 `datetime.now()` 产生时区偏移 | `int(now.timestamp())` 得到正确 epoch，有效期无偏移 | `core/security.py:35` | 已改为 P1-2，仅保留数据层问题 |
| 9 | 35 个 error 说明测试框架需加自动清理 | 由仓库残留目录导致，CI 不受影响 | `backend/pytest-of-qiuqu` 存在 | 已改为 P2-6，不再作为框架结论 |

### 附录 D–2：v2 → v3 修订记录

| # | v2 说法 | v3 实际情况 | 证据 | 处理 |
| --- | --- | --- | --- | --- |
| 1 | `legacy_notify.py` 是唯一实现，只能改名不能删 | 已整体搬到 `channel_delivery.py`（799 行），23 个渠道函数逐行未改，`queue.py` 代码零变化 | CR 归一后的 diff | P1-5 改为「已改名」 |
| 2 | `run()` / `_legacy_execute` 是孤立的兼容层 | 已删除，连同 `factories.py`、`use_unified_run_use_case`、`task_executor_cls` | `run_task.py` 顶部 docstring | P1-6 改为「已删除」 |
| 3 | `preview_share` 374 行，是全项目最差函数 | 已拆为 27 行编排 + 232 行纯计算 + 72 行读取 + 端口；最差函数变成 `run_task` 254 行 | AST 统计；等价性逐行核对 | P1-3 重写 |
| 4 | `settings.py` 灰度开关重复定义 | 第二份已删除，`use_unified_run_use_case` 整体消失 | `core/settings.py` | P2-1 标为已完成 |
| 5 | `deferred_imports` 预算从未生效 | 已改为逐条语句指纹；违规/循环不可被基线批准；基线缺失 exit 2 | `check_layering.py` + `.layering-baseline.json` | P2-8 标为已完成，数字 116 → 110 |
| 6 | 缺 lint/type 门禁（P1-7） | 已建 `pyproject.toml` 并在 CI 跑，但只有 3 条 ruff 规则 + 11 个文件的 strict mypy | `backend/pyproject.toml`（10 行） | P1-7 改为「已建立但很窄」 |
| 7 | 登录无限流（P0-2）、初始化有并发窗口（P1-1） | 两者都已修复并补了回归测试 | `login_throttle.py`；`test_setup_concurrency.py` | 标为已完成 |
| 8 | 凭据明文与时间口径是「待修」 | 现在各有独立设计文档，代码仍未实施 | `docs/backend-credential-protection.md`；`docs/backend-time-policy.md` | 保留在阶段四，标注「设计完成 / 代码未动」 |
| 9 | `layering-gate.yml` 只在 PR 跑 | push main/master 也跑 | 工作流 `on:` 段 | 已更正为 3.8 |
| 10 | v2 总分 6.5 | 7.4（架构 8.5 / 可维护 6.5 / 安全 7 / 正确性 7.5 / 测试 8 / 工程化 7） | 1.2 节加权算式 | 见 1.1/1.2 |

### 附录 E：对复核意见的处理

- **采纳**：初始化竞态应从清理项提升为必修项（已升为 P1-1，并补充"不同用户名可产生两个管理员"的机制）；凭据加密需独立设计（已写入 P0-1 与阶段四）；路线图顺序按其建议重排；"教科书级/半天成本"等表述已收敛。
- **保留并补强**：长函数与装配层类型薄弱两项结论成立（复核意见亦认同"类型薄弱成立"），但已改用精确数字与可复核口径。
- **补充**：复核意见指出 `legacy_notify` 不可直接删除，据此进一步核查发现 `run()`/`_legacy_execute` 在生产**无调用点**，这比"双实现"更接近事实，已单列为 P1-6 并给出两种处理方式。
- **不再坚持**：v1 中"CI 无测试门禁""单进程无声明""/health 每次 fork""JWT 时区偏移"四项判断已被代码证据推翻。

---

## 七、一句话总结

> 这套后端的"设计"层一直称职——知道事务边界在哪、SSRF 要怎么防、SSE 如何跨线程唤醒，甚至把单进程约束写成了包含 12 项状态的 ADR；这一轮把"组织"层最烂的三处动了真刀：374 行的分享预览拆成「编排 / 读取 / 纯计算 / 端口」，名为 legacy 的唯一实现改回本名，孤立的兼容执行路径连同开关一起删掉，而且**每拆一处都先建行为基线、拆完顺手挖出真实的静默降级**。剩下的是三个没拆的长函数、一个只有骨架的 lint 门槛，和一份写得很细但还没写进代码的凭据加密方案——7.4 / 10，方向对了，差的是把设计变成代码的那几步。
