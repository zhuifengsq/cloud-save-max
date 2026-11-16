# 后端质量 C 包交付记录

日期：2026-11-16。未 commit、未 push；仅本地实现与验证，远端 CI 未运行。

## 改变与兼容范围

- 分层检查器分别执行 violations/deferred_imports/cycles 预算，解析错误或无效/缺失基线退出 2，超预算退出 1；基线不能批准分层违规、循环。`--app-root` 可扫描独立小包。现有 layering 工作流命令原样接入，不复制工作流。
- 基线在独立步骤显式登记现状 116 条（原 90 条加 28、减 2），保留零违规、零循环；完整来源、行号及原因见 [复核清单](backend-layering-deferred-audit.md)。新增来源/目标或重复次数超清单，即使总数不增也失败。行号移动不视为新依赖。
- 三项重复配置只去重；`use_unified_run_use_case`、`use_notify_queue`、`use_keyed_locks` 字段、True 默认及环境变量行为保留。F1 的兼容开关删除不属于本包。
- 健康检查优先环境变量；git 读取缓存成功与失败结果，首次并发填充串行，保留各命令 5 秒超时。readiness 每次查数据库，失败 503；liveness 不依赖数据库。无响应字段变化。
- 任务准入模块入口指向既有 ADR 0003；开发规范补充门禁、缓存和临时目录约束。单进程支持边界保持。

## 失败与通过证据

所有测试使用项目 `backend/.venv/Scripts/python.exe`，设置 `XXM_TESTING=1 DATABASE_URL=sqlite:// RUN_MIGRATIONS=0 SCHEDULER_ENABLED=0`。从 backend 运行，未读取运行数据库或凭据文件。

1. 新建真实 CLI 小包测试，先增加独立包扫描入口，再复现旧门禁：`python -m pytest regression_tests/test_layering_budget.py -q --basetemp .tmp/quality-c-red2` → **7 failed, 2 passed**。延迟/循环超标、解析失败、无效基线实际返回 0，确认失败来自旧逻辑而非仅字段缺失。
2. `python -m pytest regression_tests/test_health_status.py -q --basetemp .tmp/quality-c-health-red` → **2 failed, 4 passed**。两次请求各启动 git 两次，共 4 次；有 git 和无 git 均复现重复启动。
3. 修后并补充同数量未批准依赖、不可提高违规/循环预算、缺失基线及解析失败禁止更新基线用例：`python -m pytest regression_tests/test_layering_budget.py regression_tests/test_health_status.py -q --basetemp .tmp/quality-c-acceptance` → **20 passed, 1 warning in 4.63s**。warning 是现有 Starlette 使用 anyio 兼容别名的 deprecation。
4. 配置命令实例化 `Settings(_env_file=None)` 验证三个默认均 True；注入对应大写环境变量 false 再实例化均 False → **settings defaults/env: ok**。
5. 从 Git 提取实施前 HEAD 的 app 源码至隔离临时目录，用原始 `HEAD:scripts/check_layering.py` 扫描 → **294 modules, 925 internal_imports, 116 deferred_imports, 0 violations, 0 cycles**。历史 `ffd0314` 提交有 251 模块、90 条延迟导入；逐项比较按来源/目标及次数，不凭旧总数推断新增量。
6. 根目录运行 `python scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json` → **296 modules, 927 internal_imports, 116 deferred_imports, 0 violations, 0 cycles, exit 0**（包含同期 A/B 新模块，最终集成数量以主报告为准）。`git diff --check` → exit 0。

完整测试、应用导入和最终集成门禁由主任务统一运行并记录，以上局部通过不代替完整验收。现有 PR/main 完整测试和迁移工作流保留；无真实服务联调。

## 复审修复与最终局部验收

复审发现：包级来源/目标不足以识别同一 `from 包` 下替换子模块或增加名称。补充完整导入语句指纹，包含全部名称、忽略 `asname` 与名称顺序；绝对子模块精确参与循环图。保持旧 116 语句预算，另登记 130 条延迟指纹（含 14 相对引用）及 14 条所有作用域相对引用清单。旧数字基线向后兼容，本项目基线同时保留清单。

- `python -m pytest regression_tests/test_layering_budget.py -q --basetemp .tmp/quality-c-review-red` → **3 failed, 14 passed**，复现同包换子模块、加名称及绝对 from 子模块循环未识别。
- `.tmp/quality-c-relative-red` 同命令 → **3 failed, 19 passed**，复现新增/替换相对延迟引用被旧逻辑遗漏。
- `.tmp/quality-c-relative-top-red` 同命令 → **3 failed, 22 passed**，复现新增/替换顶层相对引用被遗漏。
- 最终 `python -m pytest regression_tests/test_layering_budget.py regression_tests/test_health_status.py -q --basetemp .tmp/quality-c-review-final` → **31 passed, 1 warning in 7.67s**。脚本、测试及基线已稳定，主任务随后进行最终完整验证。
- 最终局部扫描仍为 **296 modules, 927 internal_imports, 116 deferred_imports, 0 violations, 0 cycles**。基线明确更新包含完整指纹，不提高违规或循环预算。

相对依赖探索性扫描发现历史 adapter 5 模块循环，详见 [清单及具体路径](backend-layering-deferred-audit.md)。本轮保留绝对图预算语义，不声称相对引用无环；通过独立批准清单保守拒绝所有作用域新增相对依赖，适配器解环作为后续专项。此范围限制已同步开发规范。

## 提交归属与回滚

本包不新增数据库提交点或模型/迁移，不需要变更提交所有权登记。后续若提交，应把脚本、基线、测试、源码及文档作为同一 C 包提交；当前按用户要求不自动提交。回滚本包需共同还原这些文件，并保留 A/B/D 对共享文件的修改；不要以完整文件覆盖的方式回滚他人修改。回滚健康缓存即可恢复每请求 git 查询；回滚检查器将失去延迟及循环预算保护。
