# 工作包 G/H 交付记录

日期：2026-11-16。未commit/push，未改运行依赖或生产代码，未读真实数据库、secrets.json或凭据。

G已交付 [字段盘点与时间政策](backend-time-policy.md)，H已交付 [凭据保护与恢复设计](backend-credential-protection.md)。两者都是方案交付，不代表UTC或加密迁移已经实现。

## 实际验证

新增 `backend/regression_tests/test_auth_time_policy.py` 13项固定时钟现状测试：

- 同一时刻在UTC−5/UTC/UTC+8下的JWT iat一致、exp−iat为7200秒；直接调用现有create_access_token，不打印token。
- aware刷新令牌在上述三种模拟本地时区下，过期前1秒成功、等于/晚于过期拒绝；调用真实refresh_access_token，假仓储IO只隔离数据依赖。
- 独立真实SQLite往返确认DateTime(timezone=True)仍丢offset；原始来源不明时，现有normalize无法推断原zone，切换宿主zone有提前/延迟到期风险。

首次执行12 passed/1 failed是测试夹具遗漏users表导致ORM joined relationship查询失败，已改为只select所需expires_at列修正夹具；没有修改生产代码或放宽时间断言。

从backend目录的实际命令：

```powershell
$env:XXM_TESTING='1'
$env:DATABASE_URL='sqlite://'
$env:RUN_MIGRATIONS='0'
$env:SCHEDULER_ENABLED='0'
.\.venv\Scripts\python.exe -m pytest regression_tests/test_auth_time_policy.py -q --basetemp .tmp/quality-gh-time-fixed
```

输出 **13 passed in 1.34s**。没有修改宿主TZ，测试用临时时钟/固定offset；未声称已覆盖夏令时或已解决旧naive来源问题。全量回归、门禁与应用导入由主任务统一记录。

H源码盘点覆盖十家网盘default_config字段、账号CRUD/序列化、扫码/SMS/captcha、probe/sign-in、adapter凭据刷新及config_saver、账号与实例缓存、备份与配置加载、日志/错误/资料raw路径。设计明确DB-only泄漏与主机失陷的不同边界，提出keyring独立存储、AEAD/AAD、版本识别、双读单写、CAS、轮换、坏密文拒绝、旧程序不兼容及假凭据恢复矩阵。没有安装cryptography或编写假加密实现；恢复测试是待批准实施的要求，尚未运行。

## 归属与后续

本包不新增数据库提交点、不变更模型/迁移/schema或前端契约。可独立撤销两份方案、时间测试和本报告，不影响A～F实现。后续优先认证时间语义（refresh_tokens四字段与JWT），执行历史另批；未知来源refresh token建议失效重登，不批量猜测历史UTC偏移。凭据实施需先评审运行依赖、API回显兼容、密钥备份边界并完成假数据真实恢复演练，再单独批准线上迁移。
