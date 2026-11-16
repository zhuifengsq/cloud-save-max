# 登录失败防护与代理部署

2026-11-16，工作包 A。

登录成功的响应和刷新、退出契约保持原样。错误沿用 `{code, message, detail}`：凭据错误、账号不存在或停用统一 HTTP 401 / `AUTH_BAD_CREDENTIALS`；拒绝准入返回 HTTP 429 / `AUTH_LOGIN_THROTTLED`，附正整数秒 `Retry-After`，前端现有通用错误提示直接显示 message，不触发刷新重试。

默认同一 IP + 用户名在 600 秒窗口内连续失败 5 次，后续拒绝 900 秒；同一 IP 跨用户名失败累计 30 次也拒绝 900 秒。成功事务完成后清除组合计数，保留 IP 失败计数。同组合请求有界等待前一个完成，默认最多2秒、全局最多16个等待者；唤醒后重新检查阈值，超时/容量满才429。不同组合的在途数计入 IP 准入预算，避免并发绕过。数据库失败和异常均释放在途状态，密码校验在全局锁外执行。

环境配置在进程启动时读取：次数和容量要求正整数；清理间隔要求正数，等待时限允许非负数：

| 环境变量 | 默认值 |
| --- | --- |
| LOGIN_THROTTLE_PAIR_LIMIT | 5 |
| LOGIN_THROTTLE_IP_LIMIT | 30 |
| LOGIN_THROTTLE_WINDOW_SECONDS | 600 |
| LOGIN_THROTTLE_COOLDOWN_SECONDS | 900 |
| LOGIN_THROTTLE_CAPACITY | 10000 |
| LOGIN_THROTTLE_SWEEP_INTERVAL_SECONDS | 30 |
| LOGIN_THROTTLE_WAIT_TIMEOUT_SECONDS | 2（允许0，立即拒绝在途竞争） |
| LOGIN_THROTTLE_MAX_WAITERS | 16 |

IP 与组合表分别限制为 capacity 条。每30秒最多一次全表清理，当前键每次独立检查过期；不淘汰有效计数/封禁。满容量时新键可能等到下次清理才能进入，返回Retry-After是保守等待建议。失败计数使用单调时钟且不受数据库回滚影响。

失败审计改为有界独立聚合：每5秒按IP批写failure_count，一批一个独立事务；请求线程不再同步写SQLite。最多1000个待写来源、单计数上限10亿，队满、批写失败、退出超时均安全诊断且可能丢聚合，不无限扩容/重试。不保存用户名、密码、令牌、Cookie或User-Agent。它是best-effort聚合审计，不承诺逐事件持久化。成功登录审计仍与令牌同事务。消费者启动失败有界重试，进程重启会清空限流和未落盘审计，仅支持单进程单副本。详见复审整改报告。

容器内 nginx 将 X-Forwarded-For 覆盖为 TCP 对端 `$remote_addr`，uvicorn 仅监听回环 127.0.0.1:8000，显式仅信任 127.0.0.1 的代理头，应用只读取 request.client.host。禁止设置 forwarded-allow-ips='*' 或直接使用客户端头作为计数键。

若 nginx 前面还有负载均衡/反向代理，必须按实际拓扑配置 nginx real_ip：用 set_real_ip_from 列出可信代理地址段、real_ip_header X-Forwarded-For、real_ip_recursive on；代理必须清理或追加真实对端，禁止信任任意网段。未配置时 nginx 看到的是上级代理地址，会将其用户归到同一 IP。仓库默认面向直接访问 nginx，不猜测部署者的可信网段。

当前容器入口支持显式TRUSTED_PROXY_CIDRS（逗号分隔可信上游CIDR）及TRUSTED_PROXY_HEADER（仅X-Forwarded-For/CF-Connecting-IP）；默认无信任，启动生成nginx配置并校验，拒绝全网信任和配置注入。只有配置真实上游地址后才能恢复正确客户端IP；没有实际Tunnel拓扑联调证据，不能仅凭配置功能声称部署已验证。

本地回归覆盖实际认证路由和 uvicorn 代理中间件；未启动真实 nginx 容器，线上代理链与真实服务联调尚未验证。回滚需恢复 A 的代码、配置和代理变更，无数据库迁移。未提交或推送。
