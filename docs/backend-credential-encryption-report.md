# 凭据加密实现及假数据演练

2026-11-16。未提交/推送，未读写真实data、secrets.json、真实密钥或网盘凭据。

新增可选AES-256-GCM凭据封装、独立keyring、数据库列、运行时透明解包与显式离线迁移命令。不是自动线上迁移，默认无keyring保留v0兼容；要强制新写加密须设置CREDENTIAL_ENCRYPTION_REQUIRED=1并提供CREDENTIAL_KEYRING_FILE。未配置加密时旧明文风险仍在，不能称默认已保护。

keyring JSON包含database_id、active_key_id、keys（key_id→base64编码32字节密钥）；密钥只能由部署者在data目录之外创建/挂载并单独备份，本次未创建真实运行密钥。加密记录保留credential_uuid/version/blob，物理cookie为空、config_json仅保留明确公开字段；未知字段按秘密处理。公开实例cookie/config_json保持旧运行形状，以覆盖账号CRUD、扫码确认、probe/sign-in及快照读取。集成复核确实发现saved_progress中的类级SQL条件，已改为真实存储身份快照并补回归；hybrid表达式只表示物理列，不提供SQL内解密。加密模式拒绝绕过mapper的ORM bulk UPDATE，原始SQL仍可绕过ORM保护，须限制为受控运维路径。

AAD绑定keyring中的database_id、稳定credential_uuid、drive_type和版本；改账号名不破坏解密。随机12字节nonce；当前实现没有数据库级nonce历史唯一账本，依赖系统CSPRNG，不声称已完成方案中唯一占位账本/专用凭据表/逐行revision CAS等全部增强项。密文交换、未知版本、缺key、坏格式和认证错误失败关闭，不回退明文；API输出错误统一安全文本，不包含底层路径、key或密文。

数据库结构通过20261116_drive_credentials新增3列，未改旧行数据。降级遇到密文明确拒绝，需恢复配套迁移前快照，避免丢失唯一密文。ORM property+mapper持久化边界将运行时秘密写为封装；load/refresh/expire按物理记录解包，rollback丢弃未提交pending。没有Session外长网络事务。

离线命令：设置独立keyring后，在维护停写状态运行 `python scripts/migrate_credentials.py --database <已升级的隔离或明确批准库> --backup <全新备份路径>`。必需显式路径，先SQLite backup和integrity_check，再在一个数据库事务中转换v0；重跑验证并跳过v1。--rotate用当前active_key_id重封装，旧key保留用于旧备份。不得对运行中的服务库直接执行；转换后重启单进程释放旧账号/adapter缓存。失败回滚但备份保留。该命令没有被用于真实运行库。

密码库已由python-jose[cryptography]间接引入，现作为直接依赖精确固定cryptography==50.0.2（本机实际版本）。[官方PyPI元数据](https://pypi.org/pypi/cryptography/50.0.2/json)声明Python3.13支持；[官方AEAD文档](https://cryptography.io/en/latest/hazmat/primitives/aead/)支持AESGCM与AAD、认证错误拒绝、随机nonce要求。未自行实现密码算法。

测试先红3项（缺schema/缺失加密及强制保护），实现后最终 `python -m pytest regression_tests/test_credential_encryption.py -q --basetemp .tmp/crypto-final`：6 passed in1.91s。覆盖真实SQLite存储无假秘密、透明读写、flush/expire/refresh/rollback、未知字段保护、公开配置、改名、错误key/缺key/坏密文/AAD交换拒绝、v0迁移幂等、A→B轮换、SQLite backup恢复与integrity_check。含初始化组合14 passed（早期4项crypto+10项setup）。Ruff通过。

新建唯一.tmp/crypto-schema-<guid>.db执行空库upgrade head、downgrade -1、upgrade head全部exit0；check_schema_drift输出db_tables26/model_tables26/drift0；migration_preflight --fail-if-pending输出current=head=20261116_drive_credentials、pending0。没有以默认真实数据库路径执行预检。

限制：专用凭据表、nonce账本、并发扫码/刷新凭据revision CAS及旧程序启动能力握手尚未实施；旧程序会看到清空的cookie和公开config，不能用来运行已迁移库。部署仍需真实密钥备份恢复演练和服务维护窗口；现有旧明文备份/WAL/镜像层并未自动清理。当前实现可选启用不等于已完成线上安全整改。

集成复审发现并修复两项兼容回归：saved_progress的条件UPDATE快照现改用真实存储列、credential UUID/version/blob，不以解密后的secret生成SQL条件；cookie/config_json改hybrid_property保留旧未加密SQL表达式兼容。开启加密或库中存在任何密文时，所有DriveAccount ORM bulk UPDATE明确拒绝，防止绕过mapper持久化保护（原始SQL仍属受控运维边界）。另外封装保存config_present标记，保留legacy config_json NULL/空字符串走cookie反序列化的语义，不将其误变成空配置。新增真实SQLite加密开关×并发账号更新CAS、legacy-cookie-only通过AdapterRegistry和账号快照读取、bulk绕过拒绝用例。修前saved_progress可复现TASK_CHANGED，修后组合40 passed（5.90s），Ruff通过；补充缺key bulk保护后再次定向验证。当前crypto测试13项，完整saved_progress27项。
