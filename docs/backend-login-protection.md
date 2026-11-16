# 登录失败防护与代理部署

2026-11-16，工作包 A。

登录成功的响应和刷新、退出契约保持原样。错误沿用 `{code, message, detail}`：凭据错误、账号不存在或停用统一 HTTP 401 / `AUTH_BAD_CREDENTIALS`；拒绝准入返回 HTTP 429 / `AUTH_LOGIN_THROTTLED`，附正整数秒 `Retry-After`，前端现有通用错误提示直接显示 message，不触发刷新重试。

默认同一 IP + 用户名在 600 秒窗口内连续失败 5 次，后续拒绝 900 秒；同一 IP 跨用户名失败累计 30 次也拒绝 900 秒。成功清除组合计数，保留 IP 失败计数。在途同组合请求拒绝并建议 1 秒后重试；不同组合的在途数计入 IP 准入预算，避免并发认证绕过阈值。数据库失败和异常均释放在途记录，密码校验在全局锁外执行。

环境配置（均要求正整数，进程启动时读取）：

| 环境变量 | 默认值 |
| --- | --- |
| LOGIN_THROTTLE_PAIR_LIMIT | 5 |
| LOGIN_THROTTLE_IP_LIMIT | 30 |
| LOGIN_THROTTLE_WINDOW_SECONDS | 600 |
| LOGIN_THROTTLE_COOLDOWN_SECONDS | 900 |
| LOGIN_THROTTLE_CAPACITY | 10000 |

IP 与组合表分别限制为 capacity 条。仅回收到期且没有在途认证的记录，不淘汰仍有效的失败计数或封禁记录。容量满拒绝新键，建议 60 秒后重试；诊断日志每分钟最多一次。失败计数只在内存中保存，使用单调时钟，不受数据库事务回滚影响。失败审计不保存用户名、密码、令牌、Cookie 或 User-Agent；审计无法持久化时写安全日志，继续返回认证失败。此机制要求一个 uvicorn 进程和一个容器副本；重启会清空记录，不提供持久封禁。

容器内 nginx 将 X-Forwarded-For 覆盖为 TCP 对端 `$remote_addr`，uvicorn 仅监听回环 127.0.0.1:8000，显式仅信任 127.0.0.1 的代理头，应用只读取 request.client.host。禁止设置 forwarded-allow-ips='*' 或直接使用客户端头作为计数键。

若 nginx 前面还有负载均衡/反向代理，必须按实际拓扑配置 nginx real_ip：用 set_real_ip_from 列出可信代理地址段、real_ip_header X-Forwarded-For、real_ip_recursive on；代理必须清理或追加真实对端，禁止信任任意网段。未配置时 nginx 看到的是上级代理地址，会将其用户归到同一 IP。仓库默认面向直接访问 nginx，不猜测部署者的可信网段。

本地回归覆盖实际认证路由和 uvicorn 代理中间件；未启动真实 nginx 容器，线上代理链与真实服务联调尚未验证。回滚需恢复 A 的代码、配置和代理变更，无数据库迁移。未提交或推送。
