# 凭据 API 最小暴露整改

2026-11-16。本包只修改应用序列化/编辑合并、响应和输入模型、账号编辑界面、类型与测试。密文持久化和迁移由独立工作包负责；本包未读取或迁移真实数据库、凭据或secrets.json，未commit/push。

## 行为与兼容

- DriveAccountOut.config只包含既有网盘元数据中明确的普通字段，且与core credential_crypto的公共字段allowlist求交。元数据secret/password及通用token/cookie/password等键不会返回，未知配置键也不会回显。123pan loginUuid、guangya phone_number/device_id补充按秘密处理，避免既有元数据漏标。
- 新增credential_present和credential_fields，仅表示是否/哪些登录字段已保存，不包含值。GET账号列表、POST/PATCH及容量概览共用序列化入口。profile只返回展示必要的标量白名单，不再返回raw或未知嵌套第三方资料；last_error返回安全错误提示，去除原始第三方错误上下文。内部resolve_drive_account_config仍返回运行时完整配置供adapter使用。
- PATCH config改为合并更新。缺少或空白秘密字段保留旧值，明确输入新值才替换；clear_credentials提供单独清除操作，公共字段或不存在键不可清除。拒绝星号/圆点掩码值，不能把UI占位文本保存成凭据。创建仍需原有登录参数；清除后账号可能需要重新认证，不伪造登录成功。
- 前端编辑表单不显示已存凭据，显示“已保存，留空保持不变”；提供逐字段明确清除选择。提交逻辑独立纯函数有行为回归；新增/更换仍传用户真实输入，未用掩码替代值。TS类型、更新API参数、响应schema与golden同步。
- 此变更收紧原有读权限的输出，不影响adapter内部配置读取。原先依赖GET账号接口取回明文秘密的外部集成不再支持；应使用写入/替换和存在性字段。QR挑战流程本身需返回的payload不属于本包账号config回显，不能据此声称所有认证接口都无敏感数据。

## 验证与证据

先新增十家网盘假凭据、真实SQLite、实际GET路由及编辑保留/清除测试，修前13 failed，明确包括明文config/profile.raw/last_error泄漏及编辑未提交secret被覆盖。首次测试夹具datetime JSON和空prefix错误修正后再确认真实红灯，未将夹具错误算作生产缺陷。

在backend目录，设置XXM_TESTING=1、DATABASE_URL=sqlite://、RUN_MIGRATIONS=0、SCHEDULER_ENABLED=0：

```text
python -m pytest regression_tests/test_credential_api_protection.py regression_tests/test_account_snapshot_cache.py regression_tests/test_task_run_account_snapshot.py regression_tests/test_frontend_contract_golden.py -q --basetemp .tmp/credential-api-combined
31 passed, 1 warning in 5.91s
```

实际GET/PATCH验证仅具账号读写权限的假用户：返回无假凭据值；空config更新保留凭据；显式清除后credential_present=False。十家参数用例额外覆盖未知token键、原始profile及错误文本。warning为既有Starlette/anyio弃用提示。

账号响应契约首次生成golden时测试ROOT变量拼写错误已修正为REPO_ROOT，再取消UPDATE_CONTRACT_GOLDEN运行上述完整组合，确认快照不是在验收时自动更新。

独立复审补齐legacy cookie输入分支：原始cookie也先解包为实际提供字段，再统一执行掩码拒绝、当前配置合并及空秘密保留；KV部分更新不使用默认值覆盖其他登录字段。新增真实SQLite测试覆盖整串掩码、KV密码掩码、部分KV替换保留authorization/username及空cookie编辑保持。`--basetemp .tmp/credential-api-legacy-fix`：14 passed、1既有warning（5.52s）。

前端 `node --test frontend/regression_tests/driveAccountConfig.test.mjs`：2 passed；本机Node24.22.1执行 `pnpm --dir frontend build`：vue-tsc及Vite成功，2471模块，既有大bundle提示保留。未实际浏览器操作，不把类型/构建通过声称为真实UI或第三方账号联调。

## 回滚边界

应用serializer/合并逻辑、schema、TS、账号抽屉/提交帮助函数和golden应一起回滚，避免回到“服务端隐藏、前端空值覆盖”的组合。不要恢复整个drive_accounts文件覆盖其他工作包；本包未引入数据库列。持久化加密包有其独立迁移/恢复约束，不能以仅回滚本包代码替代库/密钥恢复。
