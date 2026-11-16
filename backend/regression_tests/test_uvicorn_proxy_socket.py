"""Real TCP/uvicorn trust handling; nginx rendering is tested separately."""
import json
import socket
import threading
import time
from urllib.request import Request as HttpRequest, urlopen

from fastapi import FastAPI, Request
import pytest
import uvicorn


@pytest.mark.parametrize('trusted,expected', [('127.0.0.1', '203.0.113.8'), ('192.0.2.10', '127.0.0.1')])
def test_uvicorn_uses_forwarded_ip_only_from_configured_peer(trusted, expected):
    app = FastAPI()
    @app.get('/peer')
    def peer(request: Request):
        return {'ip': request.client.host}
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    config = uvicorn.Config(app, host='127.0.0.1', proxy_headers=True,
                            forwarded_allow_ips=trusted, log_level='error', lifespan='off', ws='none')
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.01)
        assert server.started
        request = HttpRequest(f'http://127.0.0.1:{listener.getsockname()[1]}/peer',
                              headers={'X-Forwarded-For': '198.51.100.99, 203.0.113.8'})
        with urlopen(request, timeout=3) as response:
            assert json.load(response)['ip'] == expected
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        listener.close()
    assert not thread.is_alive()
