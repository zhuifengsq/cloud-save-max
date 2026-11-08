from __future__ import annotations

import json
import os
from pathlib import Path
import secrets

from pydantic import AnyUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


def _secrets_file_path() -> Path:
    backend_dir = Path(__file__).resolve().parents[2]
    data_dir = backend_dir / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    return data_dir / "secrets.json"


def _load_or_init_secrets() -> dict[str, str]:
    path = _secrets_file_path()
    data: dict[str, str] = {}

    if path.exists():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, str):
                        data[k] = v
        except Exception:
            data = {}

    changed = False

    if not data.get("jwt_secret_key"):
        data["jwt_secret_key"] = secrets.token_urlsafe(48)
        changed = True

    if not data.get("refresh_token_pepper"):
        data["refresh_token_pepper"] = secrets.token_urlsafe(48)
        changed = True

    if changed:
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)

    return data


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "casx-api"
    environment: str = "dev"

    scheduler_enabled: bool = True

    api_prefix: str = "/api"

    cors_origins: list[AnyUrl] = []

    media_proxy_image_cache_enabled: bool = True
    media_proxy_image_cache_ttl_seconds: int = 86400
    media_proxy_image_cache_max_file_bytes: int = 10 * 1024 * 1024
    media_proxy_image_cache_max_total_bytes: int = 512 * 1024 * 1024
    media_proxy_image_cache_dir: str | None = None

    tasks_share_preview_batch_cache_ttl_seconds: int = 300
    tasks_share_preview_batch_cache_max_entries: int = 2000
    tasks_share_preview_batch_db_cache_ttl_seconds: int = 6 * 60 * 60
    tasks_share_preview_batch_db_cache_retention_seconds: int = 7 * 24 * 60 * 60
    # 每个任务保留的执行记录条数（超出后自动删除最旧的）
    task_execution_history_limit: int = 20
    # 慢请求日志阈值（毫秒）：超过就记一条 WARNING，带 SQL 条数与外部 HTTP 耗时；<=0 关闭
    slow_request_ms: int = 1000
    # /api/metrics 的访问令牌；配置后只认 token，未配置则要求登录且具备 audit:read
    metrics_token: str = ""
    # 用户角色/权限查询的进程内缓存 TTL（秒）；<=0 关闭。用户/角色/权限变更时会主动失效
    permission_cache_ttl_seconds: int = 30

    # ---- 重构灰度开关：置 False 即回退到旧实现（平滑过渡，见路线图 S1/S2）----
    # 统一任务执行入口（RunTaskUseCase）
    use_unified_run_use_case: bool = True
    # 通知走单消费者队列（不再每个渠道起线程）
    use_notify_queue: bool = True
    # 素材/网盘 key 级锁使用 LRU 上限（防止锁表无限增长）
    use_keyed_locks: bool = True

    # 追剧任务引流文件预览令牌有效期（秒）
    drama_lead_in_preview_token_ttl_seconds: int = 600

    drama_runtime_retry_max_attempts: int = 3
    drama_runtime_retry_backoff_seconds: float = 1.0
    drama_runtime_retry_max_backoff_seconds: float = 8.0
    drama_runtime_retry_jitter_ratio: float = 0.2

    jwt_secret_key: str | None = None
    jwt_algorithm: str = "HS256"
    access_token_expires_seconds: int = 2 * 60 * 60

    refresh_token_expires_seconds: int = 14 * 24 * 60 * 60
    refresh_token_cookie_name: str = "refresh_token"
    refresh_token_cookie_secure: bool = False
    refresh_token_cookie_samesite: str = "lax"
    refresh_token_cookie_domain: str | None = None

    refresh_token_pepper: str | None = None

    @property
    def database_url(self) -> str:
        if os.getenv("XXM_TESTING", "").strip() == "1":
            override = os.getenv("DATABASE_URL", "").strip()
            if override:
                return override
        return "sqlite:///./data/app.db"

    def model_post_init(self, __context) -> None:
        if os.getenv("XXM_TESTING", "").strip() == "1":
            self.jwt_secret_key = self.jwt_secret_key or secrets.token_urlsafe(48)
            self.refresh_token_pepper = self.refresh_token_pepper or secrets.token_urlsafe(48)
            return

        secrets_data = _load_or_init_secrets()

        jwt = (self.jwt_secret_key or "").strip()
        pepper = (self.refresh_token_pepper or "").strip()

        if not jwt:
            self.jwt_secret_key = secrets_data["jwt_secret_key"]
        else:
            secrets_data["jwt_secret_key"] = jwt

        if not pepper:
            self.refresh_token_pepper = secrets_data["refresh_token_pepper"]
        else:
            secrets_data["refresh_token_pepper"] = pepper

        path = _secrets_file_path()
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(secrets_data, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except Exception:
            pass
        tmp.replace(path)


settings = Settings()
