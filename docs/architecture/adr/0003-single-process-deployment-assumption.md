# ADR 0003：单进程部署假设（硬约束，不是性能偏好）

- 状态：已接受（Accepted，2026-11-09）
- 决策者：架构组
- 相关：方案 `docs/architecture-evolution-plan.md` §8「依赖冻结的取舍」、风险 R6
- 影响文件：`Dockerfile`、`backend/app/main.py`、`backend/app/core/settings.py`

## 背景

本轮重构的硬约束是「不能改变后端和前端依赖」（`requirements.txt` / `package.json` 不增删改），
因此 Redis / Celery / 分布式锁 / DI 框架等跨进程设施一律不可用。所有并发控制都落在**进程内原语**上：

| 进程内状态 | 位置 | 违反单进程后的后果 |
| --- | --- | --- |
| 权限缓存（TTL 30s，dict + 锁） | `backend/app/application/auth_context.py:31-47` | 多 worker 间权限不一致，最长 30s 内越权/误拒 |
| 适配器实例缓存（LRU 64 + TTL 180s + `close()`） | `backend/app/infrastructure/drive/factory.py` | 每个 worker 各自登录网盘，实例数 ×N，触发网盘风控 |
| 账号快照缓存 `TtlCache(8)` | `backend/app/infrastructure/drive/account_manager.py:19`（实现在 `core/runtime/ttl_cache.py`） | 每个 worker 各自全表读账号，写后 invalidate 只对本地生效 |
| 令牌刷新 single-flight | `backend/app/core/runtime/token_manager.py` | 多 worker 同时刷新同一账号令牌，旧令牌把新令牌刷废 |
| 任务准入 `TaskAdmission(max_active=4)` + `_task_ids` | `backend/app/infrastructure/scheduling/task_admission.py:64` | 同一任务被多个 worker 同时执行（转存重复），总并发 ×N |
| 按 key 锁 `LRUKeyedLocks`（引流 / 适配器构造） | `backend/app/core/runtime/keyed_locks.py` | 同一素材/目录被并发写，幂等保护失效 |
| 通知队列（`Queue(1000)` + 单消费者 daemon 线程） | `backend/app/application/notifications/queue.py` | 每 worker 一条消费者，同一通知重复发送 |
| TMDB 刷新任务集 + 写槽 `BoundedSemaphore(2)` | `backend/app/application/tmdb_cache.py:32-36` | 刷新重复触发；写槽 ×N 反而放大 SQLite 写竞争 |
| APScheduler `BackgroundScheduler` | `backend/app/infrastructure/scheduling/task_scheduler.py:44` | 定时任务每个 worker 各跑一遍（重复转存） |
| Telegram 轮询线程 + `last_update_id` | `backend/app/infrastructure/notifications/telegram_bot_manager.py:86-125` | 多进程抢同一 bot 的 `get_updates`，update 丢失或重复处理 |
| 扫码登录会话 `_SESSIONS` | `backend/app/application/drive_account_auth_sessions.py`（TTL 600s） | 扫码结果落在另一个 worker，登录超时 |
| 启动期迁移 + 备份 | `backend/app/main.py:70-71` | 每个 worker 各跑一次 `alembic upgrade head`，并发迁移 |

## 决策

**支持且仅支持一种部署形态：1 个 uvicorn 进程、1 个容器副本。**

证据（现状即如此）：

- `Dockerfile:53-55`：supervisor 只启动一条
  `command=python -m uvicorn app.main:app --host 0.0.0.0 --port 8000`，**没有 `--workers`**；nginx 与 uvicorn 同容器。
- `backend/app/main.py:68-94`：lifespan 内依次做「备份 → 迁移 → 权限初始化 → 清理旧执行记录 →
  启动 APScheduler → 启动 Telegram 轮询」，每一件都只能发生一次。
- `backend/app/core/settings.py:61`：`scheduler_enabled` 是进程级开关，没有 leader 选举。

## 后果

- 横向扩容 / 加 `--workers` **不是「性能调优」，而是正确性事故**：重复迁移、重复调度、重复轮询、
  同一任务并发执行、通知重复发送、权限缓存漂移。
- 单进程的吞吐上限是真实的：SSE 流、长任务线程、连接池（5+10）都在同一进程内竞争。
  优化手段只能是「减少单次占用」（已做的 P0–P5/S0–S5）而不是「加进程」。
- 该假设必须在三处可见，避免未来有人「顺手加个 --workers」：
  1. `Dockerfile` 的 uvicorn 命令行（保持无 `--workers`）与本 ADR；
  2. `docs/architecture/README.md` 的部署约束段；
  3. 方案 R6 风险行。

## 如果将来必须扩容（不在本轮范围）

优先选**不需要新依赖**的方案：**单写者模式**——
web 侧可多副本（只读/写库 + 无状态接口），但「迁移 + 调度 + 执行 + Telegram 轮询 + 通知消费」只允许一个实例做，
用环境变量（如 `ROLE=web` / `ROLE=worker`）在 `main.py` 的 lifespan 里裁剪启动项。
这条路径不引入 Redis/Celery，但需要把「进程内单例」的能力边界（准入、锁、队列）重新定义为「单写者进程内有效」，
并解决 `/api/tasks/{id}/run` 跨实例路由问题（要么粘性路由，要么允许任意实例建任务、只由写者执行）。

需要真正多写者（多实例同时执行）时，才必须引入 Redis（分布式锁/队列）或 Postgres（`SELECT ... FOR UPDATE` / advisory lock）——
这属于依赖变更，需重新走架构评审。

## 重新评估的触发条件

1. 用户要求高可用（单容器重启即中断）或并发量超出单进程上限；
2. 部署环境强制多副本（K8s 默认滚动更新即多副本）；
3. 网盘侧需要「多账号并行」超出单进程 4 个并发槽的实际承载力。
