# 部署健康探针

- 就绪检查使用 `GET /api/health`：数据库可用时返回 200；数据库探测失败返回 503，响应保留原有字段，并设置 `status=degraded`、`db_connected=false`。探测只执行 `SELECT 1`，不写业务数据。
- 存活检查使用 `GET /api/health/live`：HTTP 进程可响应时返回 200 和 `{"status":"ok"}`，不访问数据库、网盘或 Telegram。

将容器或编排平台的存活探针指向 `/api/health/live`，将就绪探针指向 `/api/health`。数据库暂时不可用时，就绪检查会失败，存活检查仍成功，从而避免因数据库故障重复重启应用。

本项目 Dockerfile 没有内置 HEALTHCHECK；请在部署平台中分别配置这两类探针。
