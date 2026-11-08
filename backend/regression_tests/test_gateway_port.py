"""DriveGatewayPort 的护栏：端口的语义边界与「第 11 家网盘」扩展性验收。

覆盖点（见 docs/architecture-evolution-plan.md §10.14）：
- 端口形状：实现满足 `DriveGatewayPort` / `RemoteEntry` 协议；
- 响应归一：fid/fid_token/目录判定/size 各家字段名不同，端口统一成领域视图；
- 网盘 quirk：quark 探测必须传 first_page、baidu 必须传 check_errno（漏传会误判）；
- 失败语义：解析失败 / 验证失败 / 读取失败三阶段可区分，文案由业务层决定；
- 扩展性：新增一家网盘只需实现适配器方法，不改 gateway 一行（假第 11 家网盘）。
"""
import pytest

from app.application.container import drive_gateway
from app.domain.ports.drive import DriveGatewayPort, RemoteEntry
from app.infrastructure.drive.gateway import (
    AdapterDriveGateway,
    DriveGatewayError,
    DriveRemoteEntry,
    gateway_for_adapter,
)


class _BaseFakeAdapter:
    """通用假适配器：字段命名故意用不同网盘的混搭风格。"""

    DRIVE_TYPE = "fake11"

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.ls_response = {
            "code": 0,
            "data": {
                "list": [
                    {"fid": "f1", "fid_token": "t1", "file_name": "第一集.mp4", "is_dir": False, "size": 1024},
                    {"fs_id": 2, "share_fid_token": "t2", "server_filename": "第二季", "isdir": 1},
                ]
            },
        }
        self.mkdir_response = {"code": 0}
        self.token_response = {"code": 0, "data": {"stoken": "st"}}
        self.detail_response = {"code": 0, "data": {"list": []}}
        self.save_response = {"code": 0, "data": {"task_id": "task-1"}}
        self.verdict = {"kind": "unknown", "message": ""}

    def _record(self, name, args, kwargs):
        self.calls.append((name, args, kwargs))

    def extract_url(self, url):
        self._record("extract_url", (url,), {})
        return ("pid", "", "0", [])

    def get_stoken(self, pwd_id, passcode=""):
        self._record("get_stoken", (pwd_id, passcode), {})
        return self.token_response

    def get_detail(self, pwd_id, stoken, folder_id):
        self._record("get_detail", (pwd_id, stoken, folder_id), {})
        return self.detail_response

    def ls_dir(self, path):
        self._record("ls_dir", (path,), {})
        return self.ls_response

    def mkdir(self, dir_path):
        self._record("mkdir", (dir_path,), {})
        return self.mkdir_response

    def save_file(self, fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken):
        self._record("save_file", (fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken), {})
        return self.save_response

    def classify_share_failure(self, response):
        return dict(self.verdict)

    def close(self):
        self._record("close", (), {})


class _FileNamesAdapter(_BaseFakeAdapter):
    """部分网盘（如 115）的 save_file 需要 file_names 关键字。"""

    def __init__(self) -> None:
        super().__init__()
        self.file_names = None

    def save_file(self, fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken, file_names=None):
        self.file_names = file_names
        self._record("save_file", (fid_list, fid_token_list, to_pdir_fid, pwd_id, stoken), {"file_names": file_names})
        return self.save_response


class _QuarkLikeAdapter(_BaseFakeAdapter):
    DRIVE_TYPE = "quark"

    def get_detail(self, pwd_id, stoken, folder_id, *, first_page=False):
        self._record("get_detail", (pwd_id, stoken, folder_id), {"first_page": first_page})
        return self.detail_response


class _BaiduLikeAdapter(_BaseFakeAdapter):
    DRIVE_TYPE = "baidu"

    def get_stoken(self, pwd_id, passcode="", check_errno=False):
        self._record("get_stoken", (pwd_id, passcode), {"check_errno": check_errno})
        return self.token_response


def _call(adapter, name):
    for call_name, args, kwargs in adapter.calls:
        if call_name == name:
            return args, kwargs
    raise AssertionError(f"没有调用 {name}：{adapter.calls}")


def test_gateway_satisfies_port_protocol():
    gateway = gateway_for_adapter(_BaseFakeAdapter())
    assert isinstance(gateway, DriveGatewayPort)
    assert gateway.drive_type == "fake11"
    entries = gateway.list_dir("0")
    assert isinstance(entries[0], RemoteEntry)
    assert entries[0] == DriveRemoteEntry(name="第一集.mp4", is_dir=False, size=1024)


def test_list_dir_normalizes_each_drive_field_names():
    entries = gateway_for_adapter(_BaseFakeAdapter()).list_dir("root")
    assert [(item.name, item.is_dir, item.size) for item in entries] == [
        ("第一集.mp4", False, 1024),
        ("第二季", True, 0),
    ]


def test_list_dir_failure_raises_gateway_error():
    adapter = _BaseFakeAdapter()
    adapter.ls_response = {"code": 500, "message": "列目录失败"}
    with pytest.raises(DriveGatewayError) as excinfo:
        gateway_for_adapter(adapter).list_dir("0")
    assert "列目录失败" in str(excinfo.value)


def test_create_dir_is_idempotent_when_already_exists():
    adapter = _BaseFakeAdapter()
    adapter.mkdir_response = {"code": 400, "message": "目录已存在"}
    gateway_for_adapter(adapter).create_dir("/剧集")  # 不抛异常＝幂等
    args, _ = _call(adapter, "mkdir")
    assert args == ("/剧集",)


def test_create_dir_raises_on_real_failure():
    adapter = _BaseFakeAdapter()
    adapter.mkdir_response = {"code": 400, "message": "无权限"}
    with pytest.raises(DriveGatewayError):
        gateway_for_adapter(adapter).create_dir("/剧集")


def test_probe_share_quark_passes_first_page():
    adapter = _QuarkLikeAdapter()
    result = gateway_for_adapter(adapter).probe_share("https://pan.quark.cn/s/abc")
    assert result.valid is True and result.stage == "read"
    args, kwargs = _call(adapter, "get_detail")
    assert args == ("pid", "st", "0")
    assert kwargs == {"first_page": True}
    token_args, _ = _call(adapter, "get_stoken")
    assert token_args == ("pid", "")  # quark 不接受 check_errno


def test_probe_share_baidu_passes_check_errno():
    adapter = _BaiduLikeAdapter()
    adapter.token_response = {"code": 105, "message": "分享链接已过期"}
    adapter.verdict = {"kind": "invalid", "message": "分享链接已过期"}
    result = gateway_for_adapter(adapter).probe_share("https://pan.baidu.com/s/1abc", passcode="8888")
    assert result.valid is False and result.kind == "invalid" and result.stage == "verify"
    args, kwargs = _call(adapter, "get_stoken")
    assert args == ("pid", "8888")
    assert kwargs == {"check_errno": True}


def test_probe_share_distinguishes_parse_verify_read_stages():
    unparsable = _BaseFakeAdapter()
    unparsable.extract_url = lambda url: ("", "", "", [])
    assert gateway_for_adapter(unparsable).probe_share("https://example.com/x").stage == "parse"

    verify = _BaseFakeAdapter()
    verify.token_response = {"code": 41012}
    verify.verdict = {"kind": "invalid", "message": "分享已失效"}
    verify_result = gateway_for_adapter(verify).probe_share("https://pan.quark.cn/s/x")
    assert (verify_result.stage, verify_result.kind) == ("verify", "invalid")

    read = _BaseFakeAdapter()
    read.detail_response = {"code": 41012}
    read.verdict = {"kind": "invalid", "message": "分享已失效"}
    read_result = gateway_for_adapter(read).probe_share("https://pan.quark.cn/s/x")
    assert (read_result.stage, read_result.kind) == ("read", "invalid")


def test_check_share_is_boolean_sugar_of_probe_share():
    adapter = _BaseFakeAdapter()
    assert gateway_for_adapter(adapter).check_share("https://pan.quark.cn/s/x") is True
    adapter.detail_response = {"code": 41012}
    adapter.verdict = {"kind": "invalid", "message": "分享已失效"}
    assert gateway_for_adapter(adapter).check_share("https://pan.quark.cn/s/x") is False


def test_transfer_is_thin_save_without_retry():
    adapter = _BaseFakeAdapter()
    adapter.detail_response = adapter.ls_response  # 分享目录里有两项
    result = gateway_for_adapter(adapter).transfer("https://pan.quark.cn/s/x", "target-fid")
    assert result["success"] is True and result["task_id"] == "task-1" and result["item_count"] == 2
    args, kwargs = _call(adapter, "save_file")
    assert args[:5] == (["f1", "2"], ["t1", "t2"], "target-fid", "pid", "st")
    assert kwargs == {}


def test_transfer_passes_file_names_when_adapter_requires_it():
    adapter = _FileNamesAdapter()
    adapter.detail_response = adapter.ls_response
    gateway_for_adapter(adapter).transfer("https://pan.quark.cn/s/x", "target-fid")
    assert adapter.file_names == ["第一集.mp4", "第二季"]


def test_transfer_raises_when_fid_token_missing():
    adapter = _BaseFakeAdapter()
    adapter.ls_response = {"code": 0, "data": {"list": [{"fid": "f1", "file_name": "a.mp4"}]}}
    adapter.detail_response = adapter.ls_response
    with pytest.raises(DriveGatewayError) as excinfo:
        gateway_for_adapter(adapter).transfer("https://pan.quark.cn/s/x", "target-fid")
    assert excinfo.value.kind == "invalid"


def test_fake_eleventh_drive_needs_no_gateway_change():
    """扩展性验收：新网盘只实现适配器方法，端口层一行不改即可用。"""
    adapter = _BaseFakeAdapter()
    adapter.DRIVE_TYPE = "fake11"
    gateway = drive_gateway(adapter)
    assert isinstance(gateway, AdapterDriveGateway)
    assert gateway.drive_type == "fake11"
    assert gateway.probe_share("https://fake11.example.com/s/abc").valid is True
    assert gateway.list_dir("0")[1].is_dir is True
    gateway.close()
    assert _call(adapter, "close")[0] == ()

