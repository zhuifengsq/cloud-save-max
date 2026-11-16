"""Bound login bodies before FastAPI parses form fields (including chunked IO)."""
from fastapi import Request
from fastapi.routing import APIRoute
from fastapi.responses import JSONResponse

LOGIN_BODY_LIMIT = 16 * 1024


class LoginBodyLimitRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()
        if not self.path.endswith('/login'):
            return handler

        async def bounded(request: Request):
            chunks = []
            size = 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > LOGIN_BODY_LIMIT:
                    return JSONResponse(status_code=413, content={
                        'code': 'AUTH_BODY_TOO_LARGE', 'message': '登录请求过大', 'detail': None})
                chunks.append(chunk)
            request._body = b''.join(chunks)
            return await handler(request)

        return bounded
