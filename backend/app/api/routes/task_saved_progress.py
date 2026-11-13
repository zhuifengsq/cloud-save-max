from dataclasses import asdict

from fastapi import APIRouter, Depends, Path, Request
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, get_current_user, require_permissions
from app.application.container import correct_saved_progress_use_case, get_db
from app.core.permissions import TASK_WRITE
from app.schemas.saved_progress import SavedProgressCorrectionOut

router = APIRouter()


@router.post('/{task_id}/saved-progress/correct', response_model=SavedProgressCorrectionOut,
             dependencies=[Depends(require_permissions(TASK_WRITE))])
def correct_task_saved_progress(
    request: Request,
    task_id: int = Path(..., gt=0),
    current: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SavedProgressCorrectionOut:
    result = correct_saved_progress_use_case(db).execute(task_id, actor_user_id=current.user.id,
        ip=request.client.host if request.client else None, user_agent=request.headers.get('user-agent'))
    return SavedProgressCorrectionOut(**asdict(result))
