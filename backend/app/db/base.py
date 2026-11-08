from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass

# 注意：这里**不能** import app.models（会形成 db.base ↔ models 的 27 模块循环）。
# 需要注册全部模型时，由调用方显式 import app.models（Alembic env.py 已显式导入）。
