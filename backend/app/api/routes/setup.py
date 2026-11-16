from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.application.container import get_db
from app.schemas.auth import LoginOut, MeOut
from app.core.settings import settings
from app.schemas.setup import SetupAdminIn, SetupStatusOut
from app.application.setup import is_initialized
from app.application.use_cases.initialize_admin import initialize_admin

router = APIRouter()


@router.get("/status", response_model=SetupStatusOut)
def get_status(db: Session = Depends(get_db)):
    return SetupStatusOut(initialized=is_initialized(db))


@router.post("/admin", response_model=LoginOut)
def post_admin(request: Request, response: Response, payload: SetupAdminIn, db: Session = Depends(get_db)):
    admin = initialize_admin(
        db, username=payload.username, email=str(payload.email), password=payload.password,
        request=request, response=response,
    )
    return LoginOut(
        access_token=admin.access_token, expires_in=settings.access_token_expires_seconds,
        user=MeOut(id=admin.id, username=admin.username, email=admin.email,
                   roles=admin.roles, permissions=admin.permissions),
    )
