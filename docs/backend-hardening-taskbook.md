# 后端健壮性、可维护性与扩展性改造任务书

日期：2026-11-13

状态：任务书草案，供评审与后续分批实施；本文不代表代码已经改造完成。

范围：本轮代码审查第 3～6 项。任务执行中断恢复、持久化队列及多进程扩容不在本轮范围。

## 1. 目标与现状

本轮要解决四类问题：业务编排散落在入口层、网盘返回结构泄漏到执行流程、列表与日志占用随数据量增长、异常与自动检查不能充分反映实际故障。

成功标准是业务入口变薄、网盘差异可局部修改、常用列表不再传输完整执行日志、真实故障可识别，且现有转存、分享检测、手动引流、Telegram 和前端交互均有回归保护。文件行数、目录数量、Protocol 数量不作为完成指标。

### 1.1 已核查的代码位置

以下路径均相对仓库根目录；函数名作为定位锚点，避免改造后行号失效。

| 问题 | 现有位置 | 核查结果 |
| --- | --- | --- |
| 路由组织执行与通知 | `backend/app/api/routes/tasks.py::post_run_task`、`tasks_stream.py` | 路由取得具体执行器、决定提交和通知 |
| 路由组织生成分享 | `backend/app/api/routes/tasks.py::post_create_task_share` | 选择账号、判断适配器能力、外部请求、保存及审计均在端点中 |
| 用例与装配边界不充分 | `backend/app/application/use_cases/run_task.py`、`container.py` | 用例主要统一参数；容器仍暴露具体类及仓储模块 |
| 网盘差异进入执行器 | `backend/app/infrastructure/execution/drama_executor.py::_call_save_file`、`_extract_saved_fids`、`_save_with_saved_fids` | 有方法签名反射、网盘类型分支和原始响应字段兼容 |
| 大模块混合职责 | `application/telegram_bot/handlers.py`、`application/drama_lead_in.py`、`infrastructure/execution/drama_executor.py` | 消息分发、编辑状态、业务操作，或配置、令牌、IO、记录等职责集中 |
| 列表全量装配 | `infrastructure/persistence/repositories/tasks.py::list_tasks_recent_executions`、`application/queries/task_views.py::list_task_views` | 已批量查询，但全部任务及最近执行记录仍一起读取 |
| 日志无单次容量限制 | `infrastructure/execution/execution_log.py::ExecutionLog` | `lines` 持续追加，SSE 有界队列不等于执行日志有界 |
| 搜索错误伪装成功 | `api/routes/tasks.py::get_task_suggestions` | 外层异常返回 `success=True`、空数组 |
| 健康探针不能靠状态码识别故障 | `api/routes/health.py::health` | 数据库失败返回 `degraded`，HTTP 仍为 200 |
| CI 未覆盖所有后端测试 | `.github/workflows/docker-publish.yml` | 只运行 `regression_tests`，未运行 `tests` |

同一轮代码审查在当前工作区运行过 `tests regression_tests`：**570 passed、2 skipped**；分层扫描结果为 **252 模块、90 deferred imports、0 violations、0 import cycles**。这是审查时的实测基线，不是未来固定测试数量，实施开始时必须重新记录。

工作区已有未提交修改，包括分享审核状态、分类、前后端契约与文档。实施前记录并保留这些变更，不重置、不覆盖、不混入无关提交，不读取或提交 `backend/data/secrets.json` 中的秘密值。

## 2. 方案选择与边界

| 方案 | 优点 | 代价 | 决策 |
| --- | --- | --- | --- |
| 按业务动作渐进收口，接口先兼容后迁移 | 每批可单独测试和回退，可保留既有行为 | 过渡期存在明确的旧入口 | **采用** |
| 一次重写应用层、所有适配器和前端列表 | 目标结构容易统一 | 回归面过大，定位和回退困难 | 不采用 |
| 继续搬目录、增加容器转发及导入白名单 | 改动较小 | 不能消除业务耦合及数据量问题 | 不作为交付方案 |

### 2.1 本轮包含

1. 执行任务、生成分享、手动引流三个动作的应用入口与事务、审计、通知职责收口。
2. 转存文件对象、结果、能力与错误语义统一，已有网盘逐一接入。
3. 分页列表、轻量概览、执行详情和日志按需读取，以及单次日志容量限制。
4. 搜索异常与健康检查的语义修正，完整后端测试接入 CI。
5. 依据上述职责拆分 Telegram handlers、引流服务、追剧执行器。
6. 配套前端消费、契约 golden、架构约束和开发规范更新。

### 2.2 本轮不包含

- Redis、Celery、新 DI 框架、替换数据库、修改既有依赖版本。
- 持久化任务队列、运行中记录、崩溃自动恢复、多 worker 或多容器副本支持。
- 新增网盘、删除 Telegram 功能、修改分享审核和运行日期的业务口径。
- 重写全部 CRUD、全部仓储、TMDB 专用 writer，或清理与本轮无关的遗留表。
- 将分层违规数为零解释为全部解耦完成；将 mock 测试通过解释为真实网盘联调通过。

## 3. 实施约束

- 保持 Python 3.13、FastAPI、SQLAlchemy、SQLite、Vue 3/TypeScript 的现有技术栈；本轮不增删改依赖。
- 保持单进程部署和最大 4 个任务执行槽。HTTP/SSE 重复任务返回 409、容量不足返回 429；SSE 在建立连接和启动 worker 前拒绝。
- 保留 `RunPolicy` 的触发差异：HTTP/SSE/Telegram/preview 是手动执行；scheduler 按运行星期执行；preview 不写执行历史。
- 保留分享 `pending`、失败分类、自动换链边界，以及引流的预览确认、查重、并发锁、超时待确认语义。
- 引流继续是独立手动操作，不接入自动转存链路；无 Telegram 配置时不产生 Telegram 外部请求。
- 新用例接收明确的命令/上下文 DTO，返回应用层结果；不返回 FastAPI 响应、Pydantic API schema、适配器实例或仓储模块。
- 工厂装配可以使用具体类；业务调用方通过容器取得已装配的用例/端口实例。禁止把 `TaskExecutor` 换个名字转发后就宣布解耦完成。
- 目标动作的短写事务由应用用例拥有；SSE 序列化、心跳、断线处理属于表现层。准入暂按 ADR 0002 留在各入口，但通过明确端口调用，不借本轮改变拒绝时机。
- 不持有数据库写事务执行网盘/HTTP 请求；也不借助 `autoflush=False` 假定事务已经结束。阶段间使用值对象，明确释放只读事务，不能用长寿命 ORM 对象跨线程或驱动后续隐式查询。
- 外部转存、生成分享、引流写入不因数据库提交失败而自动重放；只重试读操作或有证据表明安全的操作。
- SQL、事务失败必须传播；可选通知失败不能推翻已经成功提交的业务结果，必须记录可定位日志。
- 沿用现有脱敏规范，日志和错误响应不暴露 Cookie、Token、代理认证和未脱敏响应。
- 不放宽分层规则，不扩大违规预算；提交归属迁移逐项登记，清除旧点，不能只增加总预算。

目标数据流：

```text
HTTP / SSE / scheduler / Telegram
        │ 权限、输入、入口准入、事件协议
        ▼
应用用例：执行任务 / 创建分享 / 引流预览与确认
        │ 阶段编排、短事务、审计、提交后通知
        ▼
明确端口：仓储 / 转存 / 分享 / 引流 / 通知
        ▼
基础设施：SQLAlchemy、网盘适配器、网络客户端
```

## 4. 批次 A：错误语义与 CI（优先交付）

### A1. 搜索结果区分空结果、降级和失败

主要文件：`application/resource_search.py`、`api/routes/tasks.py`、`schemas/resource_search.py`、`frontend/src/api/resourceSearch.ts`、`frontend/src/types/resourceSearch.ts`、`frontend/src/components/tasks/DramaTaskDrawer.vue`。

- 正常查询无匹配：HTTP 200，`success=true`、`data=[]`，提示“未找到结果”。
- 部分搜索源失败、仍有可用源完成：HTTP 200，`success=true`；新增 `partial=true`、`warnings`，供前端显示“部分来源暂不可用”。
- 全部已启用源失败：HTTP 502，错误码 `SEARCH_UPSTREAM_FAILED`；全部失败且均为超时则 HTTP 504，错误码 `SEARCH_UPSTREAM_TIMEOUT`。成功源返回空结果仍属于成功，不能算全部失败。
- 无启用源：HTTP 200、空结果，message 明确说明“未启用搜索源”。
- 数据库/程序异常：由现有全局异常处理返回 500，不转为空结果，不把异常原文发给用户。
- 已有 `ApiError` 保持原错误码；前端失败时解除 loading、保留输入、允许重试，不显示“没有资源”。
- 需要在聚合层统计源结果，不能只删除路由的 `try/except`；响应契约及前后端类型同步更新。

验收：分别注入零匹配、部分源失败、所有源失败、超时、数据库异常，断言状态码、错误码及 UI 状态。

### A2. 健康状态可被探针识别

主要文件：`api/routes/health.py`、`Dockerfile`（仅在已有探针需同步时）、部署说明。

- `/api/health` 保留现有响应字段，数据库可用返回 200，失败返回 503 与 `status=degraded`、`db_connected=false`。
- 新增 `/api/health/live`：只表示应用 HTTP 进程可响应，返回 200，不查询数据库、不请求网盘和 Telegram。
- `/api/health` 用于就绪检查；`/api/health/live` 用于存活检查，避免数据库暂时故障引发无意义重启。
- 数据库探测不写业务数据；探测失败记录日志并正常释放会话。

验收：数据库失败时 readiness=503、liveness=200；正常时均为 200；响应不包含异常堆栈或连接秘密。

### A3. CI 覆盖完整后端测试

主要文件：`.github/workflows/docker-publish.yml`，配套更新 `docs/development-guide.md`、`docs/architecture/README.md`。

- 后端任务执行 `python -m pytest tests regression_tests -q`，沿用隔离测试环境、关闭迁移和调度。
- 保留既有迁移往返及独立 schema/分层门禁；任一后端测试失败阻止依赖该 job 的构建发布。
- 本轮涉及前端的批次必须运行已有前端 regression tests 和 build；不新增测试框架。
- 不通过跳过失败测试、修改断言放水或仅更新 golden 使 CI 变绿。

验收：完整测试命令本地通过；CI 配置中的测试范围包含两组目录。远端工作流只有实际运行后才可标记“CI 已通过”。

## 5. 批次 B：日志有界、读接口拆分与前端迁移

### B1. 单次执行日志有界

主要文件：`infrastructure/execution/execution_log.py`、`core/settings.py`、`schemas/task.py`、对应 DTO/presenter/TS 类型及日志弹窗。

- 新增 `execution_log_max_bytes`，默认 **262144 字节（256 KiB）**，配置必须为正整数。
- 上限按 UTF-8 编码字节计算，包含截断提示；保留开头与末尾，中间显示一条明确截断提示。小日志输出保持原样。
- `section()`、`line()`、超长单行都经过相同容量控制；内存保留量不能随调用次数增长，不能等到 `render()` 才截断。
- `render()` 多次调用结果稳定；最终状态和异常尾部可见。被丢弃字节数与 `log_truncated` 可从结果读取并持久化到执行 snapshot 的受控元数据中。
- SSE 仍使用既有有界队列，继续发送实时消息；断线不取消正在执行的转存；服务端保存日志与实时消息可能不同，UI 需要显示“保存日志已截断”。
- 新增 `execution_tree_max_bytes`，默认 **65536 字节（64 KiB）**、必须为正整数，限制保存和响应中的 `tree_summary`，避免另一大字段绕过控制。现有成功通知会解析树文本提取新增文件名：须先改为从结构化执行结果取得新增文件，再裁剪展示树；不能让截断改变通知、重命名或进度计算结果。
- 不批量改写历史执行记录；历史超大日志的读取在 B2 单独限流式/分段处理，避免先完整装入内存再截断。

验收：大量短行、中文、emoji、单条超长行、空日志、重复 render、尾部异常；落库日志不超上限，保留内存近似随配置上限而非总输入量增长。

### B2. 新增读接口，旧接口暂时兼容

主要文件：`api/routes/tasks.py`、新增 `api/routes/task_executions.py`、`application/queries/task_views.py`、新增 `application/queries/task_executions.py`、`infrastructure/persistence/repositories/tasks.py`、`repositories/task_executions.py`、schema/DTO/presenter。

| 接口 | 用途与契约 |
| --- | --- |
| `GET /api/tasks/page` | `page>=1`，`page_size` 默认 20、范围 1～100；返回 `items,total,page,page_size,facets`；稳定默认排序为 `id DESC` |
| `GET /api/tasks/overview` | 工作台、日历、任务选择与重复判断所需的轻量任务集合；不返回日志、树、大 snapshot、未筛选的 extra/addition |
| `GET /api/tasks/{task_id}/executions` | 执行历史摘要分页，默认 20、最大 100；`started_at DESC,id DESC` 稳定排序 |
| `GET /api/tasks/{task_id}/executions/{execution_id}` | 按需读取单条详情，日志支持 `log_offset`/`log_limit`，单位 UTF-8 字节，默认/最大 64 KiB；返回下一偏移量及是否读完 |
| `GET /api/tasks/{task_id}` | 单任务编辑/详情数据；不要求调用方扫描全部任务 |
| 原 `GET /api/tasks` | 过渡期保留原响应形状，不直接改成分页对象，不偷偷只返回前 N 条；本轮完成后仅作旧客户端兼容，另行评估删除 |

权限沿用 `TASK_READ`；执行 ID 必须同时属于路径中的 task，跨任务或不存在统一 404。日志块必须在合法 UTF-8 边界截取，并返回实际消费后的下一字节偏移；最后一块不能死循环，超范围偏移返回空块且完成标记为 true。

分页与概览的共同要求：

1. 常用分页查询不能先装配全部任务 ORM/执行记录再切片；先筛选、排序、取当页 ID，再批量读取当页数据。
2. 列表执行摘要保留状态、时间、message、stage，以及分享检测徽标所需的受控小字段；不 SELECT `run_log`、`tree_summary`、完整 `adapter_snapshot`。概览还需保留最近成功执行的必要摘要及网盘类型，维持工作台“最近更新”的现有语义，不能只保留最近一次失败记录而丢掉成功记录。
3. 过滤覆盖现有关键词（名称/分享地址/保存路径/账号显示名）、启用状态、网盘、分类、最近一次执行失败；统计和下拉选项依据全体符合条件的数据，不能只统计当前页。
4. 分类继续复用 `media_category.py` 的唯一口径。第一版允许基于必要列/关联 TMDB 的轻量候选投影计算分类与 ID，但不得读取全体完整任务或日志；该路径仍有 O(N) CPU/元数据成本，必须在报告中单列，不能声称所有查询已与总任务数无关。
5. 保留现有中文名称自然排序、同名网盘优先级及稳定 ID 兜底。允许使用轻量排序索引辅助取页；不能只对当前页排序，不能擅自换成不同的字符排序。以现有前端比较器和固定样例作为一致性验收依据。
6. `facets` 至少包含分类、网盘候选及当前过滤条件下的失败任务数量；跨任务批量操作若当前语义作用于全部筛选结果，必须基于同一筛选条件在服务端解析目标，不能悄悄缩为当前页。
7. 概览仍是 O(N) 的轻量集合，仅服务需要全局视图的页面，不作为“所有接口恒定数据量”的承诺。常用追剧列表不得先请求概览或自动拉完分页来恢复全量 `TaskItem[]`。
8. 历史大日志使用数据库分段读取，保证一次读取上限；新增日志元数据在缺失时有兼容默认值，不新增数据库列。

### B3. 迁移全部内置调用方

主要文件：`frontend/src/api/tasks.ts`、`types/tasks.ts`、`stores/taskCache.ts`、`views/tasks/DramaTaskView.vue`、`views/tasks/DramaCalendarView.vue`、`views/DashboardView.vue`、`views/media/MediaDiscoverView.vue`、`components/tasks/DramaTaskDrawer.vue`、`components/tasks/GeneratedShareStatus.vue`。

- 追剧列表使用分页接口；筛选或排序变化回到第一页；处理请求竞态，晚到的旧响应不能覆盖新条件。
- 工作台/日历共享概览缓存；分页缓存按筛选条件、排序、页码区分，不能覆盖全局概览。
- 任务新建、编辑、删除、状态变更、执行完成后，按既有任务版本事件失效相关缓存；删除最后一页最后一条后回到有效页。
- 编辑抽屉按 task ID 取详情；资源页及抽屉的重复判断改用轻量概览，不再扫描完整执行日志。
- 日志和树详情在用户打开时加载；保留实时 SSE 日志、历史日志查看、错误重试、加载状态和多块日志继续读取。
- 分享检测 pending 徽标、分类、运行日期说明、工作台及日历总量不因分页而遗漏。
- API 类型分开定义摘要与详情，不通过把所有字段设为可选掩盖消费错误。同步 frontend contract golden。

验收：内置调用方不再调用旧全量 `/api/tasks`；分类、网盘、失败筛选和名称排序跨页正确；概览页面总量不缩水；历史和实时日志都可查看。

## 6. 批次 C：统一网盘转存契约

主要文件：`domain/ports/drive.py`、新增 `domain/drive_types.py`、`infrastructure/drive/gateway.py`、新增 `infrastructure/drive/transfer.py`、`drive/adapters/*_adapter.py`、`infrastructure/execution/drama_executor.py`。

沿用现有网关，不新建一套平行的“第二网盘 SDK”。通过独立的小能力端口扩展转存、分享、引流，避免所有适配器被迫实现不支持的功能。

### C1. 统一值对象及能力

- 文件对象统一 ID、名称、目录标志、大小、修改时间、可选转存令牌；缺失可选令牌不在公共层直接判断为不可转存，由具体网盘验证需求。
- 转存结果区分 `completed`、`pending`、`failed`，包含已保存文件 ID/名称、可选远端任务 ID、错误分类、结果是否已确认。提交了远端任务不等于转存完成。
- 失败分类沿用 `invalid/passcode/captcha/auth/throttled/pending/unknown`，不另造不兼容词表。是否可重试独立表达，不由 message 文本随意猜测。
- 能力显式声明：创建分享、转存结果定位、引流、本账号素材等。业务层不比较基类方法、不用 `hasattr` 或 `inspect.signature` 决定转存调用方式。
- 标准类型仅依赖标准库，不带 requests、SQLAlchemy 或具体适配器类。

### C2. 将差异归回网盘边界

- `file_names` 参数、UC 中转目录与后续移动、同步/异步完成、`taskId/task_id`、`save_as_select_top_fids` 等兼容均放在网盘适配实现或其专用策略中。
- 执行器只处理统一文件、结果和错误。不得把原来的类型分支搬到一个大公共函数后继续扩展。
- 转存结果必须正确关联来源与目标；不能默认不同网盘返回的 saved ID 顺序都与计划一致，再误重命名其他文件。
- 写操作超时且结果不明时先核对，不重新提交同一批；保留已有重试次数/退避策略，不额外叠加一层重试。
- 错误码结合操作阶段分类；例如百度 `errno=-7` 的审核与死链语境不能合并，保留已有 pending 回归。
- 逐一覆盖当前工厂注册的 quark、baidu、115、xunlei、aliyun、uc、123pan、cloud189、cloud139、guangya。声明不支持的能力明确返回 unsupported，不伪造成功。

验收：每个注册适配器跑同一套契约用例，覆盖其声明支持的能力；至少覆盖同步完成、异步轮询、审核中、限流、认证失败、结果不明、UC 中转与移动失败。新增一个最小假网盘只需实现契约并注册，无需修改追剧执行器。真实网盘联调结果另列，未验证的网盘不得标成“真实可用”。

## 7. 批次 D：收口业务用例并拆分大模块

### D1. 生成分享先独立收口

主要文件：新增 `application/use_cases/create_task_share.py`、`application/dto/task_share.py`，修改 `api/routes/tasks.py`、`application/container.py`、相关仓储与能力端口。

流程：读取任务/账号必要信息 → 结束读事务 → 调统一分享能力 → 校验返回 → 短事务保存分享记录及审计 → 返回应用结果。

- 路由只保留鉴权、命令与 actor/request 上下文映射、结果 presenter。
- 保存与审计同事务；网盘已生成但本地保存失败必须明确报错并记录可定位上下文，不自动再次创建分享。
- 不影响现有分享路径、选定账号、保存时间、提取码与不支持能力的错误码。

### D2. 任务执行用例拥有业务编排

主要文件：`application/use_cases/run_task.py`、新增 `application/dto/task_run.py`、`domain/ports/task.py`、`domain/ports/notify.py`、`application/container.py`、`application/factories.py`、`infrastructure/execution/task_executor.py`、HTTP/SSE/scheduler/Telegram 调用点。

- 统一触发策略、执行阶段、结果持久化、执行审计和通知决策；HTTP 与 SSE 不再各写一份“执行→提交→判断追剧→组装通知”。
- 对执行入口做注入，生产调用方不再传 `executor_cls=TaskExecutor`；测试注入端口实例，而非依赖路由模块中的具体类名称。
- `RunTaskUseCase` 对外返回应用 DTO，SSE 事件格式化与 HTTP schema 转换在表现层；调度和 Telegram 复用同一结果语义。
- SSE worker 在自身线程内取得会话/工作单元；预检失败和 worker 启动失败均释放执行槽。
- 拆分读取计划、外部执行、阶段结果落盘，使长 IO 不占用数据库事务。自动换链每轮渐进持久化作为显式应用阶段保留；从执行器移出的 commit 要有对应归属测试，不能用新的全流程大事务代替。
- 必须等业务结果提交成功再发最终通知；分享检查等现有独立通知另按其阶段语义保留并测试，不能无意改变强制通知策略。
- 本轮只改善结构和边界，不承诺崩溃后恢复或跨网盘/数据库原子提交。

### D3. 引流用例与职责拆分

主要文件：新增 `application/use_cases/task_lead_in.py`、`application/lead_in/settings.py`、`tokens.py`、`materials.py`、`records.py`；修改 `application/drama_lead_in.py`、`api/routes/task_lead_in.py`、相关系统设置路由与网盘能力实现。

- 配置与规范化、预览令牌、素材查询、确认编排、结果记录分别归属明确模块。
- 预览和确认通过应用用例调用统一引流能力；普通读取通过查询入口。
- 保留令牌签名/TTL、任务/账号/路径/素材绑定，拒绝过期与过时预览；同目标并发确认只产生一次写入。
- `added/exists/conflict/pending/failed` 保持原义；外部成功但记录失败不伪装 added，也不自动重放写入。
- 可短期保留旧模块公共函数的兼容导出，但必须记录调用方并在本批消费者迁完后删除；不保留两套实现。

### D4. Telegram 与追剧执行器拆分

主要文件：`application/telegram_bot/handlers.py`、新增 `telegram_bot/dispatch.py`、`task_handlers.py`、`account_handlers.py`、`settings_handlers.py`、`editor.py`；`infrastructure/execution/drama_executor.py`、新增 `execution/drama_plan.py`、`drama_progress.py`。

- Telegram 主 handler 只做授权检查、消息/回调解析与分派；任务、账号、设置、编辑状态各自负责。模块不能通过继承同一巨大基类或互相回调内部方法重新形成隐式耦合。
- 任务执行、分享、引流等业务动作调用统一应用入口；Telegram 模块只负责消息内容、按钮、会话与交互顺序。
- 拆分时保留所有当前命令、callback 编码、消息编辑、取消、分页和用户授权；不得因国内服务器不便使用而删除此功能。
- 追剧执行器保留流程协调；文件筛选/命名/增量计划及进度计算抽成可独立测试单元，网盘差异按批次 C 处理。
- 不指定机械行数上限。验收依据是一个职责变更是否只影响对应模块、是否可不启动 FastAPI/Telegram/真实网盘独立测试。

## 8. 交付顺序、依赖与回退

| 顺序 | 工作包 | 依赖 | 独立交付与回退 |
| --- | --- | --- | --- |
| 0 | 记录基线、现有行为及工作区修改 | 无 | 测试/指标记录，标明现有失败 |
| 1 | A1 搜索错误、A2 健康检查、A3 CI | 0 | 分别提交；搜索前后端原子发布，健康探针说明同步 |
| 2 | B1 日志限额 | 0 | 日志边界测试、配置说明；回退代码不改写已有记录 |
| 3 | B2 新读接口及契约 | B1 | 新接口先上线，旧接口保持兼容 |
| 4 | B3 前端迁移 | B2 | 内置客户端切新接口；新后端兼容旧前端，旧后端不承诺兼容新前端 |
| 5 | C1/C2 网盘契约逐家接入 | 0 | 每家一批，契约测试与既有业务测试同时通过 |
| 6 | D1 生成分享用例 | C | 迁移路由编排与测试注入；对外 API 保持 |
| 7 | D2 执行用例 | C、B1 | 统一五类触发方与通知事务，保留入口准入时机 |
| 8 | D3 引流拆分 | C | 沿用已有引流实现契约与 e2e 测试 |
| 9 | D4 Telegram/追剧职责拆分 | D2、D3 | 每个职责迁移独立提交，不与行为变更混做 |
| 10 | 清理兼容代码、文档与总体验收 | 全部 | 保留已声明的旧 HTTP 列表接口；删除无调用的内部转发 |

每个工作包按“行为/故障测试 → 确认旧实现暴露问题或明确现有行为 → 最小改动 → 针对性验证 → 完整门禁 → 独立提交”执行。纯职责迁移以行为对比为主，不为追求红灯故意改变预期。

后端和前端分开发布时按“先新后端、再新前端”顺序；回退按相反顺序进行。本轮默认不新增表/列；若查询实测确需索引迁移，须单列理由、执行计划、upgrade/downgrade 和数据备份验证。

## 9. 验收矩阵与测试落点

新增测试路径为本轮交付约定，现有测试继续保留；内容测试真实边界，不只断言函数被调用。

| 工作包 | 测试落点 | 必测事项 |
| --- | --- | --- |
| A1 | 新增 `backend/regression_tests/test_search_error_contract.py` | 空结果/未启用源/部分失败/全部失败/超时/数据库错误、敏感信息不外泄 |
| A2 | 新增 `backend/regression_tests/test_health_status.py` | 正常 200、DB 失败 503、live 不查库、不进行外部探测 |
| B1 | 新增 `backend/regression_tests/test_execution_log_bounds.py` | 字节限额、中文/emoji、超长单行、头尾保留、稳定 render、snapshot 元数据 |
| B2 | 新增 `backend/regression_tests/test_task_pagination.py`、`test_execution_detail.py` | 先分页后装配、稳定顺序、全局筛选及 facets、无 N+1、不 SELECT 大字段、跨任务执行 ID=404、日志块边界 |
| B3 | 新增 `frontend/regression_tests/taskPagination.test.mjs`、`executionLog.test.mjs`；浏览器核验 | 翻页/筛选/排序/删除尾页/请求竞态/缓存失效、概览完整、日志加载失败重试 |
| C | 扩展 `test_gateway_port.py`、`test_ports_conformance.py`；新增 `test_drive_transfer_contract.py` | 注册网盘参数化、原始响应归一、异步完成、缺令牌、顺序映射、超时不重复写、pending 分类 |
| D1 | 新增 `test_create_task_share_use_case.py`，保留 `backend/tests/test_task_share_route.py` | 账号选择、能力拒绝、保存与审计同事务、提交失败不再次创建分享 |
| D2 | 扩展 `test_run_task_use_case.py`、`test_task_admission.py`、`test_route_transactions.py`、`test_commit_ownership.py` | 所有触发策略、409/429 时机、槽释放、提交后通知、通知失败不改成功、事务不跨网盘 IO |
| D3 | 保留 `backend/tests/test_drama_lead_in*.py` 及引流回归测试 | 令牌、防重、素材模式、超时待确认、事务、记录、手动触发边界 |
| D4 | 新增 `test_telegram_dispatch.py`、`test_telegram_edit_flow.py`，保留追剧测试 | 授权、命令/回调、编辑/取消、模块调用应用用例，转存筛选及进度不变 |
| 门禁 | 扩展现有分层/契约测试，新增 `test_use_case_boundaries.py` | 目标路由不获取具体执行器/账号管理器/适配器，表现层不拥有目标动作提交/通知，依赖必须实际进入生产调用 |

性能使用隔离 SQLite 数据集：**200/2000 个任务、每任务 20 条历史、每条历史 32 KiB 日志**；日志含不易压缩的确定性内容。复用并扩展 `scripts/bench_task_list.py`，记录原接口与新接口的 SQL 次数、选取列、JSON 字节、gzip 字节、P50/P95 和内存，不访问真实网盘。

- 默认 20 条分页接口不得读取/返回日志及树；历史日志增大 10 倍时，该接口的返回字节不应随日志增长。
- 同样 20 条任务的新响应未压缩字节，相比旧接口截取对应任务的完整表示至少减少 80%；同等日志数据比较，不靠 gzip 掩盖大字段。
- 相同查询路径 SQL 次数不能随页内任务数线性增长；当页关联数据批量加载。
- 正常分页的 ORM/DTO 装配量受 page_size 约束；分类及名称排序的全局轻量索引成本分别报告。
- 延迟不设脱离机器环境的绝对毫秒承诺；同机同样本比较，P95 若比基线恶化超过 10%，必须记录原因并消除回归后验收。性能测试不以极窄时间阈值加入不稳定 CI。

## 10. 验证命令

仓库根目录 PowerShell，使用项目虚拟环境，避免系统 Python 缺少 pytest：

```powershell
$backendPython = (Resolve-Path .\backend\.venv\Scripts\python.exe).Path
$env:XXM_TESTING = '1'
$env:DATABASE_URL = 'sqlite://'
$env:RUN_MIGRATIONS = '0'
$env:SCHEDULER_ENABLED = '0'
Push-Location backend
try {
    & $backendPython -m pytest tests regression_tests -q
    if ($LASTEXITCODE -ne 0) { throw '后端测试失败' }
    & $backendPython -c "import app.main; print('import ok')"
    if ($LASTEXITCODE -ne 0) { throw '应用导入失败' }
} finally {
    Pop-Location
}
& $backendPython scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json
if ($LASTEXITCODE -ne 0) { throw '分层门禁失败' }
```

涉及前端的批次，在仓库根目录执行：

```powershell
$frontendRegressionFiles = @(Get-ChildItem .\frontend\regression_tests\*.test.mjs | ForEach-Object { $_.FullName })
node --test $frontendRegressionFiles
if ($LASTEXITCODE -ne 0) { throw '前端回归失败' }
pnpm --dir frontend build
if ($LASTEXITCODE -ne 0) { throw '前端构建失败' }
```

基准测试在隔离临时库中执行：`& $backendPython scripts/bench_task_list.py`。schema/迁移检查沿用 `docs/development-guide.md`，显式指定新的临时库；禁止使用用户真实 `data/app.db` 运行测试、清理或回滚。

浏览器检查覆盖桌面及窄屏：追剧翻页和分类、全局失败筛选、名称排序、生成分享、实时/历史日志、引流预览确认、日历与工作台完整数据、资源页重复判断。Telegram 使用假客户端核验消息和按钮序列；真实联调按实际环境记录。

## 11. 最终完成条件

- [ ] A～D 各工作包均有实现文件、测试证据、契约变化说明和回退说明。
- [ ] 完整后端测试通过，已有跳过逐项说明；CI 实际覆盖 `tests` 与 `regression_tests`。
- [ ] 分层 violations=0、cycles=0；新增端口有实际调用与行为测试，未通过调整白名单绕过限制。
- [ ] 内置任务列表不再调用旧全量接口；列表/概览不读取日志大字段，日志按需分块可查看，缓存和全局筛选不漏任务。
- [ ] 日志和树内存/落库容量有界，截断对用户可见；历史大记录不需要批量改写。
- [ ] 追剧执行器不再处理具体网盘的参数、返回字段与 UC 中转流程；全部注册网盘有契约验证结果。
- [ ] 执行、分享、引流入口不再直接取得具体执行器/适配器；事务、审计、通知的归属在用例中明确，网盘 IO 不占用数据库事务。
- [ ] Telegram、引流、追剧相关模块按职责拆分，命令、回调、令牌、日期、pending 和转存行为保持。
- [ ] 搜索失败可区分且不暴露异常原文；健康检查状态码符合 readiness/liveness 定义。
- [ ] 前端类型、golden、开发规范、架构总览、ADR 0002 及相关事务/端口说明同步，旧文档不再声称容器转发等于充分解耦。
- [ ] 提交性能对比和浏览器核验记录，区分自动测试、假客户端和真实网盘联调证据。
- [ ] 既有工作区修改及用户数据保留；未改动依赖、秘密配置或单进程部署形态。

后续执行从 **批次 A** 开始，逐包验收后推进；不能一次性把 A～D 混成一个大提交，也不能以“代码搬完、测试数量增加”代替以上行为验收。
