# 执行与预览职责拆分验收

2026-11-16。对应用户复审第7项：不复制整份E包样板，不按函数行数作为验收条件，不改变转存或通知业务口径。

## TaskExecutor.run_task

分开任务数据准备、适配器执行、追剧进度更新、异常结果组装与执行记录收尾。入口保留账号准入与恢复分支，恢复只在既有share_parse/get_stoken/fetch_share_items失败且深度限制内调用；没有把已完成外部写入放入数据库失败重试。persist_execution=False继续返回id=0且不新增记录，allow_once与已存进度保持既有更新规则。手动/定时策略及分享检测、通知口径由现役execute链路测试覆盖。

## preview_share_batch

分成缓存分类、冻结候选账号名与账号管理器、网络探测与fallback、独立短写入。保留URL去重/结果顺序、优先账号/default/active排序、正缓存仍重读/负缓存快返、账号fallback与随机间隔；临时数据库故障不重放网盘IO。修复caller Session读取账号后仍持事务执行网络的路径，网络前明确结束只读事务；调用方有未提交ORM写入时拒绝，不借预览提交业务数据。实际SQLite+假IO测试覆盖fallback、缓存和网络时事务释放。

主代理另补Python异常信息保护：修前RuntimeError中的假Cookie进入响应；修后只给安全重试提示，未知IO故障也不凭异常字符串将链接判为永久失效。既有ApiError保留安全业务message。不会把这项异常保护概括为所有第三方日志/历史错误已经完成脱敏。

## TaskLeadInUseCase._preview

只读准备生成脱离ORM的账号/素材计划，网盘目录/自有或分享素材/查重各有独立读步骤，token payload纯计算，最后才执行必要的过期引流记录短同步。保留config_version/token过期/账号模式/素材名称与大小、已添加状态及确认行为。预览不调用lead_in_add或新增任务执行记录。新增真实Session pending写入拒绝测试与网络前无事务、预览不添加素材断言；pending测试修前未拒绝，修后通过。

## 验证与局限

中断前子任务已写入代码，主代理恢复后逐段审读并接续验证，没有以子任务完成状态代替证据。执行/批量/引流相关局部158 passed（8.38s），引流share/own-file/own-dir真实应用验收95 passed（4.36s）。最后完整测试 `python -m pytest tests regression_tests -q --basetemp .tmp/security-review-final-refactors`：1071 passed、2 skipped、1既有warning（62.93s）。Ruff、16文件mypy strict、应用导入及分层门禁通过。门禁305模块/943内部绝对导入/111延迟语句/0违规/0循环。

AST当前三个入口长度分别95、31、28行；此前254、252、238。大于等于80行的函数仍有53个（此前56），这是现状描述，不是以删行数代替职责验收。share_preview_plan另拆纯规则阶段，编排15行；它不启动数据库或实际网盘适配器。

不新增数据库模型或提交点：批量_short_tx仍归原模块，执行结果flush不自行新增commit，引流预览必要短提交仍原用例所有权。没有新deferred边，因此分层预算不随拆分提高。全部使用假网盘与独立测试数据库；真实服务与nginx/Tunnel联调未完成。未提交或推送，文件仍正常显示在Git提交列表。
