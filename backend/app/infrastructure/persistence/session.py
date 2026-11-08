import logging
import time
from contextlib import contextmanager
from typing import Callable, Iterator, TypeVar

from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import sessionmaker

from app.core.settings import settings

# end_read_tx 的实现放在 platform 层（app.core.runtime.tx），这里重导出以保持
# `from app.infrastructure.persistence.session import end_read_tx` 这一习惯用法的兼容性。
from app.core.runtime.tx import end_read_tx  # noqa: F401  (re-export)

T = TypeVar("T")

logger = logging.getLogger(__name__)


connect_args: dict[str, object] = {}
engine_kwargs: dict[str, object] = {"future": True}
if settings.database_url.startswith("sqlite"):
    connect_args = {"check_same_thread": False, "timeout": 30}
    # SQLite 建连接的成本不低（Windows 本机实测：NullPool 每请求约 3.4ms，
    # 复用连接后约 0.15ms），这里默认复用连接。连接归还时 SQLAlchemy 会回滚，
    # 下面 connect 事件里的 WAL/busy_timeout 等 PRAGMA 对新连接生效后随连接保留；
    # 线程间复用由 check_same_thread=False 允许。
    # 内存库（sqlite:// 或 :memory:）不适用：每个新连接会各自拥有独立的库。
    if ":memory:" not in settings.database_url and settings.database_url.rstrip("/") != "sqlite:":
        engine_kwargs["poolclass"] = QueuePool
        engine_kwargs["pool_size"] = 5
        engine_kwargs["max_overflow"] = 10
        engine_kwargs["pool_timeout"] = 30
        engine_kwargs["pool_recycle"] = 3600

engine = create_engine(settings.database_url, connect_args=connect_args, **engine_kwargs)

if settings.database_url.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.execute("PRAGMA busy_timeout=30000")
        # 排序/临时 b-tree 一律放内存：进程的 TEMP 目录不可写时，
        # SQLite 建临时文件失败只会报 "unable to open database file"，
        # 表现为列表接口（ORDER BY ... LIMIT）莫名其妙 500。
        cur.execute("PRAGMA temp_store=MEMORY")
        # SQLite 默认不校验外键（PRAGMA foreign_keys 默认 OFF），ORM 里的
        # ForeignKey 只是声明；打开后 INSERT/UPDATE 才会真正拒绝悬空引用。
        # 连接级 PRAGMA，必须在每条新连接上设置（这里是 connect 事件）。
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def is_sqlite_locked_error(exc: Exception) -> bool:
    return settings.database_url.startswith("sqlite") and "database is locked" in str(exc).lower()


def _rollback_after_failed_commit(fn: Callable[[], object]) -> None:
    """commit 失败后 Session 处于「待回滚」状态，不 rollback 直接重试 commit 只会继续失败。

    只对「某个 Session 的 commit 绑定方法」自动回滚（fn.__self__ 有 rollback），
    其它可调用对象（仓储方法、flush 等）一律不碰事务状态——flush 后 rollback 会
    丢掉待提交的变更，属于错误语义。
    """
    owner = getattr(fn, "__self__", None)
    rollback = getattr(owner, "rollback", None)
    if getattr(fn, "__name__", "") != "commit" or owner is None or not callable(rollback):
        return
    try:
        rollback()
    except Exception:  # 回滚失败不能让原始锁冲突异常被替换
        logger.warning("重试 commit 前回滚失败（将按原样重试）", exc_info=True)


def retry_on_locked(
    fn: Callable[[], T],
    attempts: int = 5,
    *,
    base_delay: float = 0.2,
    max_delay: float = 2.0,
    on_retry: Callable[[], None] | None = None,
) -> T:
    """执行 fn()，仅对 SQLite "database is locked" 做指数退避重试。

    SQLite 单写者模型下写冲突是常态而不是异常：busy_timeout=30000 只能覆盖
    "等待页锁"的场景，BEGIN IMMEDIATE 竞争、WAL checkpoint 与写事务交错时仍会
    抛 OperationalError。这里给 UnitOfWork/仓储层一个统一的重试壳，避免每个调用
    点各写一套（现状：task_scheduler.py 三处、telegram_bot_manager.py 一处）。

    约定：最后一次失败原样抛出；非锁冲突异常不重试、立即抛出。

    on_retry：每次重试前的清理钩子（如 db.rollback）。不给时，如果被包装的正是
    某个 Session 的 commit，会自动回滚一次——否则失败的事务状态会让后续重试必然失败。
    """
    last_exc: Exception | None = None
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            return fn()
        except Exception as exc:
            if not is_sqlite_locked_error(exc) or attempt >= total:
                raise
            last_exc = exc
            if on_retry is not None:
                on_retry()
            else:
                _rollback_after_failed_commit(fn)
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            time.sleep(delay)
    raise last_exc  # 理论不可达


def commit_with_locked_retry(
    db,
    attempts: int = 3,
    *,
    base_delay: float = 0.2,
    max_delay: float = 2.0,
) -> None:
    """提交 Session 事务；锁冲突时只在「事务仍存活」的前提下重试 commit。

    为什么不能无条件重试：flush 阶段的锁冲突会让 SQLAlchemy 回滚整个事务
    （数据已丢），此时再 commit 只会提交一个空事务并成功返回，表现为
    接口 200 + 数据静默丢失。实测复现见
    regression_tests/test_unit_of_work.py::test_lock_conflict_is_not_masked。

    因此重试前必须确认 Session 仍在事务中（db.in_transaction() 为 True）；
    一旦事务已被回滚，立刻把原始锁冲突异常抛给上层（接口 5xx），由调用方
    决定是否重跑整个操作（重跑必须以「整个用例」为单位，见 retry_on_locked）。
    """
    total = max(1, int(attempts))
    for attempt in range(1, total + 1):
        try:
            db.commit()
            return
        except Exception as exc:
            if not is_sqlite_locked_error(exc) or attempt >= total:
                raise
            # 事务已被回滚（in_transaction=False）或已标记「必须回滚」
            # （is_active=False，flush 失败后的状态）：再 commit 只会提交空事务
            # 或抛 PendingRollbackError，两种都不是重试，必须抛原始锁冲突。
            tx_alive = getattr(db, "in_transaction", None)
            if callable(tx_alive) and not tx_alive():
                raise
            # is_active 是属性（bool）而不是方法，两种形态都要兼容。
            active = getattr(db, "is_active", None)
            if active is not None:
                if callable(active):
                    active = active()
                if not active:
                    raise
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            time.sleep(delay)


def commit(db, attempts: int = 3) -> None:
    """提交当前事务（带锁冲突安全重试）。

    给「提交之后还要发副作用或抛异常」的流程用（例如先写失败审计再 raise
    业务错误）；纯线性写路径优先用 transaction() 明确事务边界。
    """
    commit_with_locked_retry(db, attempts=attempts)


@contextmanager
def transaction(db, attempts: int = 3) -> Iterator[object]:
    """事务边界：正常结束提交，异常回滚后原样抛出（一个用例一个事务）。

    提交走 commit_with_locked_retry（只重试 commit，且事务必须仍存活）；
    不包裹任何外部副作用；提交成功后的异常仍会 rollback（此时无待提交变更，
    是安全空操作）。
    """
    try:
        yield db
        commit_with_locked_retry(db, attempts=attempts)
    except BaseException:
        try:
            db.rollback()
        except Exception:  # 回滚失败不能让原始异常被替换
            logger.warning("事务回滚失败（原始异常将照常抛出）", exc_info=True)
        raise



def get_db():
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_db_session():
    """获取数据库会话上下文管理器"""
    from contextlib import contextmanager
    
    @contextmanager
    def _get_session():
        db = SessionLocal()
        try:
            yield db
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()
    
    return _get_session()
