from pathlib import Path

import pytest

from app.scripts.container_entrypoint import render_proxy_config


TEMPLATE = Path(__file__).resolve().parents[2] / 'default'


def test_default_does_not_trust_forwarded_headers():
    result = render_proxy_config(TEMPLATE.read_text(encoding='utf-8'), {})
    assert 'set_real_ip_from' not in result
    assert 'proxy_set_header X-Forwarded-For $remote_addr;' in result


@pytest.mark.parametrize('header', ['X-Forwarded-For', 'CF-Connecting-IP'])
def test_explicit_proxy_networks_and_header(header):
    result = render_proxy_config(TEMPLATE.read_text(encoding='utf-8'), {
        'TRUSTED_PROXY_CIDRS': '172.20.0.5/32,2001:db8::/64', 'TRUSTED_PROXY_HEADER': header,
    })
    assert 'set_real_ip_from 172.20.0.5/32;' in result
    assert 'set_real_ip_from 2001:db8::/64;' in result
    assert f'real_ip_header {header};' in result
    assert 'real_ip_recursive on;' in result


@pytest.mark.parametrize('env', [
    {'TRUSTED_PROXY_CIDRS': '0.0.0.0/0'}, {'TRUSTED_PROXY_CIDRS': '::/0'},
    {'TRUSTED_PROXY_CIDRS': '127.0.0.1; include /tmp/bad'},
    {'TRUSTED_PROXY_CIDRS': '*'}, {'TRUSTED_PROXY_HEADER': 'CF-Connecting-IP'},
    {'TRUSTED_PROXY_CIDRS': '127.0.0.1', 'TRUSTED_PROXY_HEADER': 'X-Forwarded-For;evil'},
])
def test_invalid_or_unbounded_trust_is_rejected(env):
    with pytest.raises(ValueError):
        render_proxy_config(TEMPLATE.read_text(encoding='utf-8'), env)
