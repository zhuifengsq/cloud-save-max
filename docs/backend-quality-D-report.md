# 工作包 D：渐进静态检查验收记录

日期：2026-11-16。已在本地实施；未提交、未推送，未运行远端 CI。

## 范围与配置

- `backend/pyproject.toml`：Ruff 以 Python 3.13 解析，仅启用 F821/F822/F823，执行范围为 `app/`。未启用格式化、导入排序或其他规则。
- mypy 默认文件范围为 `app/domain/ports`，`strict = true`；没有全局忽略、`type: ignore` 或导入跳过配置。随端口导入的纯领域值对象仍由 mypy 正常分析。
- 开发依赖精确固定 `ruff==0.14.4`、`mypy==1.18.2`。安装前核对现有三项开发依赖，保留 pytest、pytest-asyncio、httpx。运行时 `requirements.txt` 未改变。
- 在既有 `docker-publish.yml` 的 backend job 中添加两项检查；原 PR/main 触发范围、全量测试和迁移检查保留。

官方 [Ruff 0.14.4 元数据](https://pypi.org/pypi/ruff/0.14.4/json) 和 [mypy 1.18.2 元数据](https://pypi.org/pypi/mypy/1.18.2/json) 均声明 Python 3.13 支持，本机使用 Python 3.13.9。Ruff 的解析版本配置见 [官方设置文档](https://docs.astral.sh/ruff/settings/#target-version)。选择的是经验证的固定版本，不声称为最新版本。

## 失败证据与修复

先建立配置，再在专用虚拟环境运行，修改应用注解之前得到：

```text
python -m ruff check app
Found 8 errors. (F821)

python -m mypy --strict app/domain/ports
Found 20 errors in 2 files (checked 10 source files)
```

Ruff 报告的未定义名称为 ResourceSearchSource、DriveAccount（2 处）、ShareValidateOut、TelegramBotState（3 处）、Task，均在类型注解中。ShareValidateOut 改为使用已有应用 DTO 的模块级导入；其余引用动态仓储返回的 ORM 对象，沿用该应用边界已有的 `Any` 类型，不为修复静态名称而引入违反分层的 ORM import。此处仍是未收紧的动态边界，不能据此宣称这些调用链已经具备严格类型检查。

mypy 报告的是 lead_in 端口未标注的 `*args` 与裸 `dict`，以及 saved_progress 的 JSON 快照裸 `dict`。将异构网盘 JSON 边界显式标为 `dict[str, Any]`，保留动态入参的 `*args: Any`；未修改运行时签名、返回形状或业务逻辑，也未宣称 JSON 内容已获静态校验。

配置实际生效另外用独立临时输入验证：Ruff stdin 分别提供未定义名称、未定义导出、读取未赋值局部变量，三项均退出 1 且分别出现 F821/F822/F823；mypy 使用无类型注解函数且仅读取 pyproject 配置（不额外传 `--strict`），退出 1 并报告 `[no-untyped-def]`。临时探针在运行后清理，未放入应用目录。

## 本地命令与输出

以下命令从 `backend/` 运行，工具使用新建的独立环境 `.tmp/quality-static-venv`，并非本机 PATH 中的工具：

```powershell
$toolPython = (Resolve-Path .tmp/quality-static-venv/Scripts/python.exe).Path
& $toolPython -m ruff --version
# ruff 0.14.4
& $toolPython -m mypy --version
# mypy 1.18.2 (compiled: yes)
& $toolPython -m ruff check app
# All checks passed!
& $toolPython -m mypy --strict app/domain/ports
# Success: no issues found in 10 source files
& $toolPython -m pip check
# No broken requirements found.
```

安装时 pip 25.2 在线解析持续高 CPU 且没有进展，停止该专用安装进程后，改从官方 PyPI JSON 下载匹配 Python 3.13 Windows 的 wheel，逐个校验元数据提供的 SHA256，再 `pip install --no-index --no-deps` 安装。独立工具环境实际包版本为 ruff 0.14.4、mypy 1.18.2、typing_extensions 4.15.0、mypy_extensions 1.1.0、pathspec 0.12.1。依赖一致性检查通过；这些辅助版本仅记录本次隔离验证环境，不改运行时依赖。

回归验证设置 `XXM_TESTING=1`、`DATABASE_URL=sqlite://`、`RUN_MIGRATIONS=0`、`SCHEDULER_ENABLED=0`，使用项目测试虚拟环境及专用 basetemp：

```text
.venv/Scripts/python.exe -m pytest regression_tests -q --basetemp .tmp/quality-D-<独立GUID>
558 passed, 1 warning in 36.00s
```

警告为现有 Starlette 对 anyio BlockingPortal 别名的弃用提示。`git diff --check` 退出 0。并行实施期间的最终全量测试、应用导入和分层门禁以汇总验收记录为准；本报告不把回归子集冒充全量测试。

## 兼容与回滚

没有 HTTP、SSE、数据库模型或前端契约行为变化。仅更改开发工具、CI 接线和注解；无需模型迁移。未验证真实网盘服务，未运行远端 CI。后续扩展 mypy 到端口的实际调用链需按链逐步验收。

提交归属为工作包 D（当前尚无提交）。回滚时一并恢复该包的 pyproject、开发依赖、CI 步骤和注解修改；不撤销其他工作包或用户原有修改。
