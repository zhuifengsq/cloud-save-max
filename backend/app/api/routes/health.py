import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.application.container import get_db


def _git_describe() -> str | None:
    """Try to get version from git tags (e.g. v26.7.7)."""
    try:
        repo_root = Path(__file__).resolve().parents[4]  # backend/app/api/routes/ -> project root
        result = subprocess.run(
            ["git", "describe", "--tags", "--always"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=5,
        )
        tag = result.stdout.strip()
        return tag or None
    except Exception:
        return None


def _git_sha() -> str | None:
    """Try to get short commit SHA from git."""
    try:
        repo_root = Path(__file__).resolve().parents[4]
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root,
            capture_output=True, text=True, timeout=5,
        )
        sha = result.stdout.strip()
        return sha or None
    except Exception:
        return None


router = APIRouter()
logger = logging.getLogger(__name__)


@router.get("/health/live")
def liveness():
    """HTTP process liveness; deliberately independent of external services."""
    return {"status": "ok"}


@router.get("/health")
def health(response: Response, db: Session = Depends(get_db)):
    db_connected = False
    try:
        db.execute(text("SELECT 1"))
        db_connected = True
    except Exception:
        db_connected = False
        response.status_code = 503
        # Exception text can contain connection credentials; keep this diagnostic safe.
        logger.warning("Database health probe failed")

    build_tag = (os.getenv("BUILD_TAG") or "").strip() or _git_describe() or "dev"
    build_sha = (os.getenv("BUILD_SHA") or "").strip() or _git_sha()

    return {
        "status": "ok" if db_connected else "degraded",
        "db_connected": db_connected,
        "time": datetime.now().isoformat(),
        "build_sha": build_sha,
        "build_tag": build_tag,
    }
