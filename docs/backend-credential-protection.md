# 网盘凭据保护方案与恢复验证设计

后续已实现可选AES-GCM持久化、API不回显及显式离线转换/恢复测试，见 [实现报告](backend-credential-encryption-report.md) 和 [API报告](backend-credential-api-report.md)。下文仍为最初完整设计；独立凭据表、nonce账本、凭据revision CAS等增强项尚未全部实现，不把实现的子集当成整份设计完成，也没有转换真实库。

日期：2026-11-16。工作包 H，仅源码盘点与设计。未读取运行数据库、secrets.json、Cookie或真实密钥；未安装加密库，未实施加密或线上迁移。下文的密钥ID、协议名与测试值均为设计/假数据。

## 字段分类与源码入口

`drive_accounts.cookie`为兼容登录串，`config_json`为结构化配置，同一凭据经 `AdapterRegistry.serialize_config` 往往同时存在两处，必须一起治理。`profile_json`是公开资料与第三方原始响应混合，`last_error`是错误文本，不能直接假定没有秘密。

| 网盘/字段（来自各adapter的default_config） | 分类与处置 |
| --- | --- |
| quark、uc、baidu、115：`cookie` | 会话秘密；加密存储，API不得默认回显 |
| aliyun、xunlei：`refresh_token` | 可续期长期秘密；access_token在adapter内存中也按秘密处理 |
| guangya：`access_token/refresh_token` | 秘密；`phone_number`为个人信息，`device_id`为设备绑定信息，同归受保护片段 |
| cloud139：`authorization/cookie` | 秘密；phone为个人信息；debug为非秘密运行开关 |
| cloud189：`password/ssoncookie` | 秘密；username为个人信息；protocol/name/debug保留公开配置（name仍按权限保护） |
| 123pan：`password/authorization/loginUuid` | 密码及鉴权/会话绑定值归受保护片段；username为个人信息；protocol/deviceType/osVersion/name/debug为普通配置。loginUuid未来若确认只是公开设备标识，可在版本化schema中调整，不能靠字段名猜安全 |
| `profile_json`昵称/头像/容量、`runtime_status/enabled/is_default/probe_fail_count/last_checked_at` | 普通资料/运行状态，不无差别加密整个JSON；profile.raw及错误必须白名单映射/脱敏，手机号等个人信息单独识别 |
| 应用 `users.hashed_password`、`refresh_tokens.token_hash` | 已有单向哈希验证用途，不能改为可逆加密；应用JWT签名密钥/refresh pepper与网盘字段加密密钥分开管理 |
| notification配置中的渠道token、webhook；Telegram/TMDB/search配置密钥 | 本方案盘点的相邻秘密边界，未来应复用密钥管理机制但分别建schema/权限，不伪称仅加密网盘字段就保护整个数据库 |

源码读写与暴露边界：

| 入口 | 当前行为及未来接线要求 |
| --- | --- |
| `application/drive_accounts.py::create_drive_account/update_drive_account` → `repositories/drive_accounts.py` | normalize后同时写cookie/config_json；统一经credential store合并普通配置与秘密，仓储不独立commit |
| 同模块 `serialize_drive_account/resolve_drive_account_config` → `schemas/drive_account.py::DriveAccountOut`，路由GET/POST/PATCH | 当前config完整返回给具备相应权限的请求方，字段加密不会阻止授权API取明文。未来返回普通配置+credential_present，编辑沿用未提交秘密；显式更换/清除操作分开，必须同步前端drawer、TS、schema/golden并评审兼容 |
| `api/routes/drive_accounts.py`扫码start/poll、SMS/captcha；`drive_account_auth_sessions.py` | 会话持有adapter、payload、t/ck/session_id；扫码确认update config，短信/captcha导出runtime_config后写回。保留已有容量TTL与释放，payload仅返回流程必需字段，不能将二维码兑换所得token塞进错误detail |
| `probe_drive_account/sign_in_drive_account`、批量profile refresh、scheduler与Telegram账号动作 | 初始化/签到后export_runtime_config可能轮换凭据；全部写回统一credential store，禁止漏掉旧cookie镜像；保留账号缓存失效 |
| `infrastructure/drive/account_manager.py`、`AdapterRegistry.parse_config_json/serialize_config`、`factory.py` | 从DB解包为纯账号快照，factory实例缓存含登录态；解密在受控边界，业务与adapter仍接原形状。快照、实例缓存和HTTP头中仍会有内存明文，不声称运行内存加密 |
| aliyun/xunlei/123pan/cloud189 adapter的 `_save_refresh_token`、凭据持久化方法、模块级config_saver回调 | 存在可调用回调及历史JSON文件saver路径；当前仓库仅发现set_config_saver定义，未发现生产装配调用，不能由此证明仓库外使用方不存在。实施前禁用/收口明文文件回退，并验证动态回调也经credential store，不能加密DB却从配置文件泄漏 |
| `infrastructure/persistence/backup.py`、`scripts/backup_db.py` | SQLite backup复制完整库，含现有明文cookie/config；未来密文备份仍需对应密钥及schema/版本清单。旧快照及WAL/SHM仍可留明文；迁移不等于历史备份已加密 |
| 导入/导出 | 本仓库路由未发现独立网盘账号批量导入/导出端点；factory.load_accounts是账号配置加载入口，adapter.export_runtime_config是运行时快照输出。整库备份/恢复、历史JSON配置saver也属于导入导出面，不能写成“无导入导出风险” |
| 日志/异常/审计/响应 | `probe_drive_account`当前last_error可写str(exc)，profile.raw保留原始资料；adapter异常日志及HTTP响应可能包含鉴权上下文。未来只记录安全机器码、account id、key id、失败阶段，不打印密码、Cookie、token、配置JSON、原始第三方响应或请求头；校验失败不回显输入 |

## 威胁模型与密钥边界

| 场景 | 预期能保护的范围 | 无法保证的范围 |
| --- | --- | --- |
| 只有数据库副本泄漏 | 攻击者没有外部密钥时，受保护字段不能直接读出，篡改可被认证校验发现 | 普通配置、账号元数据、文件名等未加密内容；授权API仍可被应用解密 |
| 整个data目录/数据库备份泄漏 | 密钥放data之外且备份独立控制，密文仍有保护 | 把密钥与库放同一归档/同一泄漏权限域则不成立；旧明文备份仍暴露 |
| 运行中主机/容器完全失陷 | 可减少某些意外日志泄漏，但不承诺抵抗此威胁 | 攻击者可读密钥、adapter缓存、请求内存或调用解密接口；字段加密无法阻止 |

选择独立只读挂载的密钥文件/secret manager作为运行密钥来源，不放Git、数据库、data目录或现有secrets.json，不复用JWT密钥/pepper。容器只获取所需keyring，部署者维护独立的受控密钥备份及访问记录。密钥文件权限与数据库备份权限分开；文件路径/密码变量不能进入应用日志。单独数据库备份不含密钥，恢复需另外授权取回密钥备份。没有可恢复密钥就不能启用新格式写入。

## 格式、加密与兼容设计

建议采用维护中的 `cryptography` 库提供的AES-256-GCM AEAD接口，安装及版本需在实施前单独评审固定，不能自己实现密码算法。AEAD可同时保护保密性与完整性；同一key下nonce不得重用，错误key、nonce、AAD或篡改应验证失败。[官方AEAD文档](https://cryptography.io/en/latest/hazmat/primitives/aead/)。

建议新建专用凭据记录：稳定credential UUID、account id、schema_version、credential_revision、encryption_version、key_id、nonce、ciphertext+tag。只加密按网盘字段schema提取的秘密片段，普通config_json保留普通字段；旧cookie和config_json秘密字段切换后必须清除，不做冗余明文镜像。新行创建先分配UUID，AAD绑定数据库实例UUID、credential UUID、drive_type、schema/encryption版本；不绑定可变账号名称，避免改名无法解密。不同库恢复须保留实例UUID；克隆为新实例需显式重新封装。

每次加密从系统CSPRNG生成96bit nonce；建立同key_id/nonce唯一约束，先取得唯一nonce占位再加密，冲突重新生成且次数有界，不能由时间戳或account id推导nonce。key_id公开、密钥本体不入库；不同密钥ID可并存。所有结构长度/版本/字段必须先校验，未知版本或格式损坏拒绝读取并标记账号不可用；绝不能把损坏密文回退解释成旧明文，也不能自动重新登录重试掩盖损坏。

识别按专用版本字段，旧记录为明确legacy-v0，新记录v1；不以字符串看似base64或特殊前缀猜。双读仅允许v0明文路径与v1严格认证解密，新写统一v1；缺密钥或解密失败使相关账号动作明确失败，不返回空凭据。不通过将密文串直接传现有AdapterRegistry来“兼容”。同时修改扫码、编辑、刷新回调、签到和导入，写入前合并用户未更换的原秘密，更新以credential_revision做条件写防旧刷新覆盖新扫码凭据。

旧程序不理解v1格式，也可能重新写明文，**不能读取/写入迁移后数据库**。启动检查schema能力版本阻止旧程序运行；程序回滚必须恢复匹配旧schema的迁移前快照，并处理此后凭据轮换问题。禁止声称只revert代码即可无损恢复。

## 迁移、轮换、缓存和恢复

1. 实施批准后先完成假数据演练，维护窗口暂停账号写入/任务准入、等待在途adapter释放。以SQLite backup创建一致性快照，校验完整性与恢复可启动；单独验证密钥备份可取回，记录版本和密钥ID，不记录密钥值。备份或恢复失败则不得开始正式迁移。
2. v0→v1逐账号短事务：读取并验证schema，提取秘密，生成密文，解密自检只在内存比较，写credential记录、清cookie/config秘密、写版本/审计一起提交。用版本与revision条件更新；崩溃后重跑跳过已v1且认证正常的记录，未知/损坏拒绝，未提交事务回滚，禁止出现v1记录却保留明文副本。
3. 迁移期间不执行网盘外部写入，也不以第三方登录成功作为数据加密事务的一部分。扫码/刷新在完成切换后重新放行；仅账号凭据更新失败可报告需重新认证，不重放已保存文件。
4. 提交成功后bump账号版本，清account_manager快照、AdapterFactory实例/HTTP会话、token single-flight相关缓存及活跃认证会话；等在途读者结束再退役旧实例。清理Python对象不保证内存字节可靠擦除，报告边界。轮换/恢复后必要时重启单进程释放旧对象。
5. 密钥轮换：新key_id写入，旧key仅解密；分批认证解密旧密文后以新key重新封装，每批带revision CAS与账本。全部行、保留快照及恢复清单检查完毕才计划退役旧key；只扫描当前库无旧key不代表旧备份不再需要它。
6. 密钥丢失：启动/账号动作失败关闭，保留密文不覆盖；恢复独立keyring后再校验。若无备份，密文不可恢复，只能用户重新授权并显式替换凭据；不能伪造“忘记密钥可从数据库找回”。
7. 恢复顺序：停止写入→恢复同一检查点库和匹配程序→恢复所需keyring/实例UUID→校验integrity和所有凭据认证标签→失效缓存→假adapter验证账户读取→按授权执行真实只读联调→开放写入。恢复旧快照可能带来已被网盘轮换作废的refresh token，须重新授权，不自动无限重试。
8. 残留治理：确认旧WAL/SHM检查点、过期明文备份/JSON文件/镜像层/日志的保留和销毁策略，经部署者批准后处理；SSD/历史备份无法承诺安全擦除。将旧备份搬到隔离访问域不能被表述为它已变密文。

## 假凭据测试与恢复验收矩阵（待实现）

所有样例仅使用 `fake-cookie-A`、`fake-refresh-A`、`fake-password-A` 和测试生成的临时key；测试不得访问生产网盘、生产库或真实密钥。

| 测试 | 必须断言 |
| --- | --- |
| 十家网盘CRUD、编辑不更换/显式清除、扫码/SMS/captcha、probe/sign-in、刷新回调、配置加载 | 解密后原业务入参一致；普通配置可查询；数据库cookie/config/日志/默认API响应无假秘密原文 |
| 随机nonce与唯一冲突 | 相同明文每次密文不同、同key/nonce不得重复，重试有界 |
| 损坏tag/nonce/AAD、跨账号或drive互换、未知版本/缺key | 明确拒绝、账号不可用，永不回退明文/空成功，不输出输入 |
| 明文/密文混库、重复迁移、每阶段中断、并发扫码与刷新revision冲突 | 原子提交、账本幂等、无秘密冗余明文，不覆盖更新后的凭据 |
| key A→B轮换及部分失败 | 双key读取、只用B新写、重跑可继续，A仍支持旧备份恢复 |
| 备份/恢复、错误keyring、key丢失后找回、彻底丢失 | 实际恢复至独立临时SQLite并通过完整性/标签校验；完全丢失时只能重新授权，不能生成新key假装恢复成功 |
| 缓存/在途对象、认证会话、HTTP头 | 提交后旧凭据不再被新调用使用，在途结束有界释放；数据库回滚不提前发布新凭据缓存 |
| 旧版本回滚 | 旧程序拒绝v1库；迁移前配套快照恢复可启动；已轮换远端token需重新授权 |

此矩阵是未来实现的验收要求，本轮没有用假加密器替代真实密码库来宣称加密和恢复通过，也未运行线上恢复演练。当前明文落库与API明文config现状仍存在，需在方案批准后的专门实施批次解决。
