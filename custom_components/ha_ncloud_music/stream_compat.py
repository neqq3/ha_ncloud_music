"""Music Assistant stream compatibility helpers."""

from __future__ import annotations

import logging
from asyncio import CancelledError
from urllib.parse import urlparse

import aiohttp
from aiohttp import web

_LOGGER = logging.getLogger(__name__)

_STREAM_HEADERS = {
    # 部分云音乐高品质直链会拦截 aiohttp 默认请求头；用常见媒体客户端标识保留原始音质。
    "User-Agent": "Lavf/61.7.100",
    "Referer": "https://music.163.com/",
}

_PASSTHROUGH_HEADERS = (
    "Content-Type",
    "Content-Length",
    "Content-Range",
    "Accept-Ranges",
    "ETag",
)


def should_use_stream_compat(url: str) -> bool:
    """Return True when MA needs HA to fetch the upstream URL with media headers."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    return parsed.hostname == "d1.music.126.net" and parsed.path.startswith("/dmusic/")


async def stream_with_media_headers(request: web.Request, url: str) -> web.StreamResponse:
    """Stream an upstream audio URL while preserving Range support where possible."""
    headers = dict(_STREAM_HEADERS)
    if range_header := request.headers.get("Range"):
        headers["Range"] = range_header

    timeout = aiohttp.ClientTimeout(total=None, sock_connect=30, sock_read=60)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url) as upstream:
            if upstream.status >= 400:
                text = await upstream.text(errors="replace")
                _LOGGER.warning("MA 兼容流式转发失败: status=%s url=%s", upstream.status, url)
                return web.Response(status=upstream.status, text=text)

            response_headers = {
                key: value
                for key in _PASSTHROUGH_HEADERS
                if (value := upstream.headers.get(key)) is not None
            }
            response = web.StreamResponse(status=upstream.status, headers=response_headers)
            await response.prepare(request)

            try:
                async for chunk in upstream.content.iter_chunked(64 * 1024):
                    await response.write(chunk)
                await response.write_eof()
            except (CancelledError, ConnectionResetError):
                _LOGGER.debug("MA 兼容流式转发客户端已断开: %s", url)
            return response
