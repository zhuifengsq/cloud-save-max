"""Opt-in AES-GCM envelope. Keys live outside the database and data directory."""
import base64
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


SECRET_KEYS = frozenset({'cookie', 'password', 'authorization', 'access_token', 'refresh_token', 'ssoncookie', 'loginuuid', 'phone_number', 'device_id'})


def is_secret_key(key: str) -> bool:
    key = key.lower()
    return key in SECRET_KEYS or any(word in key for word in ('token', 'password', 'cookie', 'secret', 'authorization'))


def public_config_keys(drive_type: str) -> frozenset[str]:
    return frozenset({
        '123pan': ('username', 'protocol', 'deviceType', 'osVersion', 'name', 'debug'),
        'cloud189': ('username', 'protocol', 'name', 'debug'),
        'cloud139': ('phone', 'debug'),
    }.get(drive_type, ()))


class CredentialError(RuntimeError):
    def __init__(self):
        super().__init__('Credential protection unavailable or authentication failed')


def load_keyring(*, required: bool = False) -> dict | None:
    path = os.getenv('CREDENTIAL_KEYRING_FILE', '')
    if not path:
        if required or os.getenv('CREDENTIAL_ENCRYPTION_REQUIRED') == '1':
            raise CredentialError()
        return None
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if not isinstance(data['database_id'], str) or not data['database_id']:
            raise ValueError()
        keys = {key: base64.b64decode(value, validate=True) for key, value in data['keys'].items()}
        if not keys or any(len(value) != 32 for value in keys.values()) or data['active_key_id'] not in keys:
            raise ValueError()
        return dict(data, keys=keys)
    except Exception:
        raise CredentialError() from None


def _aad(ring: dict, identity: str, drive: str) -> bytes:
    return json.dumps([ring['database_id'], identity, drive, 1], separators=(',', ':')).encode()


def seal(payload: dict, identity: str, drive: str) -> str:
    ring = load_keyring(required=True)
    try:
        nonce = os.urandom(12)
        cipher = AESGCM(ring['keys'][ring['active_key_id']]).encrypt(
            nonce, json.dumps(payload, separators=(',', ':')).encode(), _aad(ring, identity, drive))
        return json.dumps({'v': 1, 'key_id': ring['active_key_id'], 'nonce': base64.b64encode(nonce).decode(),
                           'ciphertext': base64.b64encode(cipher).decode()}, separators=(',', ':'))
    except Exception:
        raise CredentialError() from None


def unseal(blob: str, identity: str, drive: str) -> dict:
    ring = load_keyring(required=True)
    try:
        envelope = json.loads(blob)
        if envelope['v'] != 1:
            raise ValueError()
        nonce = base64.b64decode(envelope['nonce'], validate=True)
        if len(nonce) != 12:
            raise ValueError()
        raw = AESGCM(ring['keys'][envelope['key_id']]).decrypt(nonce,
            base64.b64decode(envelope['ciphertext'], validate=True), _aad(ring, identity, drive))
        result = json.loads(raw)
        if not isinstance(result, dict) or not isinstance(result['config'], dict) or not isinstance(result['cookie'], str):
            raise ValueError()
        if not isinstance(result.get('config_present', True), bool):
            raise ValueError()
        return result
    except Exception:
        raise CredentialError() from None
