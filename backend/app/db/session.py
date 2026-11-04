from sqlalchemy import create_engine, event
from sqlalchemy.pool import QueuePool
from sqlalchemy.orm import sessionmaker

from app.core.settings import settings


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
        cur.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)


def is_sqlite_locked_error(exc: Exception) -> bool:
    return settings.database_url.startswith("sqlite") and "database is locked" in str(exc).lower()


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
