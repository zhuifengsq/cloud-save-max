"""Concrete QR transport. Frontends depend only on the authentication use case."""
from typing import Any
from app.infrastructure.drive.adapters.aliyun_adapter import AliyunAdapter

class AliyunQrcodeAuthProvider:
    def generate_qrcode(self) -> dict[str, Any]:
        return AliyunAdapter.generate_qrcode()

    def query_qrcode_status(self, t: str, ck: str) -> dict[str, Any]:
        return AliyunAdapter.query_qrcode_status(t, ck)
