"""`DriveGatewayPort` 的生产实现：一层薄语义，把端口语义翻译成适配器方法。

设计边界（见 docs/architecture-evolution-plan.md §4.2 与 §10.14）：

- 端口只吸收「各家网盘响应结构不一样」这一层差异（fid/fid_token 字段名、目录判定、分享分类），
  不吸收执行器的编排（重试、任务轮询、已保存 fid 提取、入库）。那些留在 `drama_executor`，
  因为它们和「失败怎么算、要不要重试、重试几次」强相关，搬进端口只会把风险提前。
- 因此 `transfer()` 只覆盖「分享 → 直接保存到目标目录」这一步：适合做分享校验后的转存、
  或给新网盘做冒烟验证；主链路仍走执行器。
- 端口协议在 `app/domain/ports/drive.py`，本文件只是实现；业务层通过 `container.drive_gateway()` 拿实例，
  不直接 import 本模块（保持「application 不依赖 infrastructure」的方向）。
"""
from __future__ import annotations

import inspect
import logging
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.domain.ports.drive import ShareProbeResult

logger = logging.getLogger(__name__)

_BAIDU = "baidu"
_QUARK = "quark"


@dataclass(frozen=True)
class DriveRemoteEntry:
    """目录项只读视图（满足 `RemoteEntry` 协议：name / is_dir / size）。"""

    name: str
    is_dir: bool
    size: int


class DriveGatewayError(RuntimeError):
    """端口层统一异常：不把适配器的响应字典泄露给业务层。"""

    def __init__(self, message: str, *, kind: str = "unknown") -> None:
        super().__init__(message)
        self.kind = kind


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _items(response: Any) -> list[dict[str, Any]]:
    data = _as_dict(_as_dict(response).get("data"))
    raw = data.get("list")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def _get_name(item: Mapping[str, Any]) -> str:
    return str(
        item.get("file_name")
        or item.get("server_filename")
        or item.get("fileName")
        or item.get("name")
        or item.get("title")
        or item.get("fid")
        or item.get("fs_id")
        or ""
    )


def _get_fid(item: Mapping[str, Any]) -> str:
    return str(
        item.get("fid")
        or item.get("fs_id")
        or item.get("file_id")
        or item.get("id")
        or item.get("fileId")
        or ""
    )


def _get_fid_token(item: Mapping[str, Any]) -> str:
    value = item.get("fid_token") or item.get("share_fid_token") or item.get("token")
    return "" if value is None else str(value)


def _is_dir(item: Mapping[str, Any]) -> bool:
    """目录判定：各网盘字段名不同，这里按优先级归一（与执行器同一套判据）。"""
    if item.get("is_dir") is not None:
        return bool(item.get("is_dir"))
    if item.get("isdir") is not None:
        return str(item.get("isdir")) in ("1", "true", "True")
    if item.get("dir") is not None:
        return bool(item.get("dir"))
    kind = item.get("kind")
    if kind in ("folder", "dir", "directory"):
        return True
    if kind == "file":
        return False
    file_type = item.get("type")
    if file_type in ("folder", "dir"):
        return True
    if file_type == "file":
        return False
    if item.get("file_type") is not None:
        value = str(item.get("file_type"))
        if value in ("0", "dir", "folder"):
            return True
        if value in ("1", "file"):
            return False
    return False


def _size(item: Mapping[str, Any]) -> int:
    for key in ("size", "file_size", "bytes"):
        value = item.get(key)
        if value is None:
            continue
        try:
            return int(value)
        except (TypeError, ValueError):
            continue
    return 0


def _response_ok(response: Any) -> bool:
    """适配器响应是否成功：code/status 都兼容 int 与 str（各网盘写法不一致）。"""
    data = _as_dict(response)
    code = data.get("code")
    if code is not None and str(code) not in ("0", "200"):
        return False
    status = data.get("status")
    if status is not None and str(status) not in ("0", "200"):
        return False
    return True


class AdapterDriveGateway:
    """`DriveGatewayPort` 的实现：适配器进，领域语义出。"""

    def __init__(self, adapter: Any, *, drive_type: str | None = None) -> None:
        if adapter is None:
            raise ValueError("AdapterDriveGateway 需要适配器实例")
        self._adapter = adapter
        self.drive_type = str(drive_type or getattr(adapter, "DRIVE_TYPE", "") or "")

    # ---------------------------------------------------------------- 读
    def list_dir(self, path: str) -> Sequence[DriveRemoteEntry]:
        response = self._adapter.ls_dir(path)
        if not _response_ok(response):
            raise DriveGatewayError(str(_as_dict(response).get("message") or "列目录失败"))
        return [
            DriveRemoteEntry(name=_get_name(item), is_dir=_is_dir(item), size=_size(item))
            for item in _items(response)
        ]

    def probe_share(self, share_url: str, *, passcode: str = "") -> ShareProbeResult:
        """探测分享链接：取 stoken → 读目录 → 用适配器分类失败原因。"""
        pwd_id, url_passcode, folder_id, _ = self._adapter.extract_url(share_url)
        if not pwd_id:
            return ShareProbeResult(valid=False, kind="unknown", message="", stage="parse")
        token = self._get_stoken(pwd_id, str(passcode or url_passcode or ""))
        stoken = str(_as_dict(_as_dict(token).get("data")).get("stoken") or "")
        if not stoken:
            kind, message = self._classify(token)
            return ShareProbeResult(valid=False, kind=kind, message=message, stage="verify")
        detail = self._get_detail(pwd_id, stoken, str(folder_id or "0"))
        data = _as_dict(_as_dict(detail).get("data"))
        if str(_as_dict(detail).get("code")) == "0" and isinstance(data.get("list"), list):
            return ShareProbeResult(valid=True, kind="valid", message="", stage="read")
        kind, message = self._classify(detail)
        return ShareProbeResult(valid=False, kind=kind, message=message, stage="read")

    def check_share(self, share_url: str, *, passcode: str = "") -> bool:
        return self.probe_share(share_url, passcode=passcode).valid

    # ---------------------------------------------------------------- 写
    def create_dir(self, path: str) -> None:
        """建目录；「已存在」视为成功（端口约定幂等）。"""
        response = self._adapter.mkdir(path)
        if _response_ok(response):
            return
        message = str(_as_dict(response).get("message") or "")
        if "已存在" in message or "exist" in message.lower():
            return
        raise DriveGatewayError(message or "创建目录失败")

    def transfer(self, share_url: str, target_dir: str, *, passcode: str = "") -> Mapping[str, Any]:
        """把分享里的文件直接保存到目标目录（薄实现，不含重试与任务轮询）。"""
        pwd_id, url_passcode, folder_id, _ = self._adapter.extract_url(share_url)
        if not pwd_id:
            raise DriveGatewayError("无法解析分享链接", kind="invalid")
        token = self._get_stoken(pwd_id, str(passcode or url_passcode or ""))
        stoken = str(_as_dict(_as_dict(token).get("data")).get("stoken") or "")
        if not stoken:
            kind, message = self._classify(token)
            raise DriveGatewayError(message or "获取分享令牌失败", kind=kind)
        detail = self._get_detail(pwd_id, stoken, str(folder_id or ""))
        items = _items(detail)
        if not items and not _response_ok(detail):
            kind, message = self._classify(detail)
            raise DriveGatewayError(message or "读取分享目录失败", kind=kind)
        fid_list: list[str] = []
        fid_token_list: list[str] = []
        file_names: list[str] = []
        for item in items:
            fid = _get_fid(item)
            if not fid:
                continue
            fid_token = _get_fid_token(item)
            if not fid_token:
                raise DriveGatewayError("分享列表缺少 fid_token，无法转存", kind="invalid")
            fid_list.append(fid)
            fid_token_list.append(fid_token)
            file_names.append(_get_name(item))
        if not fid_list:
            return {"success": False, "task_id": "", "item_count": 0, "message": "分享中没有可转存文件"}
        response = self._save_file(fid_list, fid_token_list, target_dir, pwd_id, stoken, file_names)
        data = _as_dict(_as_dict(response).get("data"))
        return {
            "success": _response_ok(response),
            "task_id": str(data.get("task_id") or data.get("taskId") or ""),
            "item_count": len(fid_list),
            "message": str(_as_dict(response).get("message") or ""),
        }

    def close(self) -> None:
        closer = getattr(self._adapter, "close", None)
        if callable(closer):
            closer()

    # ---------------------------------------------------------------- 适配器差异
    def _get_stoken(self, pwd_id: str, passcode: str) -> Any:
        # 百度需要 check_errno 才能在失败响应里拿到错误码（与执行前探测保持一致）
        if self.drive_type == _BAIDU:
            return self._adapter.get_stoken(pwd_id, passcode, check_errno=True)
        return self._adapter.get_stoken(pwd_id, passcode)

    def _get_detail(self, pwd_id: str, stoken: str, folder_id: str) -> Any:
        # 夸克详情接口默认翻完所有页，探测只需要首页
        if self.drive_type == _QUARK:
            return self._adapter.get_detail(pwd_id, stoken, folder_id, first_page=True)
        return self._adapter.get_detail(pwd_id, stoken, folder_id)

    def _save_file(
        self,
        fid_list: list[str],
        fid_token_list: list[str],
        to_pdir_fid: str,
        pwd_id: str,
        stoken: str,
        file_names: list[str],
    ) -> Any:
        """保存文件：部分网盘需要 file_names 关键字，用签名探测兼容（与执行器同一策略）。"""
        try:
            params = inspect.signature(self._adapter.save_file).parameters
        except (TypeError, ValueError):
            params = {}
        if "file_names" in params:
            return self._adapter.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken, file_names=file_names,
            )
        try:
            return self._adapter.save_file(fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken)
        except TypeError:
            return self._adapter.save_file(
                fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken, file_names=file_names,
            )

    def _classify(self, response: Any) -> tuple[str, str]:
        verdict = self._adapter.classify_share_failure(response) or {}
        kind = str(verdict.get("kind") or "unknown")
        message = str(verdict.get("message") or "")
        return kind, message


def gateway_for_adapter(adapter: Any, *, drive_type: str | None = None) -> AdapterDriveGateway:
    """构造端口实现；业务层请通过 `app.application.container.drive_gateway()` 调用。"""
    return AdapterDriveGateway(adapter, drive_type=drive_type)
