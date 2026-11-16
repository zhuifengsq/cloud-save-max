# 代理信任与渐进静态检查补强

2026-11-16。未提交、推送、读取真实凭据或运行数据库。

## 代理部署行为

`default` 保留向uvicorn覆盖传递 `$remote_addr`，uvicorn仍只绑定127.0.0.1并只信任容器内nginx。新增容器Python入口：先从只读模板生成nginx配置、执行 `nginx -t`，通过后exec supervisord；配置错误时拒绝启动，不能悄悄退回错误IP桶。

默认不启用nginx real_ip，客户端自行发送X-Forwarded-For不会被采信。若明确存在上级代理，通过环境配置：

```text
TRUSTED_PROXY_CIDRS=172.20.0.5/32,2001:db8::/64
TRUSTED_PROXY_HEADER=X-Forwarded-For
```

地址是示例，应替换为nginx实际TCP对端的代理地址/最小网段。支持IPv4/IPv6逗号列表，最多64项；默认header为X-Forwarded-For，也可显式选择CF-Connecting-IP。拒绝0.0.0.0/0、::/0、通配符、非法地址、任意header及分号/换行配置注入；只给header而无可信来源也拒绝。

Cloudflare Tunnel场景应信任实际cloudflared连接来源（如专用Docker网络中该容器的固定/32地址），选择CF-Connecting-IP；不要把任意其他容器或直连用户当可信代理。部署者必须保证该来源的header由正确上游设置，并限制绕过Tunnel直连。配置代码不能自动猜测云隧道、NAT或CDN来源，也不自动下载第三方CIDR清单。

可信范围与递归取最后非可信地址的语义依据 [nginx realip官方文档](https://nginx.org/en/docs/http/ngx_http_realip_module.html)。所有进入应用的IP仍由nginx清理后传递，应用不直接读取CF头或客户端XFF。

新增实际TCP/uvicorn测试，启动独立回环监听端口验证：可信peer可传递最后非可信XFF地址，不可信peer的伪造头被忽略。此测试验证真实socket和uvicorn行为；配置生成测试验证CIDR/header/拒绝注入。**本机无docker/nginx命令，未运行真实nginx或Cloudflare Tunnel链路**；启动时nginx -t是部署运行保障，不能冒充已完成容器联调。

## 类型与规则范围

mypy strict从11个端口文件扩展到限流器、失败审计聚合器、纯分享计划、真实payload读取工具及容器入口；后者依赖的DTO也由mypy正常跟踪。配置不使用ignore_errors、忽略整个目录或无理由type ignore。隔离工具运行时用 `--python-executable .venv/Scripts/python.exe` 读取项目已安装pydantic类型；CI的标准环境直接 `python -m mypy --strict`读取files列表。

实际新增修复：payload从裸dict转为只读Mapping[str, object]，数值转换显式验证可转换类型；计划中间行用TypedDict表达字段和可选过滤标记，移除该链条的Any/裸dict以及未注解时间转换。失败审计通过调用方注入writer，避免类型检查被容器装配反向拉进整个应用。期间直接纳入旧audit模块暴露1134个存量错误，未以忽略方式放行，先调整装配边界再纳入。

Ruff从3条名称规则扩大为F6/F7/F82：阻止重复字典键、部分错误比较/解包/断言、语法和控制流错误、未定义名称等。全F探针发现116项（多为未使用/re-export与旧局部变量）；未进行全仓自动修复或把所有re-export误删，F401/F811/F841等没有被盲目开启。现在的规则范围仍是渐进门禁，不能声称全仓类型安全。

## 计划职责与证据

纯计划按“收集原始行、目录规则、文件过滤、冲突选择、输出映射”拆开，保留执行顺序与所有输出标记。编排15行，规则函数分别7/55/50/67/24/28行；行数只用于说明，不作为验收目标。100组固定随机种子的数据在拆分前后产生完全一致的model_dump结果。

局部验收（XXM_TESTING=1，DATABASE_URL=sqlite://）：

```text
python -m pytest regression_tests/test_proxy_configuration.py regression_tests/test_uvicorn_proxy_socket.py regression_tests/test_share_preview_boundaries.py tests/test_baidu_share_preview.py regression_tests/test_drive_share_contract.py regression_tests/test_transfer_contract.py -q --basetemp .tmp/proxy-static-final
76 passed in 6.95s

python -m mypy --python-executable .venv/Scripts/python.exe
Success: no issues found in 16 source files

python -m ruff check app
All checks passed!
```

配置测试初次收集因模块尚未实现失败；随后默认编码读取中文模板造成3项夹具错误，修成显式UTF-8，未将这些夹具问题视为生产缺陷。扩大类型检查先报告24个实际类型错误，修后通过。git diff --check通过。类型覆盖显式文件当次约1030行，仍远未覆盖全仓约4.7万行；未来应沿下一条真实用例调用链扩大，而非靠注解Any数量达标。

回滚代理变更应同时恢复Docker CMD和default模板，不能只回退其中一项；类型/纯计划重构与规则配置应一起评审回滚，无数据库模型或运行依赖变更。
