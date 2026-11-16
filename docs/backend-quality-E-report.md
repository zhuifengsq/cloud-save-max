# 工作包 E：分享预览职责与端口验收

日期：2026-11-16。已在本地实施，不提交、不推送。真实网盘未联调。

## 行为与边界

`application/share_preview.py` 负责读取依赖、结束只读数据库事务、调用读取端口和组装结果。`share_preview_plan.py` 接收 frozen 选项、只读目录项、目标目录名称与内存重命名能力，负责文件/目录筛选、重命名、已有文件判定及过滤后的冲突选择；不接 Session 或实际网盘适配器。端口 `domain/ports/share_preview.py` 定义只读 IO 返回值与重命名能力，容器为本调用链提供有返回类型的装配函数。

`infrastructure/drive/share_preview_reader.py` 在准备阶段只读取并复制所选账号配置，返回的 reader 不保存 Session 或 ORM 行。进入 AdapterFactory 构造、登录、分享和目标目录网络阶段之前，应用显式调用 `end_read_tx` 结束读事务。网盘差异集中由已有 gateway 的新增 preview 专用只读能力处理；既有 probe、transfer 不变。特别保留预览读取完整目录，不能借用 probe 的夸克 `first_page=True` 语义。

保留现有 SharePreviewIn/Out、Web路由及Telegram动作入口。单文件和目录条目、原始/重命名字段、目录过滤、关键字/文件/日期/大小过滤、已有文件和重命名冲突口径保留。根目标 `/` 仍按旧 `rstrip('/')` 规则变为空路径，不启动目标目录比较，已单独测试。去掉原预览中从未参与结果的 TMDB 设置读取/创建，不增加可选 metadata warnings 或新响应字段。

无效URL、账号缺失、分享token/目录失败沿用现有 ApiError 机器码；目标账号不可用明确返回 `DRIVE_ACCOUNT_UNAVAILABLE`，目标目录读取失败返回 `TASK_SHARE_DETAIL_FAILED`。不再将目标初始化异常/读取异常当成无已有文件，不回显第三方原始异常或响应 message。有效空目录仍返回空列表；结构损坏的目录响应明确失败。账号与分享类型不匹配明确拒绝。前端沿用通用错误message消费，无新增DTO字段或新错误码。

目录/文件日期解析异常改为窄异常及固定安全诊断，不记录文件名或原始异常。每次构造重命名器提供独立 magic_variable 字典，避免默认类变量跨请求污染任务名。

## 真实命令与证据

全部从 backend 目录使用 `.venv/Scripts/python.exe`，先设置 `XXM_TESTING=1 DATABASE_URL=sqlite:// RUN_MIGRATIONS=0 SCHEDULER_ENABLED=0`。没有读取真实数据库、secrets.json 或凭据。

1. 旧行为基线：`python -m pytest regression_tests/test_share_preview_boundaries.py -q --basetemp .tmp/quality-e-baseline` → **5 passed in 2.83s**。覆盖文件/目录、可选作者缺失、重命名、过滤和失效分享。
2. 增加事务与异常边界测试后：相同命令用 `.tmp/quality-e-red` → **2 failed, 5 passed in 3.85s**。真实临时 SQLite 读事务在 get_detail 阶段仍存在；上游 RuntimeError 原样泄漏，证明旧实现缺少所需边界。
3. 最终局部：`python -m pytest regression_tests/test_share_preview_boundaries.py tests/test_baidu_share_preview.py regression_tests/test_drive_share_contract.py regression_tests/test_transfer_contract.py -q --basetemp .tmp/quality-e-final2` → **63 passed in 5.52s**。覆盖实际SQLite与假网盘IO、目标已有文件、目标凭据与读取失败、无效URL/未知账号、纯计算只读输入及过滤后冲突选择、Web/Telegram实际动作结果一致。纯计划测试不启动数据库或网络。
4. `.tmp/quality-static-venv/Scripts/python.exe -m mypy --strict app/domain/ports` → **Success: no issues found in 11 source files**；`python -m ruff check app` → **All checks passed!**。

旧百度分享测试由 patch 已移除的应用具体账号管理器改为 patch 新装配出口/基础设施工厂，保留原 `TASK_SHARE_DETAIL_FAILED` 行为断言，没有放宽失败预期。最终完整测试、分层基线逐项登记、应用导入与集成验收由主任务统一执行，不以本包63项替代全量。

## 提交归属、回滚和限制

主代理集成补验：新增调用方待写ORM记录用例，修前没有拒绝且会被读事务结束提交；现入口在读取前检查new/dirty/deleted，违反只读会话约定明确拒绝。新增上游status=500且list为空用例，修前伪装成功，现统一网关识别错误状态并返回既有TASK_SHARE_DETAIL_FAILED。两项均有修前失败证据，最终全量1014 passed、2 skipped；百度旧测试使用真实SQLite Session验证原错误断言，未弱化护栏。最终记录见总报告。

本包不新增数据库模型、迁移、后台线程或网盘写入；无新增业务提交点。去掉无用TMDB设置创建后，本动作不再有自己的写阶段；进入IO前只使用已有只读事务结束原语。既有 `end_read_tx` 会提交当前事务，调用方仍应遵循“进入预览前没有待提交业务写入”的只读边界约定。

新增容器延迟导入 `app.infrastructure.drive.share_preview_reader.prepare_share_preview_reader` 为账号快照/网盘reader装配；旧 share_preview 函数内 TMDB、magic_regex、drama_parse_size 导入消失，日期导入迁入纯计划模块，主任务应逐项复核批准清单。不得自动提高违规或循环预算。

回滚时将应用编排、纯计划、domain端口、reader、gateway的两项新增方法、容器本调用链工厂、对应测试一起恢复，保留其他包在 container/gateway 的修改。未验证第三方真实账号、网络分页行为、过期凭据刷新及第三方服务可用性；假IO通过不等于真实网盘联调通过。
