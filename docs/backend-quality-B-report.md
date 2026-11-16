# 工作包 B：首次初始化原子保护

日期：2026-11-16。实施于 `codex/backend-quality`，未提交、未推送。

## 行为与边界

- `initialize_admin` 应用用例拥有初始化占位、用户、管理员角色关联、刷新令牌和审计的单个事务；路由只传参及组装原有 `LoginOut`，成功响应形状不变。
- 专用 `claim_initial_admin()` 使用 SQLite `INSERT ... ON CONFLICT(key) DO NOTHING`，固定键 `system.initial_admin`。仅已确认的键冲突映射 409 / `SETUP_ALREADY_INITIALIZED`，其他完整性错误不会被误报为重复初始化。
- 保留已有用户快速检查；结束只读快照后竞争唯一写入，获得占位后再查已有用户。历史没有占位但已有用户的安装也拒绝重新初始化。
- 用例仅使用新请求会话，不承接调用方尚未提交的写入；该限制对应检查后释放只读快照的 `rollback()`。不访问真实运行库，不增加模型、迁移或依赖。
- 通用设置仓储拒绝写入保留键。全仓 `SystemSetting` / `system_settings` 引用扫描确认：当前没有通用设置键的删除、重置接口，HTTP 设置更新使用固定业务键，无法单独删除初始化占位。直接运维 SQL 不属于接口保护边界。
- 权限在事务内直接查询，不在提交前填充进程权限缓存，避免回滚残留管理员权限缓存。
- 数据库暂不可用返回 503 / `DB_NOT_READY`；任何创建阶段失败均回滚，下一次合法请求可重试。提交归属在 `test_commit_ownership.py` 的 `TRANSACTION_OWNERS` 显式登记并由 AST 边界检查约束。

## 验证

所有命令在 `backend/` 下，使用 `.venv/Scripts/python.exe`，环境为 `XXM_TESTING=1`、`DATABASE_URL=sqlite://`、`RUN_MIGRATIONS=0`、`SCHEDULER_ENABLED=0`。

首次失败验证：

```text
python -m pytest regression_tests/test_setup_concurrency.py -q --basetemp .tmp/setup-red-1
2 failed, 5 passed
```

旧代码两条独立连接的并发请求均通过前置检查后返回 `[200, 500]`，未遵守第二个请求明确 409 的约定；通用设置写入保留键也未被拒绝。

修复后最终验证：

```text
python -m pytest regression_tests/test_setup_concurrency.py regression_tests/test_commit_ownership.py -q --basetemp .tmp/setup-final-5
14 passed, 1 warning in 9.92s
```

覆盖真实临时 SQLite 文件、两条独立连接、同步屏障、真实 FastAPI 路由；最终只有一位用户、一条管理员关联、一枚刷新令牌、一条成功审计。四种失败注入在 `users`、`user_roles`、`refresh_tokens`、`audit_logs` 的实际 INSERT 执行成功之后抛出，验证占位和半成品全部回滚，随后请求可成功。另覆盖历史库、仅占位无用户、保留键、数据库缺表 503、非占位完整性错误不误映射 409。

警告来自既有 Starlette/AnyIO `BlockingPortal` 弃用提示，无新增运行时依赖。

全量后端、应用导入和分层门禁由首轮总验收统一记录；本报告不替代其结果。远端 CI、Docker 实际运行未验证。

## 回滚

按包恢复 `api/routes/setup.py`、初始化用例、设置仓储及对应测试即可回滚代码；未改变数据库 schema，不需要数据迁移。已成功初始化的占位可保留，旧版本忽略该设置键；不要单独删除占位作为重置方法。
