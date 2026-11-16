# 账号、持久化与执行链 strict 类型门禁扩展

本轮将 mypy strict 显式范围由 22 个文件扩至 37 个文件，约 3500 行。范围包含真实业务实现及正向装配检查，不以只增加空协议文件计数。

## 新增检查范围

- 账号服务 `application/drive_accounts.py`：创建/编辑入参用 TypedDict 和 Unpack，账号实体、公开响应、容量概览有明确契约；仓储、适配器工厂与注册表通过协议装配。
- 持久化：账号模型、账号仓储、事务/Session 工具、凭据迁移。仓储返回实际实体及 Sequence，事件钩子标注 SQLAlchemy Mapper、Connection、InstanceState 和 ORMExecuteState；hybrid 属性使用 SQLAlchemy inplace setter/expression，保留原有实例访问和存储列 SQL 表达式。服务通过显式 `set_credentials` 写入凭据，避免把 ORM 描述符伪装成普通可写字段。
- 执行链：任务用例、执行器、SQL 持久化/准备后执行阶段、执行日志，以及任务值对象和端口。命令、计划、结果、任务状态、策略、日志和恢复回调都有契约；原来以 Any 传递的任务实体和回调改为结构类型。

任务值对象规范位置为纯 domain，旧 application DTO 路径保留显式重导出；认证挑战异常也移到基础模块，适配器旧导入路径保留。没有通过类型声明引入 application 对 infrastructure 的反向依赖。

## 检查策略与边界

`pyproject.toml` 列出的每个文件继续 strict 检查。新增实现的初始探针有 72 项直接错误，均已处理；如果递归报告所有未纳入模块，初始探针还会带出 79 个文件的 1129 项存量错误。本轮采用 `follow_imports = "silent"`：分析这些依赖的类型接口，但将诊断范围限定在显式目标；这不等于依赖已检查通过。没有给目标文件配置 `ignore_errors`、跳过检查或新增整文件忽略，也未声称全仓类型安全。

保留的 Any 用于不同网盘的 JSON 配置、响应和执行快照等动态数据；实体字段、命令、执行结果和恢复回调不使用 Any 作为通过门禁的替代。

## 装配验证与防退化检查

`type_checks/runtime_bindings.py` 将真实 ORM、仓储模块、适配器工厂/注册表与执行阶段赋给对应协议，检查实现确实符合接口，没有使用 cast 强行通过。

`type_checks/invalid_usage.py` 是故意写错的负向夹具，不在正常目标里，也不会被运行或导入。覆盖 9 个错误：账号名称类型、账号状态赋值、凭据写入类型、任务准备命令、持久化结果、恢复回调返回值、执行用例命令、真实 ORM 字段、事务 Session 参数。

统一命令在 `backend/` 下运行：

```powershell
python ../scripts/check_backend_types.py
```

脚本先要求正常范围通过，再要求每个负向夹具的指定行和错误码都被拒绝；若检查退化为 Any 导致错误不再出现，也会失败。CI 后端任务已改用这个命令，仍使用固定的 mypy 开发依赖。只有本机工具环境与项目依赖环境分离时，才额外传入 `--python-executable .venv/Scripts/python.exe`。

## 验证

strict 37 文件通过，9 个负向用例全部被拒绝；Ruff 通过。完整后端回归 `python -m pytest tests regression_tests -q --basetemp .tmp/typing-expansion-full`：1127 passed、2 skipped、1 既有 Starlette/anyio 弃用警告（83.85 秒）。检查使用隔离 SQLite 和假数据，没有读写真实数据库、密钥或网盘，也没有提交代码。本轮不新增业务数据库结构或迁移。

最终补测生成分享检查与执行阶段 18 项通过，应用导入通过。分层检查为 313 模块、963 内部绝对导入、110 延迟语句、0 违规、0 循环；未提高预算。独立只读复核再次运行类型门禁，确认 37 文件通过、9 个反例被拒绝，未提出实质问题。`git diff --check` 通过。
