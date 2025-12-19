"""
Jellyfin HTTP API View for Home Assistant
将 Jellyfin API 注册到 HA HTTP 服务器
"""

import logging
from homeassistant.components.http import HomeAssistantView
from aiohttp import web

_LOGGER = logging.getLogger(__name__)


class JellyfinApiView(HomeAssistantView):
    """
    Jellyfin API 统一入口
    
    处理所有 /jellyfin/* 路径的请求
    """
    
    url = "/jellyfin/{path:.*}"
    name = "cloud_music:jellyfin"
    requires_auth = False  # Jellyfin 有自己的认证机制
    
    def __init__(self, cloud_music):
        """初始化视图"""
        from .jellyfin import JellyfinHandler
        self.handler = JellyfinHandler(cloud_music)
        _LOGGER.info("Jellyfin API View 初始化完成")
    
    async def post(self, request, path: str):
        """处理 POST 请求"""
        _LOGGER.debug(f"Jellyfin POST: /{path}")
        _LOGGER.debug(f"Query: {dict(request.query)}")
        
        # POST /Users/AuthenticateByName - 认证
        if path == "Users/AuthenticateByName":
            return await self.handler.handle_authenticate(request)
        
        _LOGGER.warning(f"未知 POST 端点: /{path}")
        return web.json_response({"error": "Not found"}, status=404)
    
    async def get(self, request, path: str):
        """处理 GET 请求"""
        _LOGGER.debug(f"Jellyfin GET: /{path} | Query: {dict(request.query)}")
        
        # GET /Artists - 艺术家搜索（aiojellyfin ArtistQueryBuilder 专用端点）
        if path == "Artists":
            return await self.handler.handle_search_artists(request)
        
        # GET /Items - 通用搜索（歌曲、专辑、歌单）或获取子项
        if path == "Items":
            # 如果有 parentId 参数，则是获取子项（专辑曲目等）
            if request.query.get('parentId'):
                return await self.handler.handle_user_items(request)
            # 否则是搜索或库查询
            return await self.handler.handle_search_items(request)
        
        # GET /Playlists/{playlistId}/Items - 获取歌单内的歌曲
        if path.startswith("Playlists/") and path.endswith("/Items"):
            parts = path.split("/")
            if len(parts) >= 3:
                playlist_id = parts[1]
                return await self.handler.handle_playlist_items(request, playlist_id)
        
        # GET /Users/{userId}/Items - 获取子项或项目详情
        if path.startswith("Users/") and "Items" in path:
            # /Users/{userId}/Items?ParentId=xxx
            if path.count("/") == 2 and path.endswith("/Items"):
                return await self.handler.handle_user_items(request)
            # /Users/{userId}/Items/{itemId}
            # 注意：itemId 可能包含 :// 等特殊字符（如 _fake://ar_123），不能简单用 split('/')
            elif "/Items/" in path:
                # 找到 /Items/ 后面的所有内容作为 item_id
                items_index = path.find("/Items/")
                if items_index != -1:
                    item_id = path[items_index + 7:]  # 7 = len("/Items/")
                    return await self.handler.handle_get_item(request, item_id)
        
        # GET /Items/{itemId}/Images/{imageType} - 获取封面
        if "/Images/" in path:
            # 规范化路径（确保以 / 开头）
            normalized_path = "/" + path if not path.startswith("/") else path
            
            # 如果路径包含 :// 可能是 _fake://ar_ 格式，需要特殊处理
            if "://" in normalized_path:
                # /Items/_fake://ar_61204986/Images/Primary
                items_idx = normalized_path.find("/Items/")
                images_idx = normalized_path.find("/Images/")
                if items_idx != -1 and images_idx != -1 and items_idx < images_idx:
                    item_id = normalized_path[items_idx + 7:images_idx]
                    image_type = normalized_path[images_idx + 8:]
                else:
                    return web.json_response({"error": "Invalid image path format"}, status=404)
            else:
                # 普通格式，使用 split 解析
                parts = normalized_path.split("/")
                # /Items/s_2003832740/Images/Primary -> ['', 'Items', 's_2003832740', 'Images', 'Primary']
                if len(parts) >= 5 and parts[1] == "Items" and parts[3] == "Images":
                    item_id = parts[2]
                    image_type = parts[4] if len(parts) > 4 else "Primary"
                else:
                    return web.json_response({"error": "Invalid image path format"}, status=404)
            
            return await self.handler.handle_get_image(request, item_id, image_type)
        
        # GET /Audio/{itemId}/universal - 音频流
        if path.startswith("Audio/") and path.endswith("/universal"):
            parts = path.split("/")
            if len(parts) >= 2:
                item_id = parts[1]
                return await self.handler.handle_audio_stream(request, item_id)
        
        # 未知端点
        _LOGGER.warning(f"未知 GET 端点: /{path}")
        return web.json_response({"error": f"Endpoint not implemented: {path}"}, status=404)
