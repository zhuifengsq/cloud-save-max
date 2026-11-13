from app.infrastructure.drive.gateway import gateway_for_adapter
from types import SimpleNamespace

def test_share_capability_and_typed_result():
    adapter = SimpleNamespace(DRIVE_TYPE='fake', SUPPORTS_SHARE=True, create_share_link=lambda p,t: {'share_url':'https://example.com', 'share_id':'s', 'title':t, 'passcode':'p', 'expires_in_days':'7'})
    gateway = gateway_for_adapter(adapter)
    assert gateway.capabilities.supports_share
    result = gateway.create_share_link('/a', 'A')
    assert (result.url, result.share_id, result.title, result.passcode, result.expires_in_days, result.supported) == ('https://example.com','s','A','p',7,True)

def test_unsupported_share():
    gateway = gateway_for_adapter(SimpleNamespace(DRIVE_TYPE='fake'))
    assert not gateway.capabilities.supports_share
    assert not gateway.create_share_link('/a', 'A').supported
