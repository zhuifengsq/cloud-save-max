"""领域层（domain）：纯 Python，零框架依赖。

目标架构的稳定接口，不急于套用：当前只放端口协议（ports/），真实实现仍留在
现有 models/db/extensions/services 中，等待 S3+ 分批迁移（见
docs/architecture-evolution-plan.md 附录 A）。

依赖规则（由 scripts/check_layering.py 强制）：domain 只允许 import domain 与
标准库；禁止 FastAPI、SQLAlchemy、requests 及任何 IO。
"""
