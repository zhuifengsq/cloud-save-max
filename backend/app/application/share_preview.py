"""Preview orchestration shared by Web and Telegram; database reads precede IO."""
from __future__ import annotations
from dataclasses import fields
from sqlalchemy.orm import Session
from app.application.container import share_preview_reader, share_preview_renamer, share_preview_min_bytes
from app.application.dto.task_browse import SharePreviewIn, SharePreviewOut
from app.application.drive_payload import bool_is_dir
from app.application.magic_regex import get_enabled_magic_regex_map
from app.application.share_preview_plan import PreviewPlanOptions, plan_preview_items
from app.core.runtime.tx import end_read_tx
from app.domain.ports.share_preview import PreviewRenamerPort, SharePreviewReaderPort


def preview_share(payload: SharePreviewIn, db: Session) -> SharePreviewOut:
    if db.new or db.dirty or db.deleted:
        raise RuntimeError('Preview requires a read-only session')
    reader: SharePreviewReaderPort = share_preview_reader(db, payload.shareurl, payload.account_name)
    magic_rules = get_enabled_magic_regex_map(db)
    # No TMDB request or settings creation is needed: these values never affected preview.
    end_read_tx(db)
    result = reader.read(payload.shareurl, payload.pdir_fid, str(payload.savepath or ''))
    renamer: PreviewRenamerPort = share_preview_renamer(magic_rules, str(payload.taskname or ''))
    options = PreviewPlanOptions(**{field.name: getattr(payload, field.name) for field in fields(PreviewPlanOptions)})
    minimum = share_preview_min_bytes(str(payload.min_size or ''))
    raw_items = result.items
    if payload.folders_only:
        raw_items = tuple(item for item in raw_items if bool_is_dir(item))
    items = plan_preview_items(options, raw_items, result.destination_names, renamer, min_bytes=minimum)
    return SharePreviewOut(drive_type=reader.drive_type, suggested_account_name=reader.account_name,
                           pwd_id=result.share_id, pdir_fid=result.folder_id,
                           share_author_name=result.author, items=items)
