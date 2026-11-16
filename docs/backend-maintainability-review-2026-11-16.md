# 后端扩展性与可维护性审查

本报告列出的五项整改已实施，改动与最终验收见 [整改交付报告](backend-maintainability-remediation-2026-11-16.md)。下文保留整改前的审查证据。

审查日期：2026-11-16。审查对象：当前工作区后端代码。此次没有修改业务源码。

## 总体判断

后端已有明确的工程结构，适合继续渐进维护。API、application、domain/ports、infrastructure、repository、schema/presenter 的划分，以及执行用例和回归测试，都已实际落地。不能把它概括成没有结构的代码堆。

但分层检查通过不等于依赖已经充分解耦。当前主要问题是：部分依赖通过全局装配访问器间接取得，Web 与 Telegram 仍各自编排账号认证，少数大模块同时承担业务规则和运行时设施；适配器的响应与生命周期契约也没有完全统一。

按本次代码统计，app 下有 313 个 Python 文件、48,450 行（包含注释和空行），13 个文件超过 800 行。这些数字只用于定位审查热点，不作为质量评分。协议复杂的网盘适配器较长并不自动构成问题。

## 发现一：Quark4K 上游故障绕过搜索降级处理（P1，已复现）

位置：

- `backend/app/infrastructure/search/quark4k_plugin.py:90-91`
- `backend/app/application/search_plugins.py:230-237`、`:274`
- `backend/app/application/resource_search.py:283`

触发条件：启用 Quark4K，两个分页请求均发生连接错误、超时等上游故障，且异常在插件外层等待期限内返回。

Quark4KPlugin.search 把原始异常包装成 RuntimeError。聚合层只把 requests.Timeout、requests.RequestException、OSError 等转成单源失败；RuntimeError 会直接抛出。future.result() 又没有在聚合时转换这类异常，因此会终止整个搜索调用，连其他正常源已经返回的结果也不能正常汇总，并可能中断自动换源。

本地使用 mock 将 `_fetch_page` 替换为 requests.ConnectionError，再调用真实 `_run_search(Quark4KPlugin(), ...)`，得到 `escaped_exception=RuntimeError`、`cause=ConnectionError`。没有访问外网。

建议：插件保留已知网络异常的类型，或制定专用的搜索上游异常协议；保留真正编程错误的传播行为，不要简单地吞掉所有 RuntimeError。补充「真实 Quark4K 插件失败 + 另一个源成功」的聚合测试，以及全超时返回 504 的契约测试。

## 发现二：适配器连接释放契约没有落地（P2，已确认实现缺口）

位置：

- `backend/app/infrastructure/drive/factory.py:194-225`
- `backend/app/infrastructure/drive/gateway.py:312`
- `backend/app/infrastructure/drive/adapters/baidu_adapter.py:182`
- `backend/app/infrastructure/drive/adapters/cloud189_adapter.py:161`

缓存淘汰后调用 `_close_adapter`，但该函数在没有 close 方法时直接返回。实际检查当前注册的 10 个适配器，全部没有可调用的 close 方法；其中百度、天翼等持有 requests.Session。因此当前「淘汰时关闭连接」仅存在于调用侧约定，没有落实到资源持有方。clear_cache 也明确只丢引用，不主动关闭。

影响是连接池释放依赖对象回收，无法保证淘汰、凭据更新、退出时及时关闭；本次没有做长期连接数测量，不把它表述成已经测得的持续内存泄漏。

建议：明确缓存持有适配器、请求借用适配器的所有权规则，为持有连接的实现增加幂等 close，并覆盖淘汰、清缓存、应用退出。由于缓存实例会被并发借用，不能直接在每个请求结束时关闭共享实例；关闭策略应同时考虑在用实例。

## 发现三：装配入口逐渐成为全局服务定位器（P2，架构债务）

位置：

- `backend/app/application/container.py:67`、`:172`、`:282`
- `backend/app/application/telegram_bot/actions.py:18-22`
- `scripts/check_layering.py:49-50`

container 有约 60 个顶层函数，大量仓储访问器只返回 ModuleType，另外还直接暴露 AliyunAdapter 等具体实现类。调用方常在模块导入时执行访问器，把具体类绑定为全局变量。这种方式能统一导入路径，但调用方仍知道具体实现，测试也往往需要 patch 调用方全局变量。

分层门禁将 container 作为 composition root 豁免，所以「0 violations」不能证明这些调用点已经面向稳定接口。ModuleType/Any 也削弱了仓储方法和返回值的静态检查。目前 strict 检查范围实际检查 37 个文件，并非全仓。

建议：以正在变更的业务为单位，在用例构造函数注入小型 Protocol；让 container 创建用例和适配器实例。仓储接口逐步补类型，优先覆盖任务写入、认证、搜索等高频变更边界。无需一次性引入 DI 框架或改写所有 CRUD。

## 发现四：Web 与 Telegram 重复实现账号认证业务（P2，扩展成本）

位置：

- `backend/app/api/routes/drive_accounts.py:200`、`:226`
- `backend/app/application/telegram_bot/actions.py:874`、`:922`、`:963`

两端分别实现二维码生成、会话创建/查找、版本校验、扫码状态轮询、确认后写入凭据等流程，并直接使用 AliyunAdapter。虽然底层已共用 prepare/probe 等函数，完整认证编排仍有两份。

新增一种网盘认证方式或调整异常处理时，需要同步修改 Web 和 Telegram，容易造成一端修正、另一端遗漏。这个问题比重复几个格式化函数更值得优先处理。

建议：提取共享认证用例和认证能力端口，统一管理开始、轮询、验证码提交及会话状态；Web/Telegram 保留权限、请求上下文和展示映射。

## 发现五：业务与运行设施集中在几个大模块（P2，维护热点）

重点位置：

| 模块 | 行数 | 混合职责与建议 |
| --- | ---: | --- |
| `application/tmdb_cache.py` | 1,214 | 排期推断、语言数据合并、外部抓取、SQL、专用 engine、并发写槽和后台刷新集中。优先拆纯计算规则与持久化/刷新设施，保留现有 writer 隔离行为。 |
| `application/share_preview_batch.py` | 1,011 | 目录读取、分组、账号选择、并发验证、流式反馈和失败映射集中。按读取、批量验证和应用编排的能力边界拆分。 |
| `application/telegram_bot/actions.py` | 1,068 | 同时覆盖任务、账号认证、浏览目录、通知和设置。先迁出重复认证编排，再按业务领域收窄 facade。 |

例如 fetch_share_file_list_grouped 长 188 行，validate_share_links_streaming 长 162 行。维护压力来自分支与职责组合，而不是行数本身。当前 TMDB 还被分层脚本登记为 application 直接依赖持久化的显式例外。

网盘适配器中 cloud189 约 2,302 行、baidu 约 2,145 行。可以逐步分离认证、传输、目录/分享解析，但不建议仅为缩短文件就制造大量无意义的转发类。

## 扩展性边界

| 扩展方向 | 本次判断 |
| --- | --- |
| 新增搜索源 | 文件发现 + BaseSearchPlugin 的基础较好；先统一失败类型契约，避免单源故障影响聚合。 |
| 新增网盘 | 有工厂注册、URL 识别、元数据和策略扩展点，基础可用；认证、生成分享检测等仍有具体网盘分支，不是注册一个类就能自动支持所有业务。 |
| 新增 HTTP/Telegram 之外的入口 | RunTaskUseCase 等现有共享用例可复用；账号认证等未统一的编排需要先收敛。 |
| 多 worker、多副本扩容 | 当前不支持。进程内任务准入、锁、缓存及每进程启动的调度/Bot 都需要重新划分所有权。 |

单进程是 ADR 0003 已明确接受的约束，不作为未披露缺陷。`task_admission.py:65` 固定 4 个进程内执行槽，`main.py:91-92` 启动调度和 Bot；直接增加 workers/副本会破坏任务唯一执行等假设。若未来需要扩容，应先划分 Web/执行者职责，再决定持久化任务领取、锁和消息设施，不能只改 Uvicorn 参数。

## 本次验证

使用本机项目虚拟环境，测试环境为 `XXM_TESTING=1 DATABASE_URL=sqlite:// RUN_MIGRATIONS=0 SCHEDULER_ENABLED=0`：

- `python -m pytest tests regression_tests -q --basetemp .tmp/review-20261116-a`：1127 passed、2 skipped、1 warning，82.83 秒。warning 为 Starlette/anyio 弃用提示。
- `python -m ruff check app`：All checks passed。
- `python ../scripts/check_backend_types.py`：37 个文件通过，9 个非法调用负例被正确拒绝。
- 分层门禁：313 modules、963 internal_imports、110 deferred_imports、0 violations、0 import cycles；单独列出 TMDB 的 3 条已登记持久化例外。
- 独立 mock 复现：Quark4K 网络异常被包装后逃逸；运行时检查 10 个适配器均未实现 close。

这是源码结构与本地测试审查，不包含真实网盘联调、线上负载、多副本运行或长期资源曲线验证。历史质量报告用于理解既有决策，没有将其中的通过记录作为本次验收证据。

## 建议实施顺序

1. 修复 Quark4K 失败类型契约，增加真实插件到聚合层的回归覆盖。
2. 完成适配器资源所有权及关闭生命周期，验证共享实例的并发使用。
3. 合并 Web/Telegram 的账号认证用例，减少新增网盘的跨入口改动。
4. 拆 TMDB 和分享批量处理的职责，同时逐步将 ModuleType/Any 边界替换为明确接口。
5. 只有出现明确的高可用或容量需求，再启动多进程架构改造。

建议渐进整改，不建议推倒重写。现有测试、用例、端口和网关应作为后续调整的基础。
