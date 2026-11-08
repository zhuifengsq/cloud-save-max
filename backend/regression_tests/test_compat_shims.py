"""S4-6 物理搬目录后的兼容层护栏：每个旧路径垫片都必须指向存在的新模块。"""
from __future__ import annotations

import importlib

from app._compat import iter_legacy_shims


SHIMS = iter_legacy_shims()


def test_shim_inventory_is_not_empty() -> None:
    assert len(SHIMS) >= 80, f"垫片数量异常：{len(SHIMS)}"


def test_every_shim_targets_an_importable_module() -> None:
    broken: list[str] = []
    for old, new in SHIMS:
        try:
            importlib.import_module(new)
        except Exception as exc:  # noqa: BLE001 - 收集全部问题再报
            broken.append(f"{old} -> {new}: {exc!r}")
    assert broken == [], "以下垫片指向的新模块无法 import：" + repr(broken)


def test_every_old_path_still_imports() -> None:
    broken: list[str] = []
    for old, _new in SHIMS:
        try:
            importlib.import_module(old)
        except Exception as exc:  # noqa: BLE001
            broken.append(f"{old}: {exc!r}")
    assert broken == [], "以下旧路径无法 import：" + repr(broken)


def test_shims_do_not_duplicate_new_paths() -> None:
    targets = [new for _old, new in SHIMS]
    duplicates = sorted({t for t in targets if targets.count(t) > 1})
    assert duplicates == [], f"多个垫片指向同一新模块：{duplicates}"


def test_known_identity_examples() -> None:
    from app.application.tasks import get_task as new_get_task
    from app.infrastructure.drive.factory import AdapterFactory as NewFactory
    from app.infrastructure.execution.task_executor import TaskExecutor as NewExecutor
    from app.infrastructure.http.tmdb_client import TMDBClient as NewTMDB
    from app.services.tasks import get_task as old_get_task
    from app.extensions.adapters.adapter_factory import AdapterFactory as OldFactory
    from app.extensions.runtime.task_executor import TaskExecutor as OldExecutor
    from app.thirdparty.tmdb_client import TMDBClient as OldTMDB

    assert old_get_task is new_get_task
    assert OldFactory is NewFactory
    assert OldExecutor is NewExecutor
    assert OldTMDB is NewTMDB
