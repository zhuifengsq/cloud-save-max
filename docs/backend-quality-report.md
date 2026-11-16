# 后端质量改进首轮验收

后续复审整改（同日的新批次）已实现登录资源保护、凭据API隐藏、可选加密持久化/假数据恢复、代理配置与检查范围扩展；本报告保留此前验收历史。当前结果与未完成增强项以 [安全复审整改](backend-security-remediation.md) 为准。

日期：2026-11-16。分支：`codex/backend-quality`。A～F代码与G/H盘点设计已交付，额外完成适配器解环与CI发布保护；历史时间及真实凭据加密迁移未实施。下文A～D结果保留为首轮历史记录，最新集成验收在文末。

保留实施前用户已有的 `docs/backend-code-review.md` 修改及未跟踪的任务书，未提交或推送。本次不改运行时依赖、数据库模型或迁移，不访问真实数据库、secrets.json、真实凭据或网盘 IO。

## 工作包与提交归属

| 包 | 实际变更 | 记录与回滚单元 |
| --- | --- | --- |
| A | 组合5次/IP30次失败准入；600秒窗口、900秒冷却、两表各10000条；429机器码与Retry-After；失败审计；提交成功才清组合计数；代理信任收紧 | [配置/部署/契约](backend-login-protection.md)。回滚 login_throttle、auth应用/路由、errors/handler、settings新增字段、schema/golden及Dockerfile/default代理配置与对应测试 |
| B | 唯一占位、管理员/角色/令牌/审计原子初始化；409冲突与503数据库语义；保留键保护 | [B 验收](backend-quality-B-report.md)。回滚用例、setup路由/应用、设置仓储及对应测试，无新schema |
| C | 三类预算、来源批准清单、解析错误失败；健康git回退缓存；配置去重、ADR指引 | [C 验收](backend-quality-C-report.md)、[116条来源复核](backend-layering-deferred-audit.md)。门禁脚本、基线与批准来源记录一起回滚；健康缓存可独立回滚 |
| D | 固定开发工具；最小Ruff规则、领域端口strict与现有backend CI接线；明确注解错误修复 | [D 验收](backend-quality-D-report.md)。开发依赖、pyproject、CI步骤及注解修复一起回滚 |

后续按包提交时，各包代码、测试和契约记录应一起归属同一提交；当前保留未提交差异供审阅。A/B共同使用认证发令牌能力，settings包含A新增字段与C去重，回滚时按差异块操作，不能用整文件恢复覆盖其他包或用户修改。

## 失败证据与修复

A：新模块未实现时5项失败；真实路由测试覆盖401/429/成功、IP跨用户名累计、并发、未知/停用用户、审计故障、数据库故障释放、可信/不可信代理头、刷新/退出与正数配置边界。提交失败清计数的测试修前明确得到401而非429，修后通过。新增冷却短于窗口边界测试；共21项A测试通过。

B：旧实现SQLite并发请求复现200/500，不满足200/409；仓储设置保护缺失。修后真实文件SQLite的独立会话同步屏障验证仅一个管理员、一次成功审计；在用户、角色、令牌、审计真实INSERT后注入失败，验证全部回滚与重试。

C：修前真实CLI预算/无效基线/解析失败测试7 failed，健康缓存测试2 failed，修后通过。保留原116条语句预算，来源复核明确90→116为增加28、删除/改向2，未默默提预算。D：修前8个F821与20个mypy错误，修后检查通过；隔离工具环境另验证规则确实拒绝错误代码。

## 验证方法

所有Python运行均设置 `XXM_TESTING=1 DATABASE_URL=sqlite:// RUN_MIGRATIONS=0 SCHEDULER_ENABLED=0`，使用项目 `.venv` 和唯一 `--basetemp .tmp/quality-<唯一值>`；工具使用独立 `.tmp/quality-static-venv`。

起始命令 `python -m pytest tests regression_tests -q --basetemp <独立目录>`：857 passed、2 skipped。最终记录见下方。

A 局部验收实际命令（backend目录）：`python -m pytest regression_tests/test_login_throttle.py regression_tests/test_login_route_security.py -q --basetemp .tmp/quality-a-review-green`，输出 `21 passed, 1 warning in 11.64s`。契约生成后另外取消 `UPDATE_CONTRACT_GOLDEN` 执行完整回归，避免以自动更新快照掩盖契约漂移。前端现有 `ApiErrorBody.code?: string` 与登录页 message 消费兼容新增机器码，无需更改成功响应类型；新增429的OpenAPI schema和golden已同步。

前端 `node --test <frontend/regression_tests/*.test.mjs完整文件列表>`：59 passed。初次 `pnpm --dir frontend build` 因默认Node18.20.4不满足Vite要求失败；临时在当前命令PATH前置本机Codex附带Node24.22.1后，同一构建命令通过（vue-tsc与Vite均成功），未改系统默认Node或依赖。构建有既有体积/插件耗时提示。

`python -m ruff check app`：All checks passed!；`python -m mypy --strict app/domain/ports`：Success: no issues found in 10 source files。实际工具固定版本为ruff0.14.4、mypy1.18.2。`python -c "import app.main; print('import ok')"`：import ok。

## 尚未验证的场景

远端CI、真实nginx容器/多级代理拓扑、真实网盘服务均未验证。未部署。本地TestClient和代理中间件测试不代替真实服务联调；保护仅支持单进程单副本，重启清空封禁。动态仓储与异构JSON仍保留显式Any边界，不声称调用链已完整类型化。没有模型或迁移变更，因此未因本批运行历史数据迁移或批量SQL。

门禁复核另发现旧扫描图未纳入的相对导入历史循环（网盘适配器 complete_listing 与相关适配器/base_adapter 的5模块组）。本轮保留既有图与116条语句预算的统计语义，不通过提高循环预算批准，也不扩张为全仓适配器重构；以额外导入指纹阻止新增未批准相对依赖。门禁“0 cycles”指已覆盖的绝对导入图，不代表所有Python运行时依赖无环；详细路径在C报告中登记为后续治理项。

## 首轮 A～D 集成结果（历史）

所有实现和测试稳定后，从 backend 运行：

```powershell
$env:XXM_TESTING = '1'
$env:DATABASE_URL = 'sqlite://'
$env:RUN_MIGRATIONS = '0'
$env:SCHEDULER_ENABLED = '0'
.\.venv\Scripts\python.exe -m pytest tests regression_tests -q --basetemp .tmp/quality-final-acceptance
```

输出：**917 passed, 2 skipped, 1 warning in 44.75s**。warning为现有Starlette/anyio弃用提示。A最后局部21 passed，B及提交归属14 passed，C最后局部31 passed；各局部集合可能重叠，不相加作为总数。

根目录 `backend/.venv/Scripts/python.exe scripts/check_layering.py --fail-on 0 --baseline backend/.layering-baseline.json`：**296 modules、927 internal_imports、116 deferred_imports、0 violations、0 cycles，exit 0**（绝对导入图口径）。另登记130条完整延迟导入指纹（含14条相对延迟导入）、14条全作用域相对导入指纹，未批准新增/替换均失败。

最终应用导入 `import ok`；独立工具环境 Ruff `All checks passed!`；mypy strict `Success: no issues found in 10 source files`；`git diff --check` exit 0。远端CI未运行。分包及整体复审发现的登录提交失败清计数、冷却短于窗口、from子模块批准漏报均已修复并复核；已披露历史相对循环保留为后续治理项。

## 后续 E～H 与解环交付（当前）

- E 已拆应用编排、纯计划、只读领域端口与基础设施读取，网盘差异经统一网关；保留过滤、重命名、目标差异、Web/Telegram业务行为及响应字段。移除不参与预览结果的TMDB设置创建和网络调用。见 [E报告](backend-quality-E-report.md)。集成审查另补只读会话护栏：调用方有未提交ORM写入时拒绝预览，避免结束读事务时把业务写入一起提交；新增用例修前明确未抛异常，修后通过。上游status=500伪装空目录也经失败用例复现并修正为既有TASK_SHARE_DETAIL_FAILED。
- F1 删除仅测试使用的run/_legacy_execute、失效开关与executor_cls装配缝，保留策略、装配和execute行为覆盖。见 [F1报告](backend-quality-F1-report.md)。集成补充真实execute/store/engine/drama链路、独立SQLite与假网盘IO验证10例：固定非运行日、五触发方、分享valid/invalid，精确断言实际转存、跳过、记录数、通知次数与内容；10 passed（3.75s）。
- F2 发送实现迁至channel_delivery，filtering负责过滤，sender/queue组织同步测试和异步运行；23渠道保留，覆盖参数/发送次数/满队列/消费异常/同步回退。见 [F2报告](backend-quality-F2-report.md)。没有更改通知产品口径或网盘写入重试。
- G/H 已交付 [时间盘点与治理方案](backend-time-policy.md)、[凭据保护与恢复设计](backend-credential-protection.md)，固定时钟13项通过。交付性质是方案；没有UTC历史批量更新、加密库安装、真实密钥操作、线上凭据迁移或恢复演练。
- 已将共享列表协议值/纯判断下沉listing_protocol，消除原5模块循环；相对依赖现参与真实分层/循环检查。见 [解环和CI报告](backend-quality-cycle-ci-report.md)。CI改PR仅构建、main才发布，发布前同一backend job必须通过分层；构建传入真实SHA。远端CI/真实Docker及代理链仍未执行，不自动提交或推送。

基线调整是业务改动完成后的独立审计步骤：新增仅container→share_preview_reader一条装配；删除旧factories/container执行器缝两条、legacy通知函数内导入两条、预览旧函数内导入三条、完整列表对具体适配器的三条相对反向引用。绝对延迟语句116−7+1=110，完整指纹130−10+1=121，相对引用14−3=11。逐项核对后才用--update-baseline登记，违规和循环预算保持0。当前300模块、934内部绝对导入、110延迟语句、0违规、0循环（包含相对依赖精确图）。旧C报告/清单中的116及相对循环限制是当时历史记录。

本轮审查：执行/通知和解环/CI经独立只读交叉审查；E由主代理核查并补充事务/错误边界测试。中断期间未完成的附加执行验收已由主代理接续。所有原有用户文档修改保留，未提交、未推送、未访问生产库或真实凭据。

### 当前最终验证结果

测试环境变量仍为XXM_TESTING=1、DATABASE_URL=sqlite://、RUN_MIGRATIONS=0、SCHEDULER_ENABLED=0。backend目录运行 `python -m pytest tests regression_tests -q --basetemp .tmp/quality-completed-final3`：**1014 passed, 2 skipped, 1 warning in 48.93s**。warning为既有Starlette/anyio弃用提示。中间一次失败来自百度测试用无约束Mock充当Session，新只读护栏无法据此检查状态；测试改用真实独立SQLite Session，保留原失败断言，随后全量通过。

最终Ruff：All checks passed!；mypy strict：Success: no issues found in 11 source files；应用导入：import ok；分层：300模块/934内部绝对导入/110延迟语句/0违规/0循环（相对依赖已纳入）；git diff --check：exit0。

前端完整回归59/59通过，使用本机Node24.22.1运行pnpm --dir frontend build通过（vue-tsc及Vite）；保留既有bundle体积/插件耗时提示，没有放宽阈值。全新独立SQLite的CI同款升级/降级/升级及schema drift检查通过（26表、0漂移），详见解环/CI报告。

真实远端CI、Docker镜像运行、实际外层代理链、真实网盘服务均尚未验证；按此前决定不阻塞本地开发交付。本地工作包已完成，生产部署和G/H方案之后的迁移实施不在本次执行范围。
