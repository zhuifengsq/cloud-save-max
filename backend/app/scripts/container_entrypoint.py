"""Generate a fail-closed nginx trust boundary before starting the container."""
from collections.abc import Mapping
import ipaddress
import os
from pathlib import Path
import subprocess

MARKER = '# TRUSTED_PROXY_CONFIG'


def render_proxy_config(template: str, environment: Mapping[str, str]) -> str:
    raw = environment.get('TRUSTED_PROXY_CIDRS', '').strip()
    header = environment.get('TRUSTED_PROXY_HEADER', '').strip()
    if header and not raw:
        raise ValueError('Proxy header requires explicit trusted proxy networks')
    if template.count(MARKER) != 1:
        raise ValueError('Proxy template marker is missing')
    directives: list[str] = []
    if raw:
        if header not in ('', 'X-Forwarded-For', 'CF-Connecting-IP'):
            raise ValueError('Unsupported proxy IP header')
        networks = []
        for value in raw.split(','):
            try:
                network = ipaddress.ip_network(value.strip(), strict=False)
            except ValueError:
                raise ValueError('Invalid trusted proxy network') from None
            if network.prefixlen == 0:
                raise ValueError('Trusting every source is forbidden')
            networks.append(str(network))
        if len(networks) > 64:
            raise ValueError('Too many trusted proxy networks')
        directives = [f'set_real_ip_from {network};' for network in networks]
        directives.extend((f'real_ip_header {header or "X-Forwarded-For"};', 'real_ip_recursive on;'))
    return template.replace(MARKER, '\n    '.join(directives) or '# Direct clients: no forwarded IP headers are trusted.')


def main() -> None:
    template = Path('/etc/nginx/default.conf.template').read_text(encoding='utf-8')
    configuration = render_proxy_config(template, os.environ)
    Path('/etc/nginx/http.d/default.conf').write_text(configuration, encoding='utf-8')
    subprocess.run(['/usr/sbin/nginx', '-t'], check=True)
    os.execvp('/usr/bin/supervisord', ['/usr/bin/supervisord', '-c', '/etc/supervisord.conf'])


if __name__ == '__main__':
    main()
