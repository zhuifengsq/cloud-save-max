# 适配器解环与 CI 发布边界

日期：2026-11-16。代码本地已实施，未提交、推送或发布。

## 适配器依赖

起始回归：917 passed、2 skipped、1既有anyio弃用warning（46.22s）。相对导入纳入图之前，新增真实CLI小包用例4 failed，明确证明相对循环和非法层方向漏报；修正解析后，分层脚本在本仓库实际返回exit1，报告1组5模块循环：aliyun_adapter、base_adapter、complete_listing、guangya_adapter、xunlei_adapter。

根因是complete_listing反向导入具体适配器的常量/纯函数。新增无适配器依赖的listing_protocol模块，移动阿里列表端点、迅雷列表地址与过滤参数、光鸭成功/消息判定；原适配器继续导出原名称以兼容已有引用，完整目录读取器依赖共用纯模块。没有修改分页、完整性校验、请求参数、写入或重试策略。

扫描器现在按实际package解析相对导入，将其目标加入分层与循环图，包含包的__init__和函数内引用；绝对语句预算仍沿用原统计口径，相对引用另有完整批准指纹。解析不合法的相对导入明确失败。已消除已知循环后才允许刷新批准清单，不提高循环或违规预算。

局部命令（backend目录，测试环境变量与任务书一致）：

```text
python -m pytest regression_tests/test_complete_directory_listing.py tests/test_drama_lead_in_adapters.py tests/test_drama_lead_in_own_file_adapters.py regression_tests/test_layering_budget.py -q --basetemp .tmp/quality-cycle-green
229 passed in 10.24s
```

随后实际门禁：297 modules、931 internal_imports、116 deferred_imports、0 violations、0 cycles，exit0；该次包含解环后的相对依赖图。最终模块/预算随E/F交付有变化，以集成报告为准。A～D报告内“绝对图0 cycles”的限制是当时的历史记录，本轮已补齐图覆盖并解环。

## CI 配置

既有docker-publish工作流保留后端全量测试与迁移往返，追加同一backend job中的分层检查，保证镜像发布不能先于分层结果。原独立layering工作流保留。

PR只进行无凭据镜像构建（push:false）；DockerHub登录、版本生成和push:true均显式限于push到main。构建传入实际提交SHA与构建标识，避免健康接口继续显示Dockerfile中历史默认版本。Docker上下文额外排除本地测试/类型工具缓存和pip/pytest临时目录，不把隔离工具虚拟环境打包进运行镜像。

已使用本机PyYAML解析工作流，并核对PR/main条件、发布依赖及push值，输出：`workflow YAML parsed; PR build-only and main publication conditions checked; layering blocks publication`。配置的本地解析不等于远端Actions运行。

另在新建的 `.tmp/quality-migration-<guid>.db` 上执行既有CI的四条迁移命令：upgrade 20261105_credits_in_media_cache、upgrade head、downgrade 20261105_credits_in_media_cache、upgrade head，全部exit0。显式传入此隔离文件运行check_schema_drift，输出 `db_tables=26 model_tables=26 drift=0`，exit0；未使用默认真实数据库路径。

当前终端未找到docker/gh，且本次不推送代码，因此远端CI、实际镜像构建/发布、nginx容器及外层代理链未执行。此前实际认证路由与uvicorn代理中间件测试仍作为本地证据；不能替代真实部署链路验证。

## 回滚

解环模块与其引用、扫描器/测试、批准清单应一起回滚，避免恢复旧循环后仍使用更完整门禁导致失败。CI发布条件与构建参数可独立回滚，但会恢复PR尝试发布的旧行为。当前无数据库结构或运行依赖变更。
