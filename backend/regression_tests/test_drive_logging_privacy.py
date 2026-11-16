import io
import logging

import requests


def test_quark_transport_failure_does_not_emit_raw_exception(monkeypatch, caplog):
    from app.infrastructure.drive.adapters.quark_adapter import QuarkAdapter
    def broken(*args, **kwargs):
        raise requests.ConnectionError('https://example.invalid/?access_token=fake-log-secret')
    monkeypatch.setattr(requests, 'request', broken)
    adapter = QuarkAdapter(cookie='fake-cookie', no_login=True)
    response = adapter._send_request('GET', 'https://example.invalid/')
    assert response.status_code == 500
    assert 'fake-log-secret' not in caplog.text
    assert 'ConnectionError' in caplog.text


def test_uc_transport_failure_does_not_emit_opaque_exception(monkeypatch, caplog):
    from app.infrastructure.drive.adapters.uc_adapter import UCAdapter
    def broken(*args, **kwargs):
        raise requests.ConnectionError('opaque-cookie-canary')
    monkeypatch.setattr(requests, 'request', broken)
    adapter = UCAdapter(cookie='fake-cookie', no_login=True)
    response = adapter._send_request('GET', 'https://example.invalid/')
    assert response.status_code == 500
    assert 'opaque-cookie-canary' not in caplog.text
    assert 'ConnectionError' in caplog.text


def test_pan123_transport_exception_cannot_leak_via_login_response_summary(monkeypatch, caplog):
    from app.infrastructure.drive.adapters.pan123_adapter import Pan123Adapter
    adapter = Pan123Adapter(config={'username': 'fake-user', 'password': 'fake-password'}, no_login=True)
    def broken(*args, **kwargs):
        raise requests.ConnectionError('opaque-cookie-canary')
    monkeypatch.setattr(adapter._session, 'request', broken)
    result = adapter._login()
    assert result['code'] == 500
    assert 'opaque-cookie-canary' not in str(result)
    assert 'opaque-cookie-canary' not in caplog.text


def test_default_formatter_redacts_headers_urls_and_traceback_without_mutating_record():
    from app.core.logging import SafeFormatter
    message = "GET https://user:fake-url-pass@example.invalid/path?x=fake-query {'Authorization': 'Bearer fake-auth', 'Cookie': 'fake-cookie'} password=fake-password access_token=fake-token kps=fake-mobile"
    record = logging.LogRecord('app.example', logging.ERROR, __file__, 1, '%s', (message,), None)
    formatted = SafeFormatter('%(message)s').format(record)
    assert 'fake-' not in formatted
    assert 'example.invalid/path' in formatted
    assert record.args == (message,)
    try:
        raise RuntimeError('unlabelled-fake-secret')
    except RuntimeError:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(SafeFormatter('%(message)s'))
        logger = logging.getLogger('test.safe-output')
        logger.addHandler(handler)
        try:
            logger.exception('Operation failed: %s', RuntimeError('unlabelled-fake-secret'))
        finally:
            logger.removeHandler(handler)
        output = stream.getvalue()
    assert 'unlabelled-fake-secret' not in output
    assert 'RuntimeError' in output
