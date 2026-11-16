# 安全与资源边界复审整改

2026-11-16。用户批准对复审中确认的问题继续修改。本批不提交、不推送，不读取真实数据库、secrets、Cookie或密钥，不执行正式历史库迁移。

后续五项缺陷修复、账号版本锁迁移与历史误判清理说明见 [第二轮复审修复](backend-review-followup.md)。下文保留第一批验收记录，最终验证结果以后续报告为准。

## 登录准入与审计

限流默认保留组合5次/IP30次、600秒窗口/900秒冷却、各10000条容量。全表清理改为每30秒最多一次，当前组合/IP每次独立过期检查；被封禁请求在清理间隔内不再遍历整表。清理在全局锁内，因此定期仍有一次O(N)成本，不能声称全路径都严格O(1)。满容量且尚未到下次清理的未知键保守拒绝，不能淘汰仍有效的失败或封禁记录。

同组合请求等待前一个请求完成，默认等待最多2秒，全局最多16个等待者（给Web线程池中的其他请求留余量）；等待会释放全局锁，不持锁执行bcrypt。完成/异常唤醒后重新检查冷却与IP准入额度。超时或等待容量满仍返回429和Retry-After，防止无限等待耗尽线程。新增配置：LOGIN_THROTTLE_SWEEP_INTERVAL_SECONDS=30、LOGIN_THROTTLE_WAIT_TIMEOUT_SECONDS=2、LOGIN_THROTTLE_MAX_WAITERS=16；前两者分别要求正数和非负数，后者要求正整数。

失败登录不再在请求Session写审计/commit。独立单消费者每5秒批量写入一次，按IP聚合failure_count，只存IP/次数，不接收用户名/密码/令牌/User-Agent。待写源最多1000个，计数饱和有上限；队满丢弃新源聚合并有界警告；批写失败安全日志且丢弃该批，不无限重试。新失败仍可能随后受理为新聚合。关闭给后台线程2秒完成机会，超时记录可能丢失。进程崩溃/重启会丢未落库聚合，这是明确的best-effort语义，不满足逐事件持久审计的部署需要另行选择持久审计设施。

计数和在途准入仍不依赖审计事务。登录成功的审计与令牌提交保持原事务；失败审计独立于请求回滚。新增独立会话已登记归属。

失败证据：100次拒绝前旧实现调用_sweep共202次，修后仅初次两表2次；同组合并发原实现直接拒绝，修后等待释放再准入。组合测试30 passed（18.27s）。真实临时SQLite写入验证100次失败在请求侧0 commit，flush后1个独立提交、2条IP聚合（90和10）；审计局部4 passed（1.01s）。不是用异步函数名假装不写数据库。

在各10000条组合/IP记录已进入冷却的条件下，本机连续拒绝10000请求实测0.0249秒（只测准入层，不代表HTTP吞吐压测）。同进程关闭再启动会重建审计实例。消费者线程启动失败不逃逸为登录500，不保留失效线程引用；最多每5秒尝试启动，有界诊断不输出异常原文，停止时没有消费者则明确丢弃并警告，避免在主线程无限等待数据库。新增失败注入和重启测试后审计局部6 passed（1.20s）。

## 其他边界

保留键目前仅内部写入保护且HTTP设置键固定，因此未把内部ValueError盲目改成用户4xx；SQLite是现有支持范围，保留SQLite专用原子插入，不为未承诺的数据库抽象。

凭据API、加密持久化/假数据恢复、可信代理、静态范围扩展见下文。未启用独立密钥的兼容模式不等于已获得落盘加密，正式数据迁移须显式执行并先确认恢复演练。

## 凭据 API、加密与部署

API输出只含公开配置及credential_present/credential_fields，不返回秘密原文、profile.raw或原始last_error。编辑空值保留旧凭据，clear_credentials显式清除，掩码字符串拒绝保存；兼容cookie输入分支也走同样合并规则，KV部分更新不覆盖其他secret。schema/前端抽屉/TS/golden已同步。见 [API报告](backend-credential-api-report.md)。

已实现AES-GCM独立keyring、AAD绑定数据库命名空间/凭据UUID/drive/version、版本双读、启用后的新写封装、显式离线迁移/轮换和假数据恢复。默认无keyring仍保持v0，生产应配置CREDENTIAL_KEYRING_FILE及CREDENTIAL_ENCRYPTION_REQUIRED=1；现有库不会自动改成密文。数据库新增3列，禁止对含密文库直接downgrade，需配套备份恢复。原设计中的独立凭据表、nonce历史唯一账本、逐行凭据revision CAS和旧程序能力握手未完整实现，不能称完整H设计或默认生产凭据保护已完成。具体恢复与限制见 [加密报告](backend-credential-encryption-report.md)。

复审修复了cookie输入绕过掩码/合并保护、saved_progress的SQL身份条件与新透明属性不兼容、legacy cookie-only配置迁移后丢失fallback语义三项问题。加密开启/关闭×并发账号更新真实SQLite验证通过。加密模式的ORM bulk UPDATE明确拒绝，避免绕过mapper；直接SQL仍属于受控运维边界。

二级代理通过TRUSTED_PROXY_CIDRS显式指定可信上游，TRUSTED_PROXY_HEADER只允许X-Forwarded-For或CF-Connecting-IP；默认不信任来源头，拒绝全网CIDR/通配符/配置注入。容器启动生成配置并nginx -t；本机验证配置生成及真实TCP uvicorn信任处理，未实际运行nginx/Docker/Tunnel。部署者仍需填真实可信网段，不能用Docker通用网段猜测信任。

Ruff扩大到F6/F7/F82；mypy strict从11文件扩到16文件，包含纯限流/聚合审计/预览计划及payload/容器入口，CI读取配置的files而不再固定只检查ports。纯计划新增TypedDict/明确只读object边界，拆规则阶段并做100组固定种子行为比对。约1030行仍远非全仓类型覆盖，不以减少Any文本数冒充质量达标。见 [代理与静态报告](backend-proxy-static-report.md)。

## 集成验证（安全功能稳定阶段）

环境XXM_TESTING=1 DATABASE_URL=sqlite:// RUN_MIGRATIONS=0 SCHEDULER_ENABLED=0，并移除测试命令进程内CREDENTIAL_KEYRING_FILE/REQUIRED，避免读取真实密钥。后端 `python -m pytest tests regression_tests -q --basetemp .tmp/security-review-full2`：1061 passed、2 skipped、1既有warning（62.65s）。首轮11个saved_progress失败已定位为属性SQL回归并修复，不通过放宽断言解决。

Ruff通过；mypy strict读取files配置16 files通过（本机隔离工具环境使用--python-executable指项目venv解析依赖）；应用导入import ok。新装配唯一新增延迟边为container→infrastructure.persistence.login_failure_audit，经独立清单核对后预算110→111，违规/循环仍0；此阶段305模块、942内部绝对导入。前端61项回归通过，Node24构建通过。唯一新隔离SQLite执行upgrade head/down1/up成功，schema drift26表0漂移，preflight pending0；没有用真实库做验收。后续三条长函数职责拆分的最终集成结果另补。

## 最终集成与第7项

三个长链已按职责拆分，详见 [执行与预览报告](backend-execution-preview-refactor-report.md)。批量预览新增实际事务边界、缓存与fallback用例；引流预览补待写数据不提交、网络前无事务且不执行添加操作；执行入口保留真实触发策略/记录/通知与失败恢复覆盖。root接续中断子任务完成审读与验收。

完整命令 `python -m pytest tests regression_tests -q --basetemp .tmp/security-review-final-refactors`：**1071 passed、2 skipped、1既有warning（62.93s）**。Ruff、mypy strict16 files、import ok、git diff --check均通过。分层305模块/943内部绝对导入/111延迟语句/0违规/0循环；拆分没有新增未批准延迟边。前端61/61与Node24的vue-tsc/Vite构建通过，有既有大bundle提示。新迁移在独立空库升级、降级再升级通过，drift0、pending0。

未执行远端CI、镜像运行或真实代理/Tunnel/网盘验证。未配置keyring且未显式转换的正式库仍可能明文；不能把本地加密实现与假数据恢复通过写成生产已加密。H完整方案中的独立凭据表、nonce历史账本、revision CAS、旧程序握手等仍有增强项未实现，后续需专门验收；本批修复不掩盖这些边界。没有自动提交/推送、隐藏Git改动或操作正式库。
