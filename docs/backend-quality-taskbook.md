# 后端质量改进任务书（复核后版本）

> 执行方式：按工作包逐项实施、逐项验证；实施时使用 `superpowers:executing-plans`，不自动提交或推送。

日期：2026-11-16

状态：待实施。本次仅编写任务书，不代表已授权全部工作包立即执行。

目标：优先补齐认证与初始化保护，使工程门禁真正生效，再渐进改善类型和业务职责；不改变现有转存业务口径。

架构：沿用 FastAPI、应用用例、领域端口、SQLAlchemy 仓储和 SQLite；装配入口保留，业务规则不搬入容器。

技术栈：Python 3.13、FastAPI、SQLAlchemy、SQLite、Alembic、pytest；ruff/mypy 仅作为拟议的开发工具。

依据：[开发规范](development-guide.md)、[架构总览](architecture/README.md)、[原始评审](backend-code-review.md)、[单进程决策](architecture/adr/0003-single-process-deployment-assumption.md)。原始评审是问题线索，不是本任务书的上位指令。

## 1. 核实结果与起点

2026-11-16 本地复核：完整后端测试 **857 passed、2 skipped**；测试使用独立 `--basetemp`，没有重现原报告中的 35 个临时目录权限错误。分层扫描历史基线为 294 模块、116 条延迟导入、0 违规、0 循环。实施开始时重新运行并记录实际值，不将固定测试数量当成完成条件。

| 事项 | 核实结果与本轮决策 |
| --- | --- |
| 长函数 | AST 确认 56 个函数不少于 80 行，`preview_share` 为 374 行；按职责和可验证行为拆，不以行数达标作为验收 |
| 容器类型 | 共 56 个顶层函数，其中 22 个明确返回 `ModuleType`；类型约束不足成立，测试和覆盖率工具并未因此失效 |
| CI 测试 | `.github/workflows/docker-publish.yml` 已在 main 推送和 PR 时运行两组后端测试；不重复建设一套测试工作流 |
| 分层预算 | `.layering-baseline.json` 的延迟导入和循环预算未参与失败判断，需要补齐 |
| 单进程 | 开发规范 §5.1 和 ADR 0003 已明确声明；保留约束，不将分布式改造列为紧急修复 |
| 通知实现 | `legacy_notify.send_async()` 最终复用 `send()`；`legacy_notify.py` 是当前发送实现，禁止直接删除 |
| 执行兼容 | 当前仓库 HTTP、SSE、定时、Telegram 和预览入口均走 `execute()`；`run()` 仅由 `test_run_task_use_case.py` 调用，`_legacy_execute()` 由 `run()` 间接调用。定性为“仅测试使用的遗留兼容入口”，不是两套现役生产实现；清理以调用证据和行为覆盖为依据，不以行数为依据 |
| JWT 时间 | `create_access_token()` 将时间转换为 Unix 时间戳，不能认定存在固定八小时偏移；历史数据库时间混用另行治理 |
| 健康检查 | 只有缺少 `BUILD_TAG` / `BUILD_SHA` 时才回退执行 git；Docker 构建已有环境变量，修复聚焦回退路径 |
| 凭据和登录 | 网盘凭据确实明文落库，应用侧登录失败限流缺失；分别按威胁模型和接口行为整改 |

## 2. 全局约束

1. 支持形态保持 **1 个 uvicorn 进程、1 个容器副本**；不增加 worker、Redis、Celery 或分布式锁。
2. 遵守 `development-guide.md` 的分层、事务、准入、通知和错误契约约束；路由不得继续自行组织账号、网盘和事务流程。
3. 不修改真实数据库，不读取或输出 `secrets.json` 内容、网盘 Cookie、JWT、刷新令牌等秘密值。测试使用假凭据和独立临时 SQLite。
4. 外部写入不因数据库失败、超时或待确认而自动重放；已保存文件不得为测试而删除或重转。
5. 保持手动执行不受运行星期限制、预览不落执行记录、任务确认状态和各触发方通知口径。
6. 不直接删除通知发送实现，不批量改写旧时间，不对线上凭据做无恢复演练的加密迁移。
7. 运行时依赖保持不变。开发工具依赖变更仅限工作包 D，并在执行前核对项目依赖约束；工作包 H 不包含安装加密库。
8. 错误码、响应模型或 SSE 变化时同步 schema、前端消费及 golden；不通过放宽断言、忽略整目录或提高预算掩盖失败。
9. 每包可独立验收和回滚。未经用户要求不 commit、不 push；不覆盖用户已有修改。

## 3. 工作包与执行顺序

| 顺序 | 工作包 | 交付性质 | 依赖 |
| --- | --- | --- | --- |
| 1 | A：登录失败防护 | 代码、测试、部署说明 | 无 |
| 2 | B：首次初始化原子保护 | 代码、真实 SQLite 并发测试 | 无 |
| 3 | C：门禁与低风险配置清理 | 检查脚本、测试、文档 | 无 |
| 4 | D：渐进静态检查 | 开发工具配置、现有 CI 接线 | 依赖边界确认 |
| 5 | E：分享预览职责与端口收口 | 保持行为的局部重构 | D；先建立行为基线 |
| 6 | F：遗留兼容入口清理与通知组织整理 | F1、F2 分别验收 | C；执行前复核调用清单 |
| 7 | G：时间语义治理 | 盘点、设计和新增代码护栏 | 无；历史迁移单独批准 |
| 8 | H：凭据保护方案 | 威胁模型与恢复验证方案 | 无；加密迁移单独批准 |

A～D 是首轮实施范围。E、F 分别交付，不绑成一次全仓重构。G、H 首轮只交付方案，不以“写完方案”宣称已完成 UTC 或加密改造。不承诺缺乏实测依据的“半天全部完成”。

## 4. A：登录失败防护

### 位置与职责

- 新建 `backend/app/application/login_throttle.py`：进程内、容量有界的失败计数与认证准入，支持注入单调时钟。
- 修改 `backend/app/application/auth.py::authenticate_user`、`backend/app/api/routes/auth.py::login`：连接检查、失败记录、成功复位与失败审计；不更改成功响应形状。
- 修改 `backend/app/core/settings.py`：配置阈值和容量，使用正数边界校验。
- 新建 `backend/regression_tests/test_login_throttle.py`、`test_login_route_security.py`。

### 行为约定

- 默认在 10 分钟窗口内，同一 IP + 用户名连续失败 5 次后，后续登录拒绝 15 分钟；同一 IP 跨用户名累计失败 30 次也进入相同冷却期。
- 返回 HTTP 429，机器码 `AUTH_LOGIN_THROTTLED`，附 `Retry-After`；账号不存在、停用、密码错误继续统一返回 401，不借限流结果泄露账号是否存在。
- 成功登录清除该组合的连续失败计数，不清除 IP 级其他失败累计；同组合的并发检查与在途认证管理必须原子，不能让并发请求绕过阈值，也不能持全局锁执行 bcrypt。
- 两类记录各自默认最多 10000 条。清理到期记录；不能淘汰仍在冷却期的记录来允许绕过。容量耗尽且无可回收记录时拒绝新组合并记录有界诊断，不无限扩容。
- 时长计算用 `time.monotonic()`，不依赖宿主时区。成功、异常、数据库失败都释放在途状态。
- IP 使用可信链处理后的 `request.client.host`。检查现有 nginx 与 uvicorn 的代理信任配置；不能直接相信任意客户端传入的 `X-Forwarded-For`，也不能把全部用户错误归成代理服务器 IP。
- 失败审计不记录密码、令牌、Cookie，不回显底层异常。失败计数不能随着请求事务回滚被清零；审计故障必须有日志，不能伪装登录成功。
- 文档明确本保护是进程内的，不声称重启保持封禁或支持多副本。

### 实施与验收

- [ ] 先写失败测试：第 6 次同组合登录为 429；换用户名仍受 IP 总阈值约束；时钟推进后恢复；容量满时仍保持已封禁组合。
- [ ] 用真实认证路由验证 401/429/成功登录、并发阈值、代理 IP、密码不进入响应或审计。
- [ ] 再实现最小防护；不新增独立限流框架，不复用转存执行槽。
- [ ] 更新错误契约和部署说明；原登录、刷新、退出测试仍通过。

## 5. B：首次初始化原子保护

### 位置与方案

- 修改 `backend/app/api/routes/setup.py`、`backend/app/application/setup.py`；新建 `backend/app/application/use_cases/initialize_admin.py` 收口事务。
- 修改 `backend/app/infrastructure/persistence/repositories/system_settings.py`：提供专用初始化占位写入；复用 `system_settings.key` 的数据库唯一约束，键固定为 `system.initial_admin`。
- 新建 `backend/regression_tests/test_setup_concurrency.py`。
- 占位、创建用户、管理员角色关联、刷新令牌和审计必须在同一事务中完成；占位写入失败不得继续创建用户。不能用现有“先查再 set_value”的非原子 upsert 代替唯一插入。
- 保留已有用户检查作为快速拒绝；取得占位后再次检查是否已有用户。历史安装即使没有占位，也因已有用户而拒绝重新初始化。
- 初始化占位不能被通用设置写入、删除或重置接口单独清除。用户名/邮箱唯一约束只保护单个字段，不能代替全局初始化保护。
- 已初始化或已被另一请求完成初始化：HTTP 409、`SETUP_ALREADY_INITIALIZED`。只映射已确认的占位冲突，不将所有 `IntegrityError` 都包装成该错误。
- 数据库暂时不可用沿用 503 语义；创建失败整体回滚，下一次合法请求可以重新初始化。
- 不使用 SQLite 不支持的行级锁方案；不在 `is_initialized()` 查询外面单独加一个只锁当前线程的局部锁冒充数据库互斥。

### 实施与验收

- [ ] 先使用临时 SQLite 文件、两条独立连接和同步屏障，复现两个不同用户名请求都通过旧检查的场景。
- [ ] 验证并发请求中只有一个成功、另一个明确 409；最终只有一个初始管理员和一次成功审计。
- [ ] 注入用户创建、角色关联、令牌写入和审计失败，验证占位和相关记录一并回滚。
- [ ] 验证历史已初始化库、失败后重试、受保护设置键；不通过删掉并发断言或使用单连接假对象使测试通过。
- [ ] 将新用例的提交所有权登记到 `regression_tests/test_commit_ownership.py`；路由只处理输入和响应。

## 6. C：工程门禁与低风险清理

### C1. 分层预算真正生效

位置：`scripts/check_layering.py`、`backend/.layering-baseline.json`、新建 `backend/regression_tests/test_layering_budget.py`、既有 `.github/workflows/layering-gate.yml`。

- `violations`、`deferred_imports`、`cycles` 分别比较预算；任一超标退出非零，不能互相抵扣。解析失败也必须非零。
- 将当前 116 条延迟导入与原 90 条逐项对照现有改造、装配例外，留下来源清单；合法变更经记录后可建立当前基线，不能默默把 90 改成 116。
- 新增无批准的延迟依赖必须失败；将 import 搬到函数内或搬入容器不得自动视为职责收口。
- 基线不能放行新增分层违规或循环；保持 0 违规、0 循环。更新基线与修改业务实现不能用同一个“自动修复”步骤混在一起。
- [ ] 建小型临时 Python 包，实际运行检查脚本，分别制造延迟导入超标、循环和非法依赖；验证退出码，不能只断言 JSON 字段存在。
- [ ] 验证基线文件无效、模块解析失败时明确失败；原项目门禁仍通过。

### C2. 配置、健康检查与文档纠偏

位置：`backend/app/core/settings.py`、`backend/app/api/routes/health.py`、`backend/regression_tests/test_health_status.py`、`docs/development-guide.md`。

- [ ] 删除重复定义的三项灰度配置，保持字段名、默认值、环境变量行为不变；本步骤仅去重。`use_unified_run_use_case` 作为失效兼容开关的整体删除归 F1，不能连带删除仍在使用的通知或锁开关。
- [ ] 有构建环境变量时健康检查不启动 git；缺少时首次读取后缓存，包括 git 不可用的结果。git 回退必须保留超时；数据库探针每次仍真实执行，不能缓存数据库健康状态。
- [ ] 测试环境变量优先、重复请求不重复启动 git、无 git 时正常降级、数据库失败为 503、liveness 不受数据库失败影响。
- [ ] 在任务准入模块入口补充到 ADR 0003 的指引，不另造“此前未声明单进程”的文档。
- [ ] 保留现有 PR/main 完整测试、分层和迁移工作流；Windows 本地沿用独立 `--basetemp`，不递归清理其他用户或其他测试进程的临时目录。

## 7. D：渐进静态检查

位置：新建 `backend/pyproject.toml`，修改 `backend/requirements-dev.txt` 和现有 `.github/workflows/docker-publish.yml` 的 backend job；不更改运行时 requirements。

- ruff 首轮只对 `app/` 开启 `F821`、`F822`、`F823`，先阻止未定义名称等明确错误；不要一次全仓格式化或启用全部 E/W/I 规则。
- mypy 首轮以 strict 模式检查 `app/domain/ports/`，保留未类型化 JSON 边界的显式类型；通过后逐个端口的实际调用链扩大。不用全局 `ignore_errors` 或无理由 `type: ignore` 让门禁变绿。
- 执行时选择支持 Python 3.13 的可用工具版本，精确固定在开发依赖；锁定版本和实际检查输出写入验收记录，不能只记录本机 PATH 中的工具版本。
- 保留完整 pytest 和迁移 job；新增检查接入当前 backend job，避免再复制一套触发范围不同的测试工作流。
- [ ] 在隔离工具环境验证配置实际生效，再修当前范围内的明确错误。
- [ ] 执行 `python -m ruff check app`、`python -m mypy --strict app/domain/ports`；不使用自动修复全仓 import 顺序。
- [ ] 本地通过和远端 CI 通过分开记录；没有实际远端运行结果时不得标记已通过。

## 8. E：优先收口分享预览

先做 `preview_share` 一条调用链，不同时拆全部长函数。

位置：`backend/app/application/share_preview.py`、`application/dto/task_browse.py`、`application/container.py`；新建 `application/share_preview_plan.py`、`domain/ports/share_preview.py`、`infrastructure/drive/share_preview_reader.py`、`regression_tests/test_share_preview_boundaries.py`。

- `share_preview_plan.py` 放文件过滤、重命名计划、目标目录差异等纯计算；输入显式只读数据，不接 Session 或实际网盘适配器。
- `share_preview_reader.py` 承担账号与网盘 IO 差异，遵守现有统一网关；应用代码只依赖端口形状。
- 仓储读取、外部 IO、纯计算和必要的短写入分开；进入网络阶段前显式结束只读数据库事务。
- 容器保留为装配点，逐项给这条调用链的依赖补类型；不禁止所有新代码使用容器，也不为每个普通工具函数增加一个 Protocol。
- 分享 URL 无效、账号不存在、凭据失败和网盘读取失败不能静默变成“无文件”。明确失败按既有 ApiError 分类；可选元数据缺失若增加 warnings，必须同步 DTO/schema/前端/golden，不直接塞原始异常。
- [ ] 先建立单文件/目录分享、已有文件跳过、文件过滤、重命名、失效分享、可选资料缺失的现状测试。
- [ ] 纯计划测试不启动数据库或网络；端口集成测试用真实临时 SQLite、假网盘 IO，验证 IO 时没有持有数据库事务。
- [ ] 验证 Web 与 Telegram 复用同一应用动作，结果和既有业务口径一致。
- [ ] 验收以依赖和行为测试为准，不要求编排恰好 20 行或所有函数少于 60 行；其他长函数另立后续批次。

## 9. F：遗留兼容入口清理与通知组织

本包是维护性整改，优先级低于 A、B 的认证和初始化保护。F1、F2 分别交付，不将遗留执行入口与当前通知发送实现混为一谈。

### F1. 清理仅测试调用的遗留执行入口（优先删除）

位置：`backend/app/application/use_cases/run_task.py`、`backend/app/core/settings.py`、`backend/regression_tests/test_run_task_use_case.py` 和相关文档。

复核事实：当前仓库的生产入口使用 `.execute()`；只有回归测试调用 `.run()`，后者在开关为 False 时才调用 `_legacy_execute()`。`use_unified_run_use_case` 不控制现役 `.execute()` 的执行路径，不能描述成生产回滚开关。“无 owner”属于维护责任判断，不作为调用扫描可以证明的技术事实。

- [ ] 实施前再搜索生产入口、脚本、动态装配和测试，记录调用清单；当前仓库无生产调用不等于可以证明所有仓库外集成都不存在。若发现明确的外部兼容承诺，先记录使用方和退役安排，不擅自破坏该承诺。
- [ ] 在现有调用事实不变、没有外部兼容承诺的前提下，默认删除 `run()` 和 `_legacy_execute()`，删除 `use_unified_run_use_case` 的全部配置定义、专属开关测试和回退注释；同时清理确认仅服务该入口的 `executor_cls` 注入缝与无用导入，不保留形同虚设的开关。
- [ ] 不删除整个 `test_run_task_use_case.py`。保留 `RunPolicy` 真值表、输入归一化、非法触发方、不可变性和现役依赖装配测试；仅删除针对被删兼容代码本身的测试。
- [ ] 将仍有价值的 HTTP、SSE、scheduler、Telegram、preview 行为验证迁移到 `.execute()` 的现役测试：手动执行不受运行星期限制、预览不持久化执行记录、调度按策略执行、通知口径保持不变。验证实际业务语义，不为复刻失效入口而强行向现役接口添加旧参数。
- [ ] 不把旧执行代码简单移动到测试目录后继续测试它自身。只有能作为独立参考模型、用于核对现役实现且具有明确维护用途时，才保留测试侧参考逻辑；单纯“保留历史”不算完成整改。
- [ ] 更新模块说明、配置说明和开发规范的回滚段：删除该开关可恢复生产旧流程的错误暗示；其他仍有效的开关保持原语义，已删除的兼容入口通过回滚对应提交恢复。
- [ ] 验收：生产入口继续统一走 `.execute()`，失效配置与专属测试不再残留，现役触发策略、分阶段事务及执行结果测试通过；不以测试总数减少认定覆盖退化，也不以删除行数认定完成。

### F2. 当前通知发送实现的组织整理（独立于 F1）

位置：`application/notifications/legacy_notify.py`、`notifications/sender.py`、`notifications/queue.py`、`infrastructure/execution/task_run_notifier.py` 和调用方。

- [ ] 通知按“配置过滤、排队、渠道发送”拆职责，迁移发送实现和所有引用后才删除旧文件；仅改名也应保留测试证据。
- [ ] 验证所有在用渠道的参数传递、队列满、发送异常、同步测试通知、运行时异步通知和发送次数；不得让一次通知既直发又入队。
- [ ] 未完成渠道迁移时保留现有发送实现并注明未完成；不把删文件作为独立验收目标。

## 10. G：时间语义治理（首轮只做盘点和方案）

交付：新建 `docs/backend-time-policy.md`。检查 `core/security.py`、`application/auth.py`、`application/drama_schedule.py`、相关模型、迁移和前端时间展示。

- [ ] 按字段列出来源：Unix 时间戳、Python 本地 naive、UTC aware、SQLite `CURRENT_TIMESTAMP`；注明存储、比较、输出和展示各用什么时区。
- [ ] 明确绝对时刻使用 UTC，运行星期、播出日期和用户日历使用明确的业务时区；不能将业务日期直接按 UTC 日界切换。
- [ ] 用固定时钟验证 access token 的 `exp - iat` 等于配置时长，刷新令牌过期边界在时区变化下如何处理；不能把 `datetime.now()` 的出现次数等同于已证实的偏移 bug。
- [ ] 提供历史数据迁移策略：区分已知来源和无法确认来源；不推断所有旧 naive 时间都是上海本地时间；方案包含备份、幂等、版本标识和恢复演练。
- [ ] 选择先治理认证时间还是先治理执行历史，明确涉及字段后再批准迁移实现；本任务书不授权批量 SQL 更新历史值。

## 11. H：凭据保护（首轮只做设计）

交付：新建 `docs/backend-credential-protection.md`。检查账号创建/编辑、二维码登录、适配器刷新凭据、缓存、备份、导入导出和日志路径。

- [ ] 列出敏感字段及所有读写入口，区分 Cookie、刷新凭据、公开配置和运行状态；不要无差别加密全部 JSON 后破坏查询。
- [ ] 明确威胁模型：单独数据库副本泄漏、整个 data 目录或备份泄漏、运行中主机失陷分别能保护什么。
- [ ] 选择密钥存放与备份边界。数据库和解密密钥放同一份备份不能声称仍具保密性；仅把 Cookie 搬到另一个明文文件也不算字段加密。
- [ ] 设计明文/密文版本识别、双读单写、密钥丢失与轮换、损坏密文拒绝、迁移幂等、缓存失效和恢复路径；旧程序能否读取迁移后数据库必须明确。
- [ ] 用纯假凭据制定迁移和恢复测试；正式运行库、真实密钥不进入方案样例、日志、测试或 Git。
- [ ] 加密库属于新的运行时依赖，方案确认后单独批准实施；无恢复演练不得开始线上加密迁移。

## 12. 验证命令与交付记录

每包先写能暴露实际问题的失败测试，确认失败原因，再实现和验证；不为文档文字、文件名或私有结构编写镜像断言。全量验证在工作包完成后运行，失败时先定位，不用无限重复测试代替调查。

从仓库根目录的独立 PowerShell 终端运行：

```powershell
$backendPython = (Resolve-Path .\backend\.venv\Scripts\python.exe).Path
$env:XXM_TESTING = '1'
$env:DATABASE_URL = 'sqlite://'
$env:RUN_MIGRATIONS = '0'
$env:SCHEDULER_ENABLED = '0'
$qualityTemp = '.tmp/quality-' + [guid]::NewGuid().ToString('N')
Push-Location backend
try {
    & $backendPython -m pytest tests regression_tests -q --basetemp $qualityTemp
    if ($LASTEXITCODE -ne 0) { throw '后端测试失败' }
    & $backendPython -c "import app.main; print('import ok')"
    if ($LASTEXITCODE -ne 0) { throw '应用导入失败' }
} finally {
    Pop-Location
}
& $backendPython scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json
if ($LASTEXITCODE -ne 0) { throw '分层检查失败' }
git diff --check
if ($LASTEXITCODE -ne 0) { throw '差异格式检查失败' }
```

D 实施后补跑该包的 ruff/mypy 命令。涉及模型或迁移时，按开发规范 §0 在另一个全新隔离库执行空库升级、往返迁移、schema drift 和迁移预检。涉及前端契约或 UI 时，按 §10.4 运行现有前端回归与 build；不在生产环境做验收。

每包交付记录必须包含：

- [ ] 实际改变的行为、接口、默认配置和兼容范围。
- [ ] 新增失败用例与修复后结果、完整测试、门禁、导入验证的真实命令和输出。
- [ ] 提交归属、回滚方式、尚未验证的真实服务场景；不得以假客户端通过替代真实网盘联调。
- [ ] 对应开发规范、错误契约、配置说明和 ADR 的必要更新。
- [ ] 本地通过与远端 CI 分开记录；方案已完成与功能已实现分开记录。

## 13. 明确不做

- 不重建已经存在的 PR 测试流程，不声称修复不存在的固定 JWT 八小时偏移。
- 不删除当前通知发送能力，不按文件名包含 legacy 判断可删。
- 不以强制函数行数、Protocol 数量、Any 总量或目录数量作为质量完成指标。
- 不统一抽象十家网盘的全部协议，不改网盘写入重试、不做多进程扩容。
- 不实施任务队列/日历性能改造；该需求应在实际任务规模与耗时测量后单独立项。
- 不顺带修改 QQ 频道文案格式、海报、追剧进度和其他产品功能。
- 不自动执行本任务书、不提交或 push；后续按用户明确选择的工作包推进。
