"""Read detached account settings, then perform preview IO through the gateway."""
from copy import deepcopy
from types import MappingProxyType

from sqlalchemy.orm import Session

from app.core.errors import ApiError, bad_request, not_found
from app.domain.ports.share_preview import SharePreviewRead
from app.infrastructure.drive.adapter_registry import AdapterRegistry
from app.infrastructure.drive.factory import AdapterFactory
from app.infrastructure.drive.gateway import DriveGatewayError, gateway_for_adapter
from app.infrastructure.persistence.repositories import drive_accounts


class SharePreviewReader:
    def __init__(self, *, drive_type: str, account_name: str, cookie: str, config: dict):
        self.drive_type, self.account_name = drive_type, account_name
        self._cookie, self._config = cookie, deepcopy(config)

    def _adapter(self, *, no_login: bool):
        try:
            adapter = AdapterFactory.create_adapter(self.drive_type, self._cookie, config=deepcopy(self._config),
                                                    account_name=self.account_name, no_login=no_login)
        except Exception:
            raise bad_request('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号不可用，请检查账号状态') from None
        if adapter is None:
            raise bad_request('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号不可用，请检查账号状态')
        return adapter

    def read(self, shareurl: str, folder_id: str | None, destination: str) -> SharePreviewRead:
        try:
            source = gateway_for_adapter(self._adapter(no_login=True), drive_type=self.drive_type)
            result = source.read_share_preview(shareurl, folder_id)
        except ApiError:
            raise
        except DriveGatewayError as exc:
            codes = {'preview-url': ('TASK_SHAREURL_INVALID', '无法解析分享链接'),
                     'preview-token': ('TASK_SHARE_TOKEN_FAILED', '获取分享 token 失败')}
            code, message = codes.get(exc.kind, ('TASK_SHARE_DETAIL_FAILED', '读取分享目录失败，请重试'))
            raise bad_request(code, message) from None
        except Exception:
            raise bad_request('TASK_SHARE_DETAIL_FAILED', '读取分享目录失败，请重试') from None
        names: tuple[str, ...] = ()
        if destination.strip().rstrip('/'):
            try:
                adapter = self._adapter(no_login=False)
                if not adapter.ensure_initialized(ttl_seconds=AdapterFactory.init_ttl_seconds()):
                    raise bad_request('DRIVE_ACCOUNT_UNAVAILABLE', '网盘账号登录失败，请检查账号状态')
                names = gateway_for_adapter(adapter, drive_type=self.drive_type).preview_destination_names(destination)
            except ApiError:
                raise
            except Exception:
                raise bad_request('TASK_SHARE_DETAIL_FAILED', '读取目标目录失败，请重试') from None
        return SharePreviewRead(result.share_id, result.folder_id, result.author,
                                tuple(MappingProxyType(deepcopy(dict(item))) for item in result.items), names)


def prepare_share_preview_reader(db: Session, shareurl: str, account_name: str | None) -> SharePreviewReader:
    drive_type = AdapterRegistry.detect_drive_type(shareurl)
    if not drive_type:
        raise bad_request('TASK_SHAREURL_INVALID', '无法识别的网盘分享链接')
    if account_name:
        row = drive_accounts.find_active_by_name(db, name=account_name) or drive_accounts.find_enabled_by_name(db, name=account_name)
    else:
        row = drive_accounts.find_active_by_type(db, drive_type=drive_type) or drive_accounts.find_enabled_by_type(db, drive_type=drive_type)
    if row is None:
        raise not_found('DRIVE_ACCOUNT_NOT_FOUND', '指定账号不存在或不可用')
    if row.drive_type != drive_type:
        raise bad_request('TASK_SHAREURL_INVALID', '分享链接与指定账号的网盘类型不一致')
    config = AdapterRegistry.parse_config_json(row.drive_type, row.config_json, row.cookie)
    return SharePreviewReader(drive_type=drive_type, account_name=row.name,
                              cookie=AdapterRegistry.serialize_config(drive_type, config), config=config)
