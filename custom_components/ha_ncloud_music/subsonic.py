"""
OpenSubsonic API 兼容层

为 Music Assistant 提供 Subsonic API 接口，实现曲线集成。

架构原则：
- 完全独立：不被其他模块依赖
- 只读使用：只调用 cloud_music.py 的现有方法
- 异常隔离：所有错误在此层捕获，不影响主功能

路径: /api/ncloud/subsonic/rest/xxx.view
认证: 复用云音乐 Cookie，客户端可填任意账号
"""

import logging
import re
from aiohttp import web
from homeassistant.components.http import HomeAssistantView

_LOGGER = logging.getLogger(__name__)

# Subsonic API 版本
SUBSONIC_API_VERSION = "1.16.1"
SERVER_NAME = "ha_ncloud_music"
# MA 可能会把解析失败的 coverArt ID 当成本地文件路径交给 ffmpeg。
# 因此 getCoverArt 最后必须兜底到一个真实图片 URL。
DEFAULT_COVER_URL = "https://p2.music.126.net/fL9ORyu0e777lppGU3D89A==/109951167206009876.jpg"
# OpenSubsonic search3 没有歌单结果字段，只能把歌单伪装成专辑。
# 给这些伪专辑挂一个稳定的虚拟歌手，避免 MA 记录找不到 artistId 的日志。
PLAYLIST_ARTIST_ID = "ar_playlist"


# 模块级别的缓存，用于存储搜索到的歌单（偷渡到 getPlaylists）
_searched_playlists_cache = {}

_LRC_LINE_RE = re.compile(r"\[(\d+):(\d+)(?:\.(\d+))?\]")

class SubsonicApiView(HomeAssistantView):
    """
    Subsonic API 统一入口
    
    所有 Subsonic API 请求通过此视图处理
    路径: /rest/rest/{method}.view
    
    注意: libopensonic 库使用以下 URL 拼接逻辑:
    - base_url + ":" + port + "/" + server_path + "/rest/" + method + ".view"
    - 当 server_path="/rest" 时，实际请求路径变成 /rest/rest/
    
    用户在 MA 中配置:
    - Base URL: http://192.168.6.54
    - Port: 8123
    - Server Path: /rest
    """
    
    url = "/rest/rest/{method}"
    name = "ncloud:subsonic"
    requires_auth = False  # Subsonic 有自己的认证机制
    
    def _response(self, request, post_data: dict, data: dict, status: str = "ok") -> web.Response:
        """
        生成 Subsonic 响应（支持 XML 和 JSON）
        
        根据请求参数 f 决定响应格式：
        - f=xml → XML
        - 其他（包括默认） → JSON（现代客户端通常期望 JSON）
        """
        import json
        
        # 检查请求的响应格式（同时检查 URL 和 POST 参数）
        fmt = self._get_param(request, post_data, 'f', 'json').lower()
        
        # 构建响应数据结构
        response_data = {
            "subsonic-response": {
                "status": status,
                "version": SUBSONIC_API_VERSION,
                "serverVersion": SERVER_NAME,
                **data
            }
        }
        
        if fmt in ('json', 'jsonp'):
            # JSON 格式
            callback = request.query.get('callback')
            json_str = json.dumps(response_data, ensure_ascii=False)
            
            if fmt == 'jsonp' and callback:
                return web.Response(
                    text=f"{callback}({json_str})",
                    content_type="application/javascript",
                    charset="utf-8"
                )
            return web.Response(
                text=json_str,
                content_type="application/json",
                charset="utf-8"
            )
        else:
            # XML 格式
            xml_content = self._dict_to_xml(data)
            xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<subsonic-response xmlns="http://subsonic.org/restapi" status="{status}" version="{SUBSONIC_API_VERSION}" serverVersion="{SERVER_NAME}">
{xml_content}
</subsonic-response>'''
            return web.Response(
                text=xml,
                content_type="application/xml",
                charset="utf-8"
            )
    
    def _dict_to_xml(self, data: dict, indent: int = 0) -> str:
        """将字典转换为 XML 字符串"""
        parts = []
        for key, value in data.items():
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        attrs = ' '.join([f'{k}="{self._xml_escape(str(v))}"' for k, v in item.items() if not isinstance(v, (dict, list))])
                        nested = self._dict_to_xml({k: v for k, v in item.items() if isinstance(v, (dict, list))})
                        if nested:
                            parts.append(f'<{key} {attrs}>{nested}</{key}>')
                        else:
                            parts.append(f'<{key} {attrs}/>')
            elif isinstance(value, dict):
                nested = self._dict_to_xml(value)
                parts.append(f'<{key}>{nested}</{key}>')
            elif value is not None:
                parts.append(f'<{key}>{self._xml_escape(str(value))}</{key}>')
        return ''.join(parts)
    
    def _xml_response(self, content: str, status: str = "ok") -> web.Response:
        """生成标准 Subsonic XML 响应（兼容旧代码）"""
        xml = f'''<?xml version="1.0" encoding="UTF-8"?>
<subsonic-response xmlns="http://subsonic.org/restapi" status="{status}" version="{SUBSONIC_API_VERSION}" serverVersion="{SERVER_NAME}">
{content}
</subsonic-response>'''
        return web.Response(
            text=xml,
            content_type="application/xml",
            charset="utf-8"
        )
    
    def _error_response(self, request, post_data: dict, code: int, message: str) -> web.Response:
        """生成错误响应"""
        return self._response(request, post_data, {"error": {"code": code, "message": message}}, status="failed")
    
    def _validate_auth(self, request, post_data: dict) -> bool:
        """
        验证 Subsonic 认证参数
        
        Subsonic 支持两种认证方式：
        1. Token 认证: t=token, s=salt（推荐，默认）
        2. Legacy 明文密码: p=password
        
        我们复用云音乐 Cookie，所以只检查参数存在即可
        """
        # 必须有 u (username) 和 v (version) 和 c (client)
        has_u = self._get_param(request, post_data, 'u') is not None
        has_v = self._get_param(request, post_data, 'v') is not None
        has_c = self._get_param(request, post_data, 'c') is not None
        
        if not (has_u and has_v and has_c):
            return False
        
        # 检查是否有 Token 认证 或 Legacy 明文密码
        has_token = self._get_param(request, post_data, 't') is not None and self._get_param(request, post_data, 's') is not None
        has_legacy = self._get_param(request, post_data, 'p') is not None
        
        return has_token or has_legacy
    
    async def get(self, request, method: str):
        """处理所有 Subsonic GET 请求"""
        return await self._handle_request(request, method, {})
    
    async def post(self, request, method: str):
        """处理所有 Subsonic POST 请求（部分客户端使用 POST）"""
        # 解析 POST 请求体参数
        post_data = {}
        try:
            post_data = await request.post()
        except Exception:
            pass
        return await self._handle_request(request, method, post_data)
    
    def _get_param(self, request, post_data: dict, key: str, default=None):
        """
        从请求中获取参数（同时检查 URL 查询参数和 POST 请求体）
        
        优先级：URL 查询参数 > POST 请求体
        """
        # 先检查 URL 查询参数
        if key in request.query:
            return request.query.get(key)
        # 再检查 POST 请求体
        if key in post_data:
            return post_data.get(key)
        return default

    def _parse_lrc_to_structured_lyrics(self, lyric_text: str) -> list[dict]:
        """将 LRC 歌词转换为 OpenSubsonic 结构化歌词行。"""
        lines = []
        for raw_line in lyric_text.splitlines():
            line = raw_line.strip()
            if not line:
                continue

            matches = list(_LRC_LINE_RE.finditer(line))
            if not matches:
                continue

            content = line[matches[-1].end():].strip()
            if not content:
                continue

            for match in matches:
                minutes = int(match.group(1))
                seconds = int(match.group(2))
                fraction = match.group(3) or "0"
                # LRC 小数位可能是厘秒或毫秒，这里统一换算成毫秒。
                if len(fraction) == 1:
                    milliseconds = int(fraction) * 100
                elif len(fraction) == 2:
                    milliseconds = int(fraction) * 10
                else:
                    milliseconds = int(fraction[:3].ljust(3, '0'))

                start_ms = minutes * 60 * 1000 + seconds * 1000 + milliseconds
                lines.append({
                    "start": start_ms,
                    "value": content,
                })

        return lines

    def _build_structured_lyrics(self, song_id: str, lyric_data: dict) -> list[dict]:
        """根据云音乐歌词数据构造 OpenSubsonic 结构化歌词。"""
        # MA 2.8+ 发现 songLyrics 扩展后会调用 getLyricsBySongId。
        # 没有歌词时返回成功的空列表，比返回 API 错误更安全，
        # 否则可能中断 MA 的歌单浏览流程。
        lrc = lyric_data.get('lrc', '') or ''
        if lrc:
            lines = self._parse_lrc_to_structured_lyrics(lrc)
            if lines:
                return [{
                    "displayArtist": "",
                    "displayTitle": "",
                    "lang": "",
                    "offset": 0,
                    "synced": True,
                    "line": lines,
                }]

        plain_text = lyric_data.get('lrc', '') or lyric_data.get('yrc', '') or ''
        if plain_text:
            text_lines = []
            for raw_line in plain_text.splitlines():
                line = _LRC_LINE_RE.sub('', raw_line).strip()
                if not line or line.startswith('['):
                    continue
                text_lines.append({"value": line})
            if text_lines:
                return [{
                    "displayArtist": "",
                    "displayTitle": "",
                    "lang": "",
                    "synced": False,
                    "line": text_lines,
                }]

        _LOGGER.debug("Subsonic lyrics: no usable lyric lines for song %s", song_id)
        return []
    
    async def _handle_request(self, request, method: str, post_data: dict):
        """统一处理 Subsonic 请求"""
        try:
            # 移除 .view 后缀
            method = method.replace('.view', '')
            
            # 验证认证（宽松模式）- 需要同时检查 URL 和 POST 参数
            if not self._validate_auth(request, post_data):
                return self._error_response(request, post_data, 10, "Required parameter is missing")
            
            # 获取 cloud_music 实例
            hass = request.app["hass"]
            cloud_music = hass.data.get('cloud_music')
            
            if cloud_music is None:
                return self._error_response(request, post_data, 0, "Cloud Music not initialized")
            
            # 路由到具体方法
            handler = getattr(self, f'_handle_{method}', None)
            if handler:
                return await handler(request, post_data, cloud_music)
            else:
                _LOGGER.warning(f"Subsonic: 未实现的方法 {method}")
                return self._error_response(request, post_data, 0, f"Method not implemented: {method}")
                
        except Exception as e:
            # 异常隔离：绝不让错误传播到 HA 核心
            _LOGGER.error(f"Subsonic API error ({method}): {e}")
            return self._error_response(request, post_data, 0, "Server error")
    
    # ==================== 系统 API ====================
    
    async def _handle_ping(self, request, post_data, cloud_music) -> web.Response:
        """ping - 连接测试"""
        return self._response(request, post_data, {})
    
    async def _handle_getLicense(self, request, post_data, cloud_music) -> web.Response:
        """getLicense - 许可信息（返回有效许可）"""
        return self._response(request, post_data, {
            "license": {
                "valid": True,
                "email": "ha_ncloud_music@local",
                "licenseExpires": "2099-12-31T23:59:59"
            }
        })
    
    async def _handle_getMusicFolders(self, request, post_data, cloud_music) -> web.Response:
        """getMusicFolders - 音乐文件夹（返回固定的云音乐）"""
        return self._response(request, post_data, {
            "musicFolders": {
                "musicFolder": [{"id": "1", "name": "云音乐"}]
            }
        })
    
    async def _handle_getArtists(self, request, post_data, cloud_music) -> web.Response:
        """getArtists - 艺术家索引（返回空，通过搜索访问）"""
        return self._response(request, post_data, {
            "artists": {
                "ignoredArticles": "The El La Los Las Le Les",
                "index": []
            }
        })
    
    async def _handle_getIndexes(self, request, post_data, cloud_music) -> web.Response:
        """getIndexes - 返回文件夹索引，包含虚拟的搜索歌单文件夹"""
        # 创建一个虚拟的"搜索歌单"文件夹入口
        # 用户可以通过 Browse → 云音乐 → 搜索歌单 访问
        index_items = []
        
        # 如果有搜索到的歌单，显示入口
        if _searched_playlists_cache:
            index_items.append({
                "name": "搜索歌单",
                "artist": [{
                    "id": "folder_searched_playlists",
                    "name": f"🔍 搜索歌单 ({len(_searched_playlists_cache)} 个)"
                }]
            })
        
        return self._response(request, post_data, {
            "indexes": {
                "ignoredArticles": "The El La Los Las Le Les",
                "index": index_items
            }
        })
    
    async def _handle_getMusicDirectory(self, request, post_data, cloud_music) -> web.Response:
        """getMusicDirectory - 返回文件夹内容，用于显示搜索到的歌单"""
        dir_id = self._get_param(request, post_data, 'id', '')
        _LOGGER.info(f"Subsonic getMusicDirectory: id={dir_id}")
        
        # 如果是搜索歌单文件夹
        if dir_id == "folder_searched_playlists":
            children = []
            for pl in _searched_playlists_cache.values():
                children.append({
                    "id": pl["id"],
                    "parent": "folder_searched_playlists",
                    "isDir": False,
                    "title": pl["name"],
                    "artist": pl.get("owner", ""),
                    "coverArt": pl.get("coverArt", ""),
                    "type": "music"
                })
            
            return self._response(request, post_data, {
                "directory": {
                    "id": "folder_searched_playlists",
                    "name": "搜索歌单",
                    "child": children
                }
            })
        
        # 其他情况返回空
        return self._response(request, post_data, {
            "directory": {
                "id": dir_id,
                "name": "未知",
                "child": []
            }
        })
    
    # ==================== 空实现 API (避免 MA 报错) ====================
    
    async def _handle_getAlbumList2(self, request, post_data, cloud_music) -> web.Response:
        """getAlbumList2 - 专辑列表（返回空）"""
        return self._response(request, post_data, {"albumList2": {"album": []}})
    
    async def _handle_getNewestPodcasts(self, request, post_data, cloud_music) -> web.Response:
        """getNewestPodcasts - 播客（返回空）"""
        return self._response(request, post_data, {"newestPodcasts": {"episode": []}})
    
    async def _handle_getStarred2(self, request, post_data, cloud_music) -> web.Response:
        """getStarred2 - 收藏（返回空）"""
        return self._response(request, post_data, {"starred2": {}})
    
    async def _handle_getRandomSongs(self, request, post_data, cloud_music) -> web.Response:
        """getRandomSongs - 随机歌曲（返回空）"""
        return self._response(request, post_data, {"randomSongs": {"song": []}})
    
    async def _handle_getAlbum(self, request, post_data, cloud_music) -> web.Response:
        """getAlbum - 获取专辑详情（同时支持歌单伪装的专辑）"""
        album_id = self._get_param(request, post_data, 'id', '')
        
        # 处理歌单伪装的专辑 (pl_xxx)
        if album_id and album_id.startswith('pl_'):
            real_id = album_id[3:]
            try:
                # search3 阶段把歌单伪装成 album 返回后，MA 点进详情会继续调用 getAlbum。
                # 这里负责把这个“伪专辑”再还原成真实歌单内容，保证搜索结果可继续浏览。
                # 获取歌单详情
                playlist_result = await cloud_music.netease_cloud_music(f'/playlist/detail?id={real_id}')
                if playlist_result and playlist_result.get('playlist'):
                    playlist_data = playlist_result['playlist']
                    
                    # 获取歌单中的歌曲
                    songs = await cloud_music.async_get_playlist(real_id)
                    songs_list = []
                    for song in songs:
                        songs_list.append({
                            "id": f"s_{song.id}",
                            "parent": album_id,
                            "isDir": False,
                            "title": song.song,
                            "album": playlist_data.get('name', ''),
                            "artist": song.singer,
                            "track": 0,
                            "year": 0,
                            "duration": int(song.duration / 1000) if song.duration > 1000 else int(song.duration),
                            "size": 0,
                            "suffix": "mp3",
                            "contentType": "audio/mpeg",
                            "coverArt": f"s_{song.id}",
                            "albumId": album_id,
                            "artistId": PLAYLIST_ARTIST_ID,
                            "type": "music",
                            "created": "2020-01-01T00:00:00.000Z"
                        })
                    
                    creator = playlist_data.get('creator', {})
                    return self._response(request, post_data, {
                        "album": {
                            "id": album_id,
                            "name": f"📋 {playlist_data.get('name', '')}",
                            "artist": f"歌单 · {creator.get('nickname', '未知')}",
                            "artistId": PLAYLIST_ARTIST_ID,
                            "coverArt": f"p_{real_id}",
                            "songCount": len(songs_list),
                            "duration": sum(s.get('duration', 0) for s in songs_list),
                            "created": "2020-01-01T00:00:00.000Z",
                            "year": None,
                            "song": songs_list
                        }
                    })
            except Exception as e:
                _LOGGER.error(f"Subsonic getAlbum (歌单) 失败: {e}")
            return self._error_response(request, post_data, 70, "Playlist not found")
        
        # 处理普通专辑 (al_xxx)
        if not album_id or not album_id.startswith('al_'):
            return self._error_response(request, post_data, 10, "Invalid album id")
        
        real_id = album_id[3:]
        
        try:
            result = await cloud_music.netease_cloud_music(f'/album?id={real_id}')
            if result and result.get('album'):
                album_data = result['album']
                songs_data = result.get('songs', [])
                
                # 构建歌曲列表
                songs = []
                for song in songs_data:
                    songs.append(self._format_song_from_api_dict(song))
                
                artist_info = album_data.get('artist', {})
                return self._response(request, post_data, {
                    "album": {
                        "id": album_id,
                        "name": album_data.get('name', ''),
                        "artist": artist_info.get('name', ''),
                        "artistId": f"ar_{artist_info.get('id', '')}",
                        "coverArt": album_id,
                        "songCount": len(songs),
                        "duration": sum(s.get('duration', 0) for s in songs),
                        "created": "2020-01-01T00:00:00.000Z",
                        "year": album_data.get('publishTime', 0) // 31536000000 + 1970 if album_data.get('publishTime') else None,
                        "song": songs
                    }
                })
        except Exception as e:
            _LOGGER.error(f"Subsonic getAlbum 失败: {e}")
        
        return self._error_response(request, post_data, 70, "Album not found")
    
    async def _handle_getArtist(self, request, post_data, cloud_music) -> web.Response:
        """getArtist - 获取艺术家详情"""
        artist_id = self._get_param(request, post_data, 'id', '')
        if not artist_id or not artist_id.startswith('ar_'):
            return self._error_response(request, post_data, 10, "Invalid artist id")

        if artist_id == PLAYLIST_ARTIST_ID:
            # 这个虚拟歌手只服务于“歌单伪装成专辑”的搜索结果。
            # 内容故意保持为空，MA 只需要这个映射是合法可解析的。
            return self._response(request, post_data, {
                "artist": {
                    "id": PLAYLIST_ARTIST_ID,
                    "name": "歌单",
                    "coverArt": "",
                    "artistImageUrl": "",
                    "albumCount": 0,
                    "album": []
                }
            })
        
        real_id = artist_id[3:]
        
        try:
            # 获取艺术家详情
            result = await cloud_music.netease_cloud_music(f'/artist/detail?id={real_id}')
            if result and result.get('data') and result['data'].get('artist'):
                artist_data = result['data']['artist']
                
                # 获取艺术家热门歌曲
                songs_result = await cloud_music.netease_cloud_music(f'/artist/top/song?id={real_id}')
                albums = []
                
                # 获取艺术家专辑
                albums_result = await cloud_music.netease_cloud_music(f'/artist/album?id={real_id}&limit=20')
                if albums_result and albums_result.get('hotAlbums'):
                    for album in albums_result['hotAlbums'][:20]:
                        albums.append({
                            "id": f"al_{album.get('id')}",
                            "name": album.get('name', ''),
                            "artist": artist_data.get('name', ''),
                            "artistId": artist_id,
                            "coverArt": f"al_{album.get('id')}",
                            "songCount": album.get('size', 0),
                            "duration": 0,
                            "created": "2020-01-01T00:00:00.000Z",
                            "year": album.get('publishTime', 0) // 31536000000 + 1970 if album.get('publishTime') else None
                        })
                
                return self._response(request, post_data, {
                    "artist": {
                        "id": artist_id,
                        "name": artist_data.get('name', ''),
                        "coverArt": artist_id,
                        "artistImageUrl": artist_data.get('cover', ''),
                        "albumCount": len(albums),
                        "album": albums
                    }
                })
        except Exception as e:
            _LOGGER.error(f"Subsonic getArtist 失败: {e}")
        
        return self._error_response(request, post_data, 70, "Artist not found")
    
    async def _handle_getAlbumInfo2(self, request, post_data, cloud_music) -> web.Response:
        """getAlbumInfo2 - 获取专辑元信息"""
        album_id = self._get_param(request, post_data, 'id', '')
        if not album_id:
            return self._error_response(request, post_data, 10, "Missing album id")
        
        # 返回基本信息结构（可以为空）
        return self._response(request, post_data, {
            "albumInfo": {
                "notes": "",
                "musicBrainzId": "",
                "smallImageUrl": "",
                "mediumImageUrl": "",
                "largeImageUrl": ""
            }
        })
    
    async def _handle_getArtistInfo2(self, request, post_data, cloud_music) -> web.Response:
        """getArtistInfo2 - 获取艺术家元信息"""
        artist_id = self._get_param(request, post_data, 'id', '')
        if not artist_id:
            return self._error_response(request, post_data, 10, "Missing artist id")
        
        # 返回基本信息结构（可以为空）
        return self._response(request, post_data, {
            "artistInfo2": {
                "biography": "",
                "musicBrainzId": "",
                "smallImageUrl": "",
                "mediumImageUrl": "",
                "largeImageUrl": "",
                "similarArtist": []
            }
        })
    
    async def _handle_getTopSongs(self, request, post_data, cloud_music) -> web.Response:
        """getTopSongs - 获取艺术家热门歌曲"""
        artist_name = self._get_param(request, post_data, 'artist', '')
        count = int(self._get_param(request, post_data, 'count', 50))
        
        if not artist_name:
            return self._error_response(request, post_data, 10, "Missing artist name")
        
        try:
            from urllib.parse import quote
            
            # 先搜索艺术家获取 ID
            search_result = await cloud_music.netease_cloud_music(
                f'/cloudsearch?keywords={quote(artist_name)}&type=100&limit=1'
            )
            
            if search_result and search_result.get('result') and search_result['result'].get('artists'):
                artist_id = search_result['result']['artists'][0].get('id')
                
                # 获取艺术家热门歌曲
                songs_result = await cloud_music.netease_cloud_music(
                    f'/artist/top/song?id={artist_id}'
                )
                
                if songs_result and songs_result.get('songs'):
                    songs = []
                    for song in songs_result['songs'][:count]:
                        songs.append(self._format_song_from_api_dict(song))
                    
                    return self._response(request, post_data, {
                        "topSongs": {"song": songs}
                    })
            
            # 如果找不到艺术家，返回空列表
            return self._response(request, post_data, {"topSongs": {"song": []}})
            
        except Exception as e:
            _LOGGER.error(f"Subsonic getTopSongs 失败: {e}")
            return self._response(request, post_data, {"topSongs": {"song": []}})
    
    async def _handle_getOpenSubsonicExtensions(self, request, post_data, cloud_music) -> web.Response:
        """
        getOpenSubsonicExtensions - OpenSubsonic 扩展声明
        
        这是 MA 识别 OpenSubsonic 服务器的必要端点！
        返回服务器支持的 OpenSubsonic 扩展列表。
        """
        return self._response(request, post_data, {
            "openSubsonicExtensions": [
                {"name": "formPost", "versions": [1]},
                {"name": "songLyrics", "versions": [1]},
            ]
        })

    async def _handle_getLyricsBySongId(self, request, post_data, cloud_music) -> web.Response:
        """getLyricsBySongId - 为 OpenSubsonic 客户端返回结构化歌词。"""
        song_id = self._get_param(request, post_data, 'id', '')
        if not song_id or not song_id.startswith('s_'):
            return self._error_response(request, post_data, 10, "Invalid song id")

        real_id = song_id[2:]
        try:
            lyric_data = await cloud_music.async_get_lyric(real_id)
            structured_lyrics = self._build_structured_lyrics(real_id, lyric_data)
            return self._response(request, post_data, {
                "lyricsList": {
                    "structuredLyrics": structured_lyrics
                }
            })
        except Exception as e:
            _LOGGER.error(f"Subsonic getLyricsBySongId 失败: {e}", exc_info=True)
            # 即使云音乐没有歌词或歌词接口失败，也保持端点成功返回；
            # 否则 MA 会报告 Method/API 错误。
            return self._response(request, post_data, {
                "lyricsList": {
                    "structuredLyrics": []
                }
            })

    async def _handle_getLyrics(self, request, post_data, cloud_music) -> web.Response:
        """getLyrics - 兼容旧 Subsonic 客户端的歌词端点。"""
        title = self._get_param(request, post_data, 'title', '')
        artist = self._get_param(request, post_data, 'artist', '')

        if not title:
            return self._response(request, post_data, {"lyricsList": {"lyrics": []}})

        from urllib.parse import quote

        try:
            search_result = await cloud_music.netease_cloud_music(
                f'/cloudsearch?keywords={quote(title)}&type=1&limit=10'
            )
            if search_result and search_result.get('result') and search_result['result'].get('songs'):
                matched_song = None
                for song in search_result['result']['songs']:
                    artists = song.get('ar', []) or song.get('artists', [])
                    artist_names = [item.get('name', '') for item in artists]
                    if not artist or artist in artist_names:
                        matched_song = song
                        break

                if matched_song:
                    lyric_data = await cloud_music.async_get_lyric(str(matched_song.get('id')))
                    plain_lyrics = lyric_data.get('lrc', '') or ''
                    if plain_lyrics:
                        return self._response(request, post_data, {
                            "lyricsList": {
                                "lyrics": [{
                                    "artist": artist,
                                    "title": title,
                                    "value": plain_lyrics
                                }]
                            }
                        })
        except Exception as e:
            _LOGGER.error(f"Subsonic getLyrics 失败: {e}", exc_info=True)

        return self._response(request, post_data, {"lyricsList": {"lyrics": []}})
    
    # ==================== 搜索 API ====================
    
    async def _handle_search3(self, request, post_data, cloud_music) -> web.Response:
        """search3 - 搜索歌曲、艺术家、专辑"""
        query = self._get_param(request, post_data, 'query', '')
        if not query:
            return self._response(request, post_data, {"searchResult3": {}})
        
        from urllib.parse import quote
        
        # 解析分页参数（MA 默认会请求各 20 条）
        song_count = int(self._get_param(request, post_data, 'songCount', 20))
        artist_count = int(self._get_param(request, post_data, 'artistCount', 20))
        album_count = int(self._get_param(request, post_data, 'albumCount', 20))
        
        songs = []
        artists = []
        albums = []
        
        # 搜索歌曲 (type=1)
        if song_count > 0:
            try:
                res = await cloud_music.netease_cloud_music(
                    f'/cloudsearch?keywords={quote(query)}&type=1&limit={song_count}'
                )
                if res and res.get('result') and res['result'].get('songs'):
                    for item in res['result']['songs'][:song_count]:
                        songs.append(self._format_song_from_api_dict(item))
                _LOGGER.debug(f"Subsonic search3: 找到 {len(songs)} 首歌曲")
            except Exception as e:
                _LOGGER.error(f"Subsonic search3 歌曲搜索失败: {e}")
        
        # 搜索艺术家 (type=100)
        if artist_count > 0:
            try:
                res = await cloud_music.netease_cloud_music(
                    f'/cloudsearch?keywords={quote(query)}&type=100&limit={artist_count}'
                )
                if res and res.get('result') and res['result'].get('artists'):
                    for item in res['result']['artists'][:artist_count]:
                        artists.append({
                            "id": f"ar_{item.get('id')}",
                            "name": item.get('name', ''),
                            "coverArt": f"ar_{item.get('id')}",
                            "artistImageUrl": "",  # 不使用歌手照片，通过 coverArt 获取专辑封面
                            "albumCount": item.get('albumSize', 0)
                        })
                _LOGGER.debug(f"Subsonic search3: 找到 {len(artists)} 位艺术家")
            except Exception as e:
                _LOGGER.error(f"Subsonic search3 艺术家搜索失败: {e}")
        
        # 搜索专辑 (type=10)
        if album_count > 0:
            try:
                res = await cloud_music.netease_cloud_music(
                    f'/cloudsearch?keywords={quote(query)}&type=10&limit={album_count}'
                )
                if res and res.get('result') and res['result'].get('albums'):
                    for item in res['result']['albums'][:album_count]:
                        artist_info = item.get('artist', {})
                        albums.append({
                            "id": f"al_{item.get('id')}",
                            "name": item.get('name', ''),
                            "artist": artist_info.get('name', ''),
                            "artistId": f"ar_{artist_info.get('id', '')}",
                            "coverArt": f"al_{item.get('id')}",
                            "songCount": item.get('size', 0),
                            "duration": 0,
                            "created": "2020-01-01T00:00:00.000Z",  # MA 必需字段
                            "year": item.get('publishTime', 0) // 31536000000 + 1970 if item.get('publishTime') else None
                        })
                _LOGGER.debug(f"Subsonic search3: 找到 {len(albums)} 张专辑")
            except Exception as e:
                _LOGGER.error(f"Subsonic search3 专辑搜索失败: {e}")
        
        # 搜索歌单 (type=1000) - 包装成虚拟专辑返回
        # 因为 MA 的 libopensonic 不支持 search3 返回 playlist 字段
        # 所以我们把歌单伪装成专辑，用户点击后通过 getAlbum 获取歌单详情
        # 同时缓存到 _searched_playlists_cache，在 getPlaylists 中显示
        
        # 清空之前的缓存，只保留最近一次搜索的结果。
        # 这是一个有意为之的妥协：MA 不会把搜索关键词再带到 Playlists 页面，
        # 所以这里只保留“最近一次搜索上下文”，避免旧搜索结果长期混在用户歌单里。
        _searched_playlists_cache.clear()
        _LOGGER.info(f"Subsonic search3: 清空歌单缓存，开始新搜索 keywords={query}")
        playlist_as_albums = []
        try:
            _LOGGER.info(f"Subsonic search3: 开始搜索歌单 keywords={query}")
            res = await cloud_music.netease_cloud_music(
                f'/cloudsearch?keywords={quote(query)}&type=1000&limit=30'
            )
            _LOGGER.info(f"Subsonic search3: 歌单搜索返回 code={res.get('code') if res else 'None'}")
            if res and res.get('result') and res['result'].get('playlists'):
                _LOGGER.info(f"Subsonic search3: 找到 {len(res['result']['playlists'])} 个歌单")
                for item in res['result']['playlists'][:30]:
                    creator = item.get('creator', {})
                    # 使用特殊前缀 pl_ 标识这是歌单伪装的专辑
                    playlist_as_albums.append({
                        "id": f"pl_{item.get('id')}",  # pl_ 前缀表示歌单
                        "name": f"[歌单] {item.get('name', '')}",  # 使用中文标识
                        "artist": f"歌单 · {creator.get('nickname', '未知')}",
                        # search3 没有 playlist 字段，歌单只能作为 album 返回；
                        # 但 artistId 仍然必须能被 MA 解析。
                        "artistId": PLAYLIST_ARTIST_ID,
                        "coverArt": f"p_{item.get('id')}",  # 使用歌单封面
                        "songCount": item.get('trackCount', 0),
                        "duration": 0,
                        "created": "2020-01-01T00:00:00.000Z",
                        "year": None
                    })
                    # 同时缓存歌单到全局变量，用于偷渡到 getPlaylists。
                    # 这样用户即使切到 Playlists 标签页，也还能看到刚搜到的歌单。
                    _searched_playlists_cache[f"p_{item.get('id')}"] = {
                        "id": f"p_{item.get('id')}",
                        "name": f"[搜索] {item.get('name', '')}",
                        "owner": creator.get('nickname', '未知'),
                        "public": True,
                        "songCount": item.get('trackCount', 0),
                        "duration": 0,
                        "created": "2020-01-01T00:00:00.000Z",
                        "changed": "2020-01-01T00:00:00.000Z",
                        "coverArt": f"p_{item.get('id')}"
                    }
                    _LOGGER.info(f"Subsonic search3: 缓存歌单 {item.get('name')} 到偷渡列表")
            else:
                _LOGGER.warning(f"Subsonic search3: 歌单搜索结果为空 res={res}")
        except Exception as e:
            _LOGGER.error(f"Subsonic search3 歌单搜索失败: {e}", exc_info=True)
        
        # 组合结果：歌单在前，专辑在后。
        # 这是另一个展示层妥协：MA 的 Albums 标签页只显示前几十个结果，
        # 如果把歌单排在后面，它们很容易被普通专辑挤掉，看起来就像“搜不到歌单”。
        _LOGGER.info(f"Subsonic search3: 歌单 {len(playlist_as_albums)} 个, 专辑 {len(albums)} 个")
        
        # 限制专辑数量为 20，给歌单留空间
        albums = albums[:20]
        
        # 歌单放前面，专辑放后面
        final_albums = playlist_as_albums + albums
        _LOGGER.info(f"Subsonic search3: 最终返回 {len(final_albums)} 个（歌单+专辑）")
        
        result = {"searchResult3": {}}
        if songs:
            result["searchResult3"]["song"] = songs
        if artists:
            result["searchResult3"]["artist"] = artists
        if final_albums:
            result["searchResult3"]["album"] = final_albums
        
        return self._response(request, post_data, result)
    
    def _format_song_from_api(self, item: dict) -> str:
        """将云音乐 API 返回的歌曲数据转换为 Subsonic song XML"""
        song_id = f"s_{item.get('id')}"
        
        title = self._xml_escape(item.get('name', ''))
        
        artists = item.get('ar', [])
        artist = self._xml_escape(', '.join([a.get('name', '') for a in artists]))
        
        album_info = item.get('al', {})
        album = self._xml_escape(album_info.get('name', ''))
        
        duration = int(item.get('dt', 0) / 1000)
        cover_id = song_id
        
        return (
            f'<song id="{song_id}" title="{title}" artist="{artist}" '
            f'album="{album}" duration="{duration}" '
            f'coverArt="{cover_id}" isDir="false" '
            f'contentType="audio/mpeg" suffix="mp3"/>'
        )
    
    def _format_song_from_api_dict(self, item: dict, quality_info: dict = None) -> dict:
        """将云音乐 API 返回的歌曲数据转换为 Subsonic JSON 格式
        
        Args:
            item: 歌曲详情数据
            quality_info: 可选,从/song/url/v1获取的实际音质信息
        """
        song_id = f"s_{item.get('id')}"
        artists = item.get('ar', [])
        album_info = item.get('al', {})
        album_id = album_info.get('id', '')
        
        # 尝试获取封面 URL（云音乐api搜索结果中专辑信息包含 picUrl）
        cover_url = album_info.get('picUrl', '')
        
        # 首先尝试使用传入的实际音质信息
        if quality_info:
            sr = quality_info.get('sr', 44100)
            br = quality_info.get('br', 320000)
            audio_type = quality_info.get('type', 'mp3')
            size = quality_info.get('size', 0)
            
            # 根据实际格式判断
            if audio_type in ('flac', 'alac'):
                suffix = 'flac'
                content_type = 'audio/flac'
                if sr >= 96000:
                    bit_depth = 24
                elif sr >= 48000:
                    bit_depth = 24
                else:
                    bit_depth = 16
            else:
                suffix = 'mp3'
                content_type = 'audio/mpeg'
                bit_depth = None
            
            quality_data = {
                'suffix': suffix,
                'contentType': content_type,
                'bitRate': br // 1000,
                'samplingRate': sr,
                'size': size,
                'channelCount': 2,
                'bitDepth': bit_depth
            }
        else:
            # Fallback: 从歌曲详情数据推断
            quality_data = self._get_quality_from_song_data(item)
        
        result = {
            "id": song_id,
            "parent": f"al_{album_id}" if album_id else "",
            "isDir": False,
            "title": item.get('name', ''),
            "album": album_info.get('name', ''),
            "artist": ', '.join([a.get('name', '') for a in artists]),
            "track": item.get('no', 0),
            "year": 0,
            "duration": int(item.get('dt', 0) / 1000),
            "size": quality_data.get('size', 0),
            "suffix": quality_data.get('suffix', 'mp3'),
            "contentType": quality_data.get('contentType', 'audio/mpeg'),
            "coverArt": song_id,  # 使用 ID，MA 会调用 getCoverArt
            "albumId": f"al_{album_id}" if album_id else "",
            "artistId": f"ar_{artists[0].get('id', '')}" if artists else "",
            "type": "music",
            "created": "2020-01-01T00:00:00.000Z"
        }
        
        # 添加OpenSubsonic扩展字段
        if quality_data.get('bitRate'):
            result["bitRate"] = quality_data['bitRate']
        if quality_data.get('samplingRate'):
            result["samplingRate"] = quality_data['samplingRate']
        if quality_data.get('bitDepth'):
            result["bitDepth"] = quality_data['bitDepth']
        if quality_data.get('channelCount'):
            result["channelCount"] = quality_data['channelCount']
        
        return result
    
    def _get_quality_from_song_data(self, item: dict, cloud_music=None) -> dict:
        """从歌曲数据推断音质信息或从API获取实际音质"""
        song_id = item.get('id')
        
        # 优先尝试从API获取实际音质(包含jyeffect等黑胶VIP音质)
        if cloud_music and song_id:
            try:
                import asyncio
                # 获取实际音质信息
                url_res = asyncio.create_task(
                    cloud_music.netease_cloud_music(
                        f'/song/url/v1?id={song_id}&level={cloud_music.audio_quality}'
                    )
                )
                # 等待结果(这是在async函数中)
                url_data = asyncio.get_event_loop().run_until_complete(url_res)
                
                if url_data and url_data.get('data'):
                    quality_info = url_data['data'][0]
                    sr = quality_info.get('sr', 44100)
                    br = quality_info.get('br', 320000)
                    audio_type = quality_info.get('type', 'mp3')
                    size = quality_info.get('size', 0)
                    
                    # 根据实际格式判断
                    if audio_type in ('flac', 'alac'):
                        suffix = 'flac'
                        content_type = 'audio/flac'
                        # 推断位深度
                        if sr >= 96000:
                            bit_depth = 24
                        elif sr >= 48000:
                            bit_depth = 24
                        else:
                            bit_depth = 16
                    else:
                        suffix = 'mp3'
                        content_type = 'audio/mpeg'
                        bit_depth = None
                    
                    result = {
                        'suffix': suffix,
                        'contentType': content_type,
                        'bitRate': br // 1000,  # kbps
                        'samplingRate': sr,
                        'size': size,
                        'channelCount': 2
                    }
                    
                    if bit_depth:
                        result['bitDepth'] = bit_depth
                    
                    _LOGGER.debug(f"Subsonic: 从API获取音质 id={song_id}, sr={sr}, type={audio_type}")
                    return result
            except Exception as e:
                _LOGGER.debug(f"Subsonic: 无法从API获取音质,使用推断方式: {e}")
        
        # Fallback: 从歌曲详情数据推断
        # /song/detail 返回的数据中包含各音质版本信息
        hr = item.get('hr')  # Hi-Res
        sq = item.get('sq')  # 无损
        h = item.get('h')    # 高(320k)
        m = item.get('m')    # 中(192k)
        l = item.get('l')    # 低(128k)
        
        # 按优先级选择可用音质
        quality = hr or sq or h or m or l
        
        if not quality:
            # 无音质信息,返回默认值
            return {
                'suffix': 'mp3',
                'contentType': 'audio/mpeg',
                'size': 0
            }
        
        br = quality.get('br', 320000)
        sr = quality.get('sr', 44100)
        size = quality.get('size', 0)
        
        # 根据比特率判断格式
        if br >= 900000:  # 无损或Hi-Res
            suffix = 'flac'
            content_type = 'audio/flac'
            # 推断位深度
            if sr >= 96000:
                bit_depth = 24
            elif sr >= 48000:
                bit_depth = 24
            else:
                bit_depth = 16
        else:
            suffix = 'mp3'
            content_type = 'audio/mpeg'
            bit_depth = None
        
        result = {
            'suffix': suffix,
            'contentType': content_type,
            'bitRate': br // 1000,  # 转换为 kbps
            'samplingRate': sr,
            'size': size,
            'channelCount': 2
        }
        
        if bit_depth:
            result['bitDepth'] = bit_depth
        
        return result
    
    def _format_song_xml(self, song) -> str:
        """将 MusicInfo 转换为 Subsonic song XML"""
        # 确定性 ID: s_ 前缀 + 歌曲ID
        song_id = f"s_{song.id}"
        
        # XML 转义
        title = self._xml_escape(song.song)
        artist = self._xml_escape(song.singer)
        album = self._xml_escape(song.album) if hasattr(song, 'album') and song.album else ""
        
        # 时长（毫秒转秒）
        duration = int(song.duration / 1000) if song.duration > 1000 else int(song.duration)
        
        # 封面 ID (复用歌曲 ID)
        cover_id = song_id
        
        return (
            f'<song id="{song_id}" title="{title}" artist="{artist}" '
            f'album="{album}" duration="{duration}" '
            f'coverArt="{cover_id}" isDir="false" '
            f'contentType="audio/mpeg" suffix="mp3"/>'
        )
    
    def _xml_escape(self, text: str) -> str:
        """XML 转义特殊字符"""
        if not text:
            return ""
        return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
            .replace("'", "&apos;"))
    
    # ==================== 歌曲 API ====================
    
    async def _handle_getSong(self, request, post_data, cloud_music) -> web.Response:
        """getSong - 获取单曲信息"""
        song_id = self._get_param(request, post_data, 'id', '')
        if not song_id or not song_id.startswith('s_'):
            return self._error_response(request, post_data, 10, "Invalid song id")
        
        real_id = song_id[2:]
        
        try:
            result = await cloud_music.netease_cloud_music(f'/song/detail?ids={real_id}')
            if result and result.get('songs'):
                song_data = result['songs'][0]
                
                # 获取实际音质信息
                quality_info = None
                try:
                    url_res = await cloud_music.netease_cloud_music(
                        f'/song/url/v1?id={real_id}&level={cloud_music.audio_quality}'
                    )
                    if url_res and url_res.get('data'):
                        quality_info = url_res['data'][0]
                        _LOGGER.debug(f"Subsonic getSong: 音质 id={real_id}, sr={quality_info.get('sr')}, type={quality_info.get('type')}")
                except Exception as e:
                    _LOGGER.warning(f"Subsonic getSong: 获取音质失败 {e}")
                
                return self._response(request, post_data, {
                    "song": self._format_song_from_api_dict(song_data, quality_info)
                })
        except Exception as e:
            _LOGGER.error(f"Subsonic getSong 失败: {e}")
        
        return self._error_response(request, post_data, 70, "Song not found")
    
    # ==================== 流媒体 API ====================
    
    async def _handle_stream(self, request, post_data, cloud_music) -> web.Response:
        """
        stream - 音频流
        
        策略: Redirect (302) 优先，性能最佳
        """
        song_id = self._get_param(request, post_data, 'id', '')
        if not song_id or not song_id.startswith('s_'):
            return self._error_response(request, post_data, 10, "Invalid song id")
        
        real_id = song_id[2:]
        
        try:
            url, fee = await cloud_music.song_url(real_id)
            
            if url:
                _LOGGER.debug(f"Subsonic stream: 重定向到 {url[:50]}...")
                return web.HTTPFound(url)
            else:
                _LOGGER.warning(f"Subsonic stream: 无法获取歌曲 {real_id} 的 URL")
                return web.Response(status=404, text=f"Item '{song_id}' not found")
                
        except Exception as e:
            _LOGGER.error(f"Subsonic stream 失败: {e}")
            return web.Response(status=500, text="Stream error")
    
    async def _handle_download(self, request, post_data, cloud_music) -> web.Response:
        """download - 下载（复用 stream 逻辑）"""
        return await self._handle_stream(request, post_data, cloud_music)
    
    # ==================== 封面 API ====================
    
    async def _handle_getCoverArt(self, request, post_data, cloud_music) -> web.Response:
        """getCoverArt - 获取封面图片（代理模式，直接返回图片数据）"""
        cover_id = self._get_param(request, post_data, 'id', '')
        _LOGGER.debug(f"Subsonic getCoverArt: 收到请求 id={cover_id}")
        
        if not cover_id:
            return self._error_response(request, post_data, 10, "Missing id")
        
        # 获取请求的尺寸参数（可选）
        # 如果 MA 没有指定尺寸，则返回原图（最大清晰度）
        # 参考 Jellyfin 实现：直接返回云音乐原始 picUrl，不添加尺寸限制
        size = self._get_param(request, post_data, 'size', None)
        cover_url = None
        
        try:
            # 歌曲封面 (s_xxx)
            if cover_id.startswith('s_'):
                real_id = cover_id[2:]
                result = await cloud_music.netease_cloud_music(f'/song/detail?ids={real_id}')
                if result and result.get('songs'):
                    cover_url = result['songs'][0].get('al', {}).get('picUrl', '')
            
            # 专辑封面 (al_xxx)
            elif cover_id.startswith('al_'):
                real_id = cover_id[3:]
                result = await cloud_music.netease_cloud_music(f'/album?id={real_id}')
                if result and result.get('album'):
                    cover_url = result['album'].get('picUrl', '')
            
            # 艺术家封面 (ar_xxx) - 使用热门专辑封面，避免歌手照片
            elif cover_id.startswith('ar_'):
                real_id = cover_id[3:]
                # 获取艺术家的热门专辑，使用第一张专辑的封面
                if cover_id != PLAYLIST_ARTIST_ID:
                    result = await cloud_music.netease_cloud_music(f'/artist/album?id={real_id}&limit=1')
                    if result and result.get('hotAlbums') and len(result['hotAlbums']) > 0:
                        cover_url = result['hotAlbums'][0].get('picUrl', '')
                    if not cover_url:
                        # 有些歌手没有热门专辑，但 artist/detail 里仍可能有头像或封面。
                        result = await cloud_music.netease_cloud_music(f'/artist/detail?id={real_id}')
                        if result and result.get('data') and result['data'].get('artist'):
                            artist_data = result['data']['artist']
                            cover_url = artist_data.get('cover') or artist_data.get('avatar')
            
            # 歌单封面 (p_xxx)
            elif cover_id.startswith('p_'):
                # ========== 特殊处理：每日推荐封面 ==========
                # 使用第一首推荐歌曲的专辑封面作为歌单封面
                if cover_id == 'p_daily':
                    try:
                        songs = await cloud_music.async_get_dailySongs()
                        if songs and len(songs) > 0:
                            # 使用第一首歌的封面
                            cover_url = songs[0].picUrl
                            _LOGGER.debug(f"Subsonic getCoverArt: 每日推荐使用第一首歌封面 {cover_url[:50] if cover_url else 'None'}...")
                    except Exception as e:
                        _LOGGER.error(f"获取每日推荐封面失败: {e}")
                # ========== 每日推荐封面处理结束 ==========
                else:
                    # 普通歌单封面
                    real_id = cover_id[2:]
                    result = await cloud_music.netease_cloud_music(f'/playlist/detail?id={real_id}')
                    if result and result.get('playlist'):
                        cover_url = result['playlist'].get('coverImgUrl', '')
            
            # 其他情况：尝试作为歌曲 ID
            else:
                result = await cloud_music.netease_cloud_music(f'/song/detail?ids={cover_id}')
                if result and result.get('songs'):
                    cover_url = result['songs'][0].get('al', {}).get('picUrl', '')
            
            if not cover_url:
                _LOGGER.debug(f"Subsonic getCoverArt: no cover URL for {cover_id}, using fallback")
                cover_url = DEFAULT_COVER_URL

            if cover_url:
                # 只有当 MA 明确请求尺寸时才添加 ?param= 参数
                # 否则返回原图（最大清晰度）
                if size:
                    cover_url = f"{cover_url}?param={size}y{size}"
                
                # 代理模式：获取图片数据并返回
                import aiohttp
                async with aiohttp.ClientSession() as session:
                    headers = {'Referer': 'https://music.163.com/'}
                    async with session.get(cover_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                        if resp.status == 200:
                            image_data = await resp.read()
                            content_type = resp.headers.get('Content-Type', 'image/jpeg')
                            # aiohttp.web.Response 只接受媒体类型；
                            # 云音乐有时会返回 "image/jpeg; charset=..." 这类值。
                            content_type = content_type.split(';', 1)[0].strip()
                            _LOGGER.debug(f"Subsonic getCoverArt: 返回图片 {len(image_data)} bytes")
                            return web.Response(body=image_data, content_type=content_type)
                        else:
                            _LOGGER.warning(f"Subsonic getCoverArt: 获取图片失败 HTTP {resp.status}")
            else:
                _LOGGER.warning(f"Subsonic getCoverArt: 未找到封面 URL, cover_id={cover_id}")
                
        except Exception as e:
            _LOGGER.error(f"Subsonic getCoverArt 失败: {e}", exc_info=True)
        
        return self._error_response(request, post_data, 70, "Cover art not found")
    
    # ==================== 播放列表 API ====================
    
    async def _handle_getPlaylists(self, request, post_data, cloud_music) -> web.Response:
        """getPlaylists - 获取用户歌单列表"""
        try:
            # 确保 userinfo 已加载
            await cloud_music._ensure_userinfo_loaded()
            
            if not hasattr(cloud_music, 'userinfo') or not cloud_music.userinfo:
                _LOGGER.debug("Subsonic getPlaylists: userinfo 未加载")
                return self._response(request, post_data, {"playlists": {"playlist": []}})
            
            uid = cloud_music.userinfo.get('uid')  # 修复：使用 'uid' 而非 'userId'
            if not uid:
                _LOGGER.debug("Subsonic getPlaylists: 用户未登录")
                return self._response(request, post_data, {"playlists": {"playlist": []}})
            
            result = await cloud_music.netease_cloud_music(f'/user/playlist?uid={uid}')
            if not result or not result.get('playlist'):
                return self._response(request, post_data, {"playlists": {"playlist": []}})
            
            playlists = []
            
            # ========== 添加每日推荐（固定歌单，每天更新）==========
            # 使用特殊 ID "p_daily"，始终显示在列表最前面
            # 云音乐每天会为登录用户推荐 30 首歌曲
            playlists.append({
                "id": "p_daily",
                "name": "📅 每日推荐",
                "owner": "云音乐",
                "public": True,
                "songCount": 30,
                "duration": 0,
                "created": "2020-01-01T00:00:00.000Z",
                "changed": "2020-01-01T00:00:00.000Z",
                "coverArt": "p_daily"
            })
            # ========== 每日推荐添加结束 ==========
            
            # 添加用户的普通歌单
            for pl in result['playlist']:
                playlist_id = pl.get('id')
                playlists.append({
                    "id": f"p_{playlist_id}",
                    "name": pl.get('name', ''),
                    "owner": pl.get('creator', {}).get('nickname', ''),
                    "public": pl.get('privacy') == 0,
                    "songCount": pl.get('trackCount', 0),
                    "duration": 0,  # 歌单总时长（可选）
                    "created": "2020-01-01T00:00:00.000Z",
                    "changed": "2020-01-01T00:00:00.000Z",
                    "coverArt": f"p_{playlist_id}"  # 用于 getCoverArt
                })
            
            # 添加缓存的搜索歌单（偷渡功能）。
            # OpenSubsonic/MA 没有“搜索歌单结果页”到“歌单列表页”的原生通道，
            # 所以这里把最近一次搜索命中的歌单临时插到用户歌单列表里，作为补偿展示。
            if _searched_playlists_cache:
                _LOGGER.info(f"Subsonic getPlaylists: 偷渡 {len(_searched_playlists_cache)} 个搜索歌单")
                for pl in _searched_playlists_cache.values():
                    playlists.insert(1, pl)  # 插入到每日推荐之后
            
            _LOGGER.info(f"Subsonic getPlaylists: 返回 {len(playlists)} 个歌单（含偷渡）")
            return self._response(request, post_data, {
                "playlists": {"playlist": playlists}
            })
        except Exception as e:
            _LOGGER.error(f"Subsonic getPlaylists 失败: {e}", exc_info=True)
            return self._response(request, post_data, {"playlists": {"playlist": []}})
    
    async def _handle_getPlaylist(self, request, post_data, cloud_music) -> web.Response:
        """getPlaylist - 获取歌单详情"""
        playlist_id = self._get_param(request, post_data, 'id', '')
        if not playlist_id or not playlist_id.startswith('p_'):
            return self._error_response(request, post_data, 10, "Invalid playlist id")
        
        # ========== 特殊处理：每日推荐 ==========
        # 每日推荐使用固定 ID "p_daily"
        # 调用云音乐 API /recommend/songs 获取今日推荐的 30 首歌曲
        if playlist_id == 'p_daily':
            try:
                _LOGGER.info("Subsonic getPlaylist: 获取每日推荐歌单")
                
                # 调用 HA 集成中已实现的每日推荐 API
                songs = await cloud_music.async_get_dailySongs()
                if not songs:
                    _LOGGER.warning("每日推荐歌曲列表为空")
                    return self._error_response(request, post_data, 70, "Daily recommend not available")
                
                # 格式化歌曲列表
                songs_list = []
                for song in songs:
                    songs_list.append({
                        "id": f"s_{song.id}",
                        "isDir": False,
                        "title": song.song,
                        "album": getattr(song, 'album', ''),
                        "artist": song.singer,
                        "duration": int(song.duration / 1000) if song.duration > 1000 else int(song.duration),
                        "coverArt": f"s_{song.id}",
                        "contentType": "audio/mpeg",
                        "suffix": "mp3",
                        "type": "music"
                    })
                
                _LOGGER.info(f"Subsonic getPlaylist: 返回 {len(songs_list)} 首每日推荐歌曲")
                
                return self._response(request, post_data, {
                    "playlist": {
                        "id": "p_daily",
                        "name": "📅 每日推荐",
                        "owner": "云音乐",
                        "public": True,
                        "songCount": len(songs_list),
                        "duration": sum(s.get('duration', 0) for s in songs_list),
                        "created": "2020-01-01T00:00:00.000Z",
                        "changed": "2020-01-01T00:00:00.000Z",
                        "coverArt": "p_daily",
                        "entry": songs_list
                    }
                })
            except Exception as e:
                _LOGGER.error(f"Subsonic getPlaylist (每日推荐) 失败: {e}", exc_info=True)
                return self._error_response(request, post_data, 0, "Server error")
        # ========== 每日推荐处理结束 ==========
        
        # 普通歌单处理：提取歌单 ID
        real_id = playlist_id[2:]
        
        try:
            # 先获取歌单信息
            playlist_info = await cloud_music.netease_cloud_music(f'/playlist/detail?id={real_id}')
            playlist_data = playlist_info.get('playlist', {}) if playlist_info else {}
            
            # MA 会通过歌曲的专辑映射来解析歌单内单曲封面，
            # 所以这里直接使用云音乐原始歌曲接口，保留 al/ar ID，
            # 不再使用会丢失这些 ID 的简化 async_get_playlist 模型。
            tracks_res = await cloud_music.netease_cloud_music(
                f'/playlist/track/all?id={real_id}&limit=1000'
            )
            track_items = tracks_res.get('songs', []) if tracks_res else []
            if not track_items:
                return self._error_response(request, post_data, 70, "Playlist not found")
            
            songs_list = []
            for song in track_items:
                album_info = song.get('al') or {}
                artists = song.get('ar') or []
                album_id = album_info.get('id')
                artist_id = artists[0].get('id') if artists else None
                songs_list.append({
                    "id": f"s_{song.get('id')}",
                    "isDir": False,
                    "title": song.get('name', ''),
                    "album": album_info.get('name', ''),
                    "artist": ', '.join([artist.get('name', '') for artist in artists]),
                    "duration": int(song.get('dt', 0) / 1000),
                    "coverArt": f"s_{song.get('id')}",
                    "parent": f"al_{album_id}" if album_id else "",
                    "albumId": f"al_{album_id}" if album_id else "",
                    "artistId": f"ar_{artist_id}" if artist_id else "",
                    "contentType": "audio/mpeg",
                    "suffix": "mp3",
                    "type": "music"
                })
            
            return self._response(request, post_data, {
                "playlist": {
                    "id": playlist_id,
                    "name": playlist_data.get('name', ''),
                    "owner": playlist_data.get('creator', {}).get('nickname', ''),
                    "public": playlist_data.get('privacy', 0) == 0,
                    "songCount": len(songs_list),
                    "duration": sum(s.get('duration', 0) for s in songs_list),
                    "created": "2020-01-01T00:00:00.000Z",
                    "changed": "2020-01-01T00:00:00.000Z",
                    "coverArt": playlist_id,
                    "entry": songs_list
                }
            })
        except Exception as e:
            _LOGGER.error(f"Subsonic getPlaylist 失败: {e}", exc_info=True)
            return self._error_response(request, post_data, 0, "Server error")
