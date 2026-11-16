"""Exercise the real forum plugin through aggregation, with only HTTP replaced."""

from types import SimpleNamespace

import pytest
import requests

from app.application import resource_search, search_plugins
from app.core.errors import ApiError
from app.infrastructure.search.quark4k_plugin import Quark4KPlugin


def configure(monkeypatch, error, *, healthy=False):
    monkeypatch.setattr(
        Quark4KPlugin, "_fetch_page", lambda *a: (_ for _ in ()).throw(error)
    )
    discovered = [{"key": "quark4k", "plugin": Quark4KPlugin(), "status": "ready"}]
    if healthy:
        discovered.append(
            {
                "key": "healthy",
                "status": "ready",
                "plugin": SimpleNamespace(
                    key="healthy",
                    name="Healthy",
                    timeout_seconds=1,
                    search=lambda _: [
                        {"title": "movie", "url": "https://pan.quark.cn/s/fake"}
                    ],
                ),
            }
        )
    monkeypatch.setattr(
        search_plugins, "discover_search_plugins", lambda **kw: discovered
    )
    monkeypatch.setattr(
        search_plugins,
        "_ensure_rows",
        lambda *a: {
            "plugin:" + item["key"]: SimpleNamespace(enabled=True)
            for item in discovered
        },
    )


def test_real_plugin_network_failure_keeps_other_source_results(monkeypatch):
    configure(monkeypatch, requests.ConnectionError("private upstream"), healthy=True)
    result = search_plugins.search_enabled_plugins_result(None, "movie")
    assert (result.sources, result.succeeded, result.timed_out) == (2, 1, 0)
    assert [item["shareurl"] for item in result.items] == [
        "https://pan.quark.cn/s/fake"
    ]
    assert result.warnings == ["搜索插件暂不可用"]


@pytest.mark.parametrize(
    "error,status,code",
    [
        (requests.Timeout("private upstream"), 504, "SEARCH_UPSTREAM_TIMEOUT"),
        (requests.ConnectionError("private upstream"), 502, "SEARCH_UPSTREAM_FAILED"),
    ],
)
def test_real_plugin_failure_preserves_aggregate_http_contract(
    monkeypatch, error, status, code
):
    configure(monkeypatch, error)
    from app.application import api_search_sources
    from app.application.search_results import SearchBatch

    monkeypatch.setattr(
        api_search_sources,
        "search_enabled_api_sources_result",
        lambda *a, **kw: SearchBatch(),
    )
    monkeypatch.setattr(resource_search, "_update_search_cache_ttl", lambda: None)
    resource_search.cache_clear()
    with pytest.raises(ApiError) as caught:
        resource_search.fetch_task_suggestions_result(
            None, "movie", 0, force_refresh=True
        )
    assert (caught.value.http_status, caught.value.code) == (status, code)


def test_real_plugin_programming_error_remains_visible(monkeypatch):
    configure(monkeypatch, RuntimeError("bug"))
    with pytest.raises(RuntimeError):
        search_plugins.search_enabled_plugins_result(None, "movie")
