from unittest.mock import Mock
from app.infrastructure.drive.account_manager import DatabaseAccountManager


def test_account_manager_can_be_constructed_from_detached_payload(monkeypatch):
    from app.infrastructure.drive import account_manager
    manager = Mock()
    monkeypatch.setattr(account_manager, 'AccountManager', lambda: manager)
    payload = {'accounts': []}
    instance = DatabaseAccountManager.from_payload(payload)
    manager.load_accounts.assert_called_once_with(payload, no_login=False)
    assert instance.manager is manager
