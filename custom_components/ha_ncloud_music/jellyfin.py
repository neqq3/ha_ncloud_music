"""
Jellyfin API Handler for NetEase Cloud Music
基于 Music Assistant Jellyfin parser 的完整字段要求实现
"""

import logging
from aiohttp import web

_LOGGER = logging.getLogger(__name__)

# 虚拟用户配置
VIRTUAL_USER_ID = "netease_user_123456"
VIRTUAL_USER_NAME = "netease"
VIRTUAL_ACCESS_TOKEN = "dummy_access_token_for_netease"
VIRTUAL_SERVER_ID = "netease_jellyfin_server"

# Jellyfin API 版本
API_VERSION = "10.8.0"


class JellyfinHandler:
    """Jellyfin API 处理器 - 完全兼容 MA parser"""
    
    def __init__(self, cloud_music):
        """初始化处理器"""
        self.cloud_music = cloud_music
        _LOGGER.info("JellyfinHandler 初始化完成")
    
    def _success_response(self, data: dict) -> web.Response:
        """返回成功响应"""
        return web.json_response(data, status=200)
    
    async def handle_authenticate(self, request) -> web.Response:
        """
        POST /Users/AuthenticateByName
        认证端点 - 返回虚拟用户信息
        """
        _LOGGER.info("Jellyfin: 认证请求")
        
        return self._success_response({
            "User": {
                "Id": VIRTUAL_USER_ID,
                "Name": VIRTUAL_USER_NAME,
                "ServerId": VIRTUAL_SERVER_ID,
                "HasPassword": False,
                "HasConfiguredPassword": False,
                "HasConfiguredEasyPassword": False,
                "EnableAutoLogin": True,
                "Policy": {
                    "IsAdministrator": True,
                    "IsHidden": False,
                    "IsDisabled": False,
                    "EnableRemoteAccess": True,
                    "EnableMediaPlayback": True
                }
            },
            "AccessToken": VIRTUAL_ACCESS_TOKEN,
            "ServerId": VIRTUAL_SERVER_ID
        })
    
    def _format_jellyfin_song(self, item: dict) -> dict:
        """
        基于 MA parse_track() 要求的完整字段
        """
        song_id = item.get('id')
        album_info = item.get('al', {}) or item.get('album', {}) or {}
        artists = item.get('ar', []) or item.get('artists', [])
        artist_name = artists[0].get('name', '未知艺术家') if artists else '未知艺术家'
        artist_id = artists[0].get('id') if artists else None
        # 确保艺术家ID有效（不能是0或None），否则使用歌曲ID生成虚拟艺术家ID
        if not artist_id:
            artist_id = f"fake_{song_id}"
        
        duration_ms = item.get('dt', 0) or item.get('duration', 0) or 0
        
        # 确保专辑ID有效（不能是0或None）
        album_id = album_info.get('id') if album_info else None
        if not album_id:
            album_id = song_id  # 使用歌曲ID作为虚拟专辑ID
        album_name = album_info.get('name', '未知专辑') if album_info else '未知专辑'
        
        return {
            "Id": f"s_{song_id}",
            "Name": item.get('name', ''),
            "Type": "Audio",
            "Album": album_name,
            "AlbumId": f"al_{album_id}",
            "AlbumArtist": artist_name,
            "AlbumArtists": [{"Id": f"ar_{artist_id}", "Name": artist_name}],
            "Artists": [artist_name],
            "ArtistItems": [{"Id": f"ar_{artist_id}", "Name": artist_name}],  # 必需
            "RunTimeTicks": int(duration_ms) * 10000,  # 毫秒转100纳秒
            "ProductionYear": 0,
            "IndexNumber": 1,
            "ParentIndexNumber": 1,
            "CanDownload": True,  # MA parser 必需
            "MediaStreams": [{  # audio_format() 需要
                "Codec": "mp3",
                "Channels": 2,
                "SampleRate": 44100,
                "BitRate": 320000,
                "BitDepth": 16,
                "Type": "Audio"
            }],
            "ImageTags": {"Primary": f"s_{song_id}"},
            "BackdropImageTags": [],
            "ProviderIds": {},  # 必需
            "UserData": {
                "PlaybackPositionTicks": 0,
                "PlayCount": 0,
                "IsFavorite": False,
                "Played": False
            },
            "MediaType": "Audio",
            "Container": "mp3",
        }
    
    def _format_jellyfin_album(self, item: dict) -> dict:
        """基于 MA parse_album() 要求"""
        album_id = item.get('id')
        artist_info = item.get('artist', {}) or item.get('artists', [{}])[0] if item.get('artists') else {}
        artist_id = artist_info.get('id', 0)
        artist_name = artist_info.get('name', '未知艺术家')
        
        publish_time = item.get('publishTime', 0)
        production_year = publish_time // 31536000000 + 1970 if publish_time and publish_time > 0 else 0
        
        return {
            "Id": f"al_{album_id}",
            "Name": item.get('name', ''),
            "Type": "MusicAlbum",
            "AlbumArtist": artist_name,
            "AlbumArtists": [{"Id": f"ar_{artist_id}", "Name": artist_name}],  # MA parser检查此字段
            "Artists": [artist_name],
            "ArtistItems": [{"Id": f"ar_{artist_id}", "Name": artist_name}],  # 备用字段
            "ProductionYear": production_year,
            "ImageTags": {"Primary": f"al_{album_id}"},
            "BackdropImageTags": [],
            "ProviderIds": {},  # 必需
            "ChildCount": item.get('size', 0) or 0,
            "UserData": {
                "PlaybackPositionTicks": 0,
                "PlayCount": 0,
                "IsFavorite": False,
                "Played": False
            }
        }
    
    def _format_jellyfin_artist(self, item: dict) -> dict:
        """基于 MA parse_artist() 要求"""
        artist_id = item.get('id')
        # MA要求ID以 _fake:// 开头（原始字符串，会在HTTP传输时被URL编码）
        jellyfin_id = f"_fake://ar_{artist_id}"
        
        return {
            "Id": jellyfin_id,
            "Name": item.get('name', ''),
            "Type": "MusicArtist",
            "ImageTags": {"Primary": jellyfin_id},
            "BackdropImageTags": [],
            "ProviderIds": {},
            "ChildCount": 50,  # 告诉MA这个艺术家有内容
            "AlbumCount": 10,  # 默认假设有10张专辑
            "SongCount": 50,   # 默认假设有50首热门歌曲
            "Overview": item.get('briefDesc', ''),
            "UserData": {
                "PlaybackPositionTicks": 0,
                "PlayCount": 0,
                "IsFavorite": False,
                "Played": False
            }
        }
    
    def _format_jellyfin_playlist(self, item: dict) -> dict:
        """基于 MA parse_playlist() 要求"""
        playlist_id = item.get('id')
        creator = item.get('creator', {})
        
        return {
            "Id": f"pl_{playlist_id}",
            "Name": item.get('name', ''),
            "Type": "Playlist",
            "Owner": creator.get('nickname', ''),
            "ChildCount": item.get('trackCount', 0),
            "ImageTags": {"Primary": f"pl_{playlist_id}"},
            "BackdropImageTags": [],
            "ProviderIds": {},
            "UserData": {
                "PlaybackPositionTicks": 0,
                "PlayCount": 0,
                "IsFavorite": False,
                "Played": False
            },
            "MediaType": "Audio"
        }
    
    async def handle_search_items(self, request) -> web.Response:
        """
        GET /Items
        支持多种查询模式:
        - searchTerm=xxx - 搜索
        - ParentId=xxx - 获取专辑/歌单的歌曲列表
        """
        search_term = request.query.get('searchTerm', '')
        include_types = request.query.get('includeItemTypes', '')
        parent_id = request.query.get('ParentId', '')
        parent_id_raw = request.query.get('parentId', '')  # 小写形式
        limit = int(request.query.get('limit', '50'))
        
        # 统一 ParentId（支持大小写）
        if not parent_id and parent_id_raw:
            parent_id = parent_id_raw
        
        # 处理 ParentId 请求 - 获取专辑或歌单内的歌曲
        if parent_id:
            _LOGGER.info(f"Jellyfin Items: 获取 ParentId={parent_id} 的子项目")
            items = []
            
            try:
                # 专辑曲目 (al_xxx)
                if parent_id.startswith('al_'):
                    real_id = parent_id[3:]
                    res = await self.cloud_music.netease_cloud_music(f'/album?id={real_id}')
                    if res and res.get('songs'):
                        for song in res['songs']:
                            items.append(self._format_jellyfin_song(song))
                        _LOGGER.info(f"Jellyfin: 专辑 {real_id} 返回 {len(items)} 首歌曲")
                
                # 歌单曲目 (pl_xxx)
                elif parent_id.startswith('pl_'):
                    real_id = parent_id[3:]
                    res = await self.cloud_music.netease_cloud_music(f'/playlist/track/all?id={real_id}')
                    if res and res.get('songs'):
                        for song in res['songs']:
                            items.append(self._format_jellyfin_song(song))
                        _LOGGER.info(f"Jellyfin: 歌单 {real_id} 返回 {len(items)} 首歌曲")
                
            except Exception as e:
                _LOGGER.error(f"Jellyfin: 获取 ParentId={parent_id} 失败 - {e}")
            
            return self._success_response({
                "Items": items,
                "TotalRecordCount": len(items),
                "StartIndex": 0
            })
        
        # 无搜索词时的处理
        if not search_term:
            # 1. 无 ParentId：返回虚拟音乐库 (MA 的 get_media_folders 调用)
            if not parent_id:
                _LOGGER.info("Jellyfin: 返回虚拟音乐库 (get_media_folders)")
                response_data = {
                    "Items": [
                        {
                            "Id": "netease_virtual_library",
                            "Name": "网易云音乐",
                            "Type": "CollectionFolder",
                            "CollectionType": "music",
                            "ServerId": "netease_server",
                            "Etag": "netease_music_etag",
                            "CanDownload": False,
                            "SupportsSync": False
                        }
                    ],
                    "TotalRecordCount": 1,
                    "StartIndex": 0
                }
                return self._success_response(response_data)
            
            # 2. ParentId 是虚拟库 + Playlist 类型：返回用户收藏的歌单
            if parent_id == "netease_virtual_library" and 'Playlist' in include_types:
                _LOGGER.info(f"Jellyfin: 请求虚拟库的歌单")
                items = []
                try:
                    # 确保 userinfo 已加载
                    await self.cloud_music._ensure_userinfo_loaded()
                    
                    if hasattr(self.cloud_music, 'userinfo') and self.cloud_music.userinfo:
                        uid = self.cloud_music.userinfo.get('uid')
                        if uid:
                            # 获取用户歌单
                            result = await self.cloud_music.netease_cloud_music(f'/user/playlist?uid={uid}')
                            if result and result.get('playlist'):
                                for pl in result['playlist']:
                                    items.append(self._format_jellyfin_playlist(pl))
                                _LOGGER.info(f"Jellyfin: 返回 {len(items)} 个用户歌单")
                        else:
                            _LOGGER.warning("Jellyfin: 用户未登录，无法获取歌单")
                    else:
                        _LOGGER.warning("Jellyfin: userinfo 未加载")
                except Exception as e:
                    _LOGGER.error(f"Jellyfin: 获取用户歌单失败 - {e}", exc_info=True)
                
                return self._success_response({
                    "Items": items,
                    "TotalRecordCount": len(items),
                    "StartIndex": 0
                })
            
            # 3. ParentId 是虚拟库 + 其他类型：返回空（禁用 Artists/Albums/Tracks 同步）
            if parent_id == "netease_virtual_library":
                return self._success_response({
                    "Items": [],
                    "TotalRecordCount": 0,
                    "StartIndex": 0
                })
            
            # 3. ParentId 是歌单库：返回用户收藏的歌单
            if parent_id == "netease_playlists_library":
                _LOGGER.info("Jellyfin: 歌单库查询，返回用户收藏的歌单")
                items = []
                try:
                    # 确保 userinfo 已加载
                    await self.cloud_music._ensure_userinfo_loaded()
                    
                    if hasattr(self.cloud_music, 'userinfo') and self.cloud_music.userinfo:
                        uid = self.cloud_music.userinfo.get('uid')
                        if uid:
                            # 获取用户歌单
                            result = await self.cloud_music.netease_cloud_music(f'/user/playlist?uid={uid}')
                            if result and result.get('playlist'):
                                for pl in result['playlist']:
                                    items.append(self._format_jellyfin_playlist(pl))
                                _LOGGER.info(f"Jellyfin: 返回 {len(items)} 个用户歌单")
                        else:
                            _LOGGER.warning("Jellyfin: 用户未登录，无法获取歌单")
                    else:
                        _LOGGER.warning("Jellyfin: userinfo 未加载")
                except Exception as e:
                    _LOGGER.error(f"Jellyfin: 获取用户歌单失败 - {e}", exc_info=True)
                
                return self._success_response({
                    "Items": items,
                    "TotalRecordCount": len(items),
                    "StartIndex": 0
                })
        
        _LOGGER.info(f"Jellyfin Items: 搜索 searchTerm={search_term}, types={include_types}")
        
        from urllib.parse import quote as url_quote
        items = []
        
        # 搜索歌曲
        if 'Audio' in include_types or not include_types:
            try:
                res = await self.cloud_music.netease_cloud_music(
                    f'/cloudsearch?keywords={url_quote(search_term)}&type=1&limit={limit}'
                )
                if res and res.get('result') and res['result'].get('songs'):
                    for song in res['result']['songs'][:limit]:
                        items.append(self._format_jellyfin_song(song))
            except Exception as e:
                _LOGGER.error(f"Jellyfin: 搜索歌曲失败 - {e}")
        
        # 搜索专辑
        if 'MusicAlbum' in include_types or not include_types:
            try:
                res = await self.cloud_music.netease_cloud_music(
                    f'/cloudsearch?keywords={url_quote(search_term)}&type=10&limit={limit}'
                )
                if res and res.get('result') and res['result'].get('albums'):
                    for album in res['result']['albums'][:limit]:
                        items.append(self._format_jellyfin_album(album))
            except Exception as e:
                _LOGGER.error(f"Jellyfin: 搜索专辑失败 - {e}")
        
        # 搜索歌单
        if 'Playlist' in include_types or not include_types:
            try:
                # 特殊关键词 "我的歌单"：返回用户收藏的歌单
                if search_term == "我的歌单":
                    _LOGGER.info("Jellyfin: 搜索'我的歌单'，返回用户收藏的歌单")
                    # 确保 userinfo 已加载
                    await self.cloud_music._ensure_userinfo_loaded()
                    
                    if hasattr(self.cloud_music, 'userinfo') and self.cloud_music.userinfo:
                        uid = self.cloud_music.userinfo.get('uid')
                        if uid:
                            # 获取用户歌单
                            result = await self.cloud_music.netease_cloud_music(f'/user/playlist?uid={uid}')
                            if result and result.get('playlist'):
                                for pl in result['playlist']:
                                    items.append(self._format_jellyfin_playlist(pl))
                                _LOGGER.info(f"Jellyfin: ✅ 返回 {len(result['playlist'])} 个用户歌单")
                        else:
                            _LOGGER.warning("Jellyfin: 用户未登录，无法获取歌单")
                    else:
                        _LOGGER.warning("Jellyfin: userinfo 未加载")
                else:
                    # 普通搜索：搜索公开歌单
                    res = await self.cloud_music.netease_cloud_music(
                        f'/cloudsearch?keywords={url_quote(search_term)}&type=1000&limit={limit}'
                    )
                    if res and res.get('result') and res['result'].get('playlists'):
                        for playlist in res['result']['playlists'][:limit]:
                            items.append(self._format_jellyfin_playlist(playlist))
                        _LOGGER.info(f"Jellyfin: ✅ 歌单搜索成功，找到 {len(res['result']['playlists'])} 个")
            except Exception as e:
                _LOGGER.error(f"Jellyfin: 搜索歌单失败 - {e}", exc_info=True)
        
        return self._success_response({
            "Items": items,
            "TotalRecordCount": len(items),
            "StartIndex": 0
        })
    
    async def handle_search_artists(self, request) -> web.Response:
        """GET /Artists - 艺术家专用端点"""
        search_term = request.query.get('searchTerm', '')
        limit = int(request.query.get('limit', '20'))
        
        _LOGGER.info(f"Jellyfin Artists: 搜索 {search_term}")
        
        if not search_term:
            return self._success_response({"Items": [], "TotalRecordCount": 0, "StartIndex": 0})
        
        from urllib.parse import quote as url_quote
        items = []
        
        try:
            res = await self.cloud_music.netease_cloud_music(
                f'/cloudsearch?keywords={url_quote(search_term)}&type=100&limit={limit}'
            )
            if res and res.get('result') and res['result'].get('artists'):
                for artist in res['result']['artists'][:limit]:
                    items.append(self._format_jellyfin_artist(artist))
        except Exception as e:
            _LOGGER.error(f"Jellyfin Artists: 失败 - {e}")
        
        return self._success_response({
            "Items": items,
            "TotalRecordCount": len(items),
            "StartIndex": 0
        })
    
    async def handle_user_items(self, request) -> web.Response:
        """GET /Users/{userId}/Items 或 /Items - 获取子项（专辑歌曲、艺术家专辑等）"""
        # URL解码parentId
        import urllib.parse
        parent_id_raw = request.query.get('parentId') or request.query.get('ParentId', '')
        parent_id = urllib.parse.unquote(parent_id_raw)
        include_types = request.query.get('includeItemTypes') or request.query.get('IncludeItemTypes', '')
        
        _LOGGER.info(f"Jellyfin Items: ParentId={parent_id} (raw={parent_id_raw}), IncludeItemTypes={include_types}")
        
        items = []
        
        # 1. 专辑 -> 歌曲
        if parent_id.startswith('al_'):
            real_id = parent_id[3:]
            try:
                res = await self.cloud_music.netease_cloud_music(f'/album?id={real_id}')
                if res and res.get('songs'):
                    for song in res['songs']:
                        items.append(self._format_jellyfin_song(song))
            except Exception as e:
                _LOGGER.error(f"Jellyfin Items (Album): 失败 - {e}")
        
        # 2. 艺术家 -> 专辑/歌曲（支持_fake://ar_前缀）
        elif parent_id.startswith('ar_') or parent_id.startswith('_fake://ar_'):
            if parent_id.startswith('_fake://ar_'):
                real_id = parent_id[11:]  # 移除 _fake://ar_
            else:
                real_id = parent_id[3:]
            
            _LOGGER.info(f"🎵 Jellyfin Items (Artist): ParentId={parent_id}, real_id={real_id}, IncludeItemTypes={include_types}")
            
            try:
                # 获取专辑
                if 'MusicAlbum' in include_types:
                    _LOGGER.info(f"📀 获取歌手专辑: /artist/album?id={real_id}")
                    res = await self.cloud_music.netease_cloud_music(f'/artist/album?id={real_id}&limit=50')
                    if res and res.get('hotAlbums'):
                        _LOGGER.info(f"✅ 获取到 {len(res['hotAlbums'])} 个专辑")
                        for album in res['hotAlbums']:
                            items.append(self._format_jellyfin_album(album))
                    else:
                        _LOGGER.warning(f"❌ 未获取到专辑，API响应: {res}")
                
                # 获取热门歌曲 (当请求 Audio 类型或无类型限制时)
                if 'Audio' in include_types or not include_types:
                    _LOGGER.info(f"🎤 获取歌手热门歌曲: /artist/top/song?id={real_id}, IncludeItemTypes={include_types}")
                    res = await self.cloud_music.netease_cloud_music(f'/artist/top/song?id={real_id}')
                    _LOGGER.info(f"API响应keys: {list(res.keys()) if res else 'None'}")
                    if res and res.get('songs'):
                        _LOGGER.info(f"✅ 获取到 {len(res['songs'])} 首热门歌曲")
                        for song in res['songs']:
                            items.append(self._format_jellyfin_song(song))
                    else:
                        _LOGGER.warning(f"❌ 未获取到热门歌曲，完整响应: {res}")
            except Exception as e:
                _LOGGER.error(f"Jellyfin Items (Artist): 失败 - {e}", exc_info=True)
        
        # 3. 歌单 -> 歌曲
        elif parent_id.startswith('pl_'):
            real_id = parent_id[3:]
            try:
                res = await self.cloud_music.netease_cloud_music(f'/playlist/track/all?id={real_id}')
                if res and res.get('songs'):
                    for song in res['songs']:
                        items.append(self._format_jellyfin_song(song))
            except Exception as e:
                _LOGGER.error(f"Jellyfin Items (Playlist): 失败 - {e}")

        _LOGGER.info(f"📊 Jellyfin Items 返回: {len(items)} 个项目 (ParentId={parent_id})")
        return self._success_response({
            "Items": items,
            "TotalRecordCount": len(items),
            "StartIndex": 0
        })

    async def handle_playlist_items(self, request, playlist_id: str) -> web.Response:
        """GET /Playlists/{id}/Items"""
        real_id = playlist_id[3:] if playlist_id.startswith('pl_') else playlist_id
        
        # 分页参数
        start_index = int(request.query.get('startIndex', 0))
        limit = int(request.query.get('limit', 100))
        
        items = []
        total_count = 0
        
        try:
            result = await self.cloud_music.netease_cloud_music(f'/playlist/track/all?id={real_id}')
            if result and result.get('songs'):
                all_songs = result['songs']
                total_count = len(all_songs)
                
                # 应用分页
                end_index = start_index + limit
                page_songs = all_songs[start_index:end_index]
                
                for song in page_songs:
                    items.append(self._format_jellyfin_song(song))
                
                _LOGGER.info(f"Jellyfin Playlist: {playlist_id} 返回 {len(items)}/{total_count} 首歌曲 (offset={start_index})")
        except Exception as e:
            _LOGGER.error(f"Jellyfin Playlists: 失败 - {e}")
        
        return self._success_response({
            "Items": items,
            "TotalRecordCount": total_count,
            "StartIndex": start_index
        })
    
    async def handle_get_item(self, request, item_id: str) -> web.Response:
        """
        GET /Users/{userId}/Items/{itemId}
        获取单个项目的完整信息
        """
        # URL解码item_id（处理_fake://等特殊字符）
        import urllib.parse
        decoded_id = urllib.parse.unquote(item_id)
        
        _LOGGER.info(f"⚡ Jellyfin GET_ITEM: {item_id} -> decoded: {decoded_id}")
        
        # 解析解码后的ID
        if decoded_id.startswith('_fake://ar_'):
            item_type = 'ar_'
            real_id = decoded_id[11:]  # 移除 _fake://ar_
        elif decoded_id.startswith('s_'):
            item_type = 's_'
            real_id = decoded_id[2:]
        elif decoded_id.startswith('al_'):
            item_type = 'al_'
            real_id = decoded_id[3:]
        elif decoded_id.startswith('ar_'):
            item_type = 'ar_'
            real_id = decoded_id[3:]
        elif decoded_id.startswith('pl_'):
            item_type = 'pl_'
            real_id = decoded_id[3:]
        else:
            item_type = None
            real_id = decoded_id
        
        _LOGGER.debug(f"Item type: {item_type}, real_id: {real_id}")
        
        try:
            # 歌曲
            if decoded_id.startswith('s_'):
                _LOGGER.info(f"Jellyfin GET_ITEM: 获取歌曲详情 real_id={real_id}")
                res = await self.cloud_music.netease_cloud_music(f'/song/detail?ids={real_id}')
                
                if res and res.get('songs'):
                    song_data = self._format_jellyfin_song(res['songs'][0])
                    _LOGGER.info(f"✅ Jellyfin GET_ITEM: 歌曲找到 Name={song_data.get('Name')}")
                    return self._success_response(song_data)
                else:
                    _LOGGER.warning(f"❌ Jellyfin GET_ITEM: 歌曲未找到 real_id={real_id}, res={res}")
            
            # 专辑
            elif decoded_id.startswith('al_'):
                res = await self.cloud_music.netease_cloud_music(f'/album?id={real_id}')
                if res and res.get('album'):
                    album_data = {
                        'id': res['album'].get('id'),
                        'name': res['album'].get('name'),
                        'artist': res['album'].get('artist'),
                        'artists': res['album'].get('artists'),
                        'publishTime': res['album'].get('publishTime'),
                        'size': res['album'].get('size')
                    }
                    return self._success_response(self._format_jellyfin_album(album_data))
                else:
                    # 专辑不存在时返回虚拟专辑（让播放继续）
                    _LOGGER.warning(f"Album {real_id} not found, returning virtual album")
                    virtual_album = {
                        "Id": item_id,
                        "Name": "未知专辑",
                        "Type": "MusicAlbum",
                        "AlbumArtist": "未知艺术家",
                        "AlbumArtists": [],
                        "Artists": [],
                        "ArtistItems": [],
                        "ProductionYear": 0,
                        "ImageTags": {},
                        "BackdropImageTags": [],
                        "ProviderIds": {},
                        "ChildCount": 0,
                        "UserData": {"IsFavorite": False}
                    }
                    return self._success_response(virtual_album)
            
            # 艺术家 (支持 _fake://ar_ 前缀)
            elif item_type == 'ar_':
                # 处理虚拟艺术家 (ar_0, ar_fake_xxx 等)
                if real_id in ('0', '') or real_id.startswith('fake_'):
                    _LOGGER.info(f"Jellyfin: 返回虚拟艺术家 {item_id}")
                    virtual_artist = {
                        "Id": item_id,
                        "Name": "未知艺术家",
                        "Type": "MusicArtist",
                        "ImageTags": {},
                        "BackdropImageTags": [],
                        "ProviderIds": {},
                        "UserData": {"IsFavorite": False}
                    }
                    return self._success_response(virtual_artist)
                
                res = await self.cloud_music.netease_cloud_music(f'/artist/detail?id={real_id}')
                if res and res.get('data'):
                    artist_data = {
                        'id': res['data'].get('artist', {}).get('id'),
                        'name': res['data'].get('artist', {}).get('name')
                    }
                    return self._success_response(self._format_jellyfin_artist(artist_data))
                else:
                    # 艺术家不存在时返回虚拟艺术家
                    _LOGGER.warning(f"Artist {real_id} not found, returning virtual artist")
                    virtual_artist = {
                        "Id": item_id,
                        "Name": "未知艺术家",
                        "Type": "MusicArtist",
                        "ImageTags": {},
                        "BackdropImageTags": [],
                        "ProviderIds": {},
                        "UserData": {"IsFavorite": False}
                    }
                    return self._success_response(virtual_artist)
            
            # 歌单
            elif decoded_id.startswith('pl_'):
                res = await self.cloud_music.netease_cloud_music(f'/playlist/detail?id={real_id}')
                if res and res.get('playlist'):
                    playlist_data = {
                        'id': res['playlist'].get('id'),
                        'name': res['playlist'].get('name'),
                        'creator': res['playlist'].get('creator'),
                        'trackCount': res['playlist'].get('trackCount')
                    }
                    return self._success_response(self._format_jellyfin_playlist(playlist_data))
        
        except Exception as e:
            _LOGGER.error(f"❌ Jellyfin GET_ITEM exception {item_id}: {e}", exc_info=True)
        
        _LOGGER.error(f"❌ Jellyfin GET_ITEM 404: {item_id}")
        return web.json_response({"error": f"Item {item_id} not found"}, status=404)
    
    async def handle_get_image(self, request, item_id: str, image_type: str) -> web.Response:
        """GET /Items/{itemId}/Images/{imageType}"""
        # URL 解码并解析 ID（与 handle_get_item 保持一致）
        import urllib.parse
        decoded_id = urllib.parse.unquote(item_id)
        
        _LOGGER.info(f"⚡ Jellyfin GET_IMAGE: {item_id} -> decoded: {decoded_id}")
        
        # 解析 ID 类型和真实 ID
        if decoded_id.startswith('_fake://ar_'):
            item_type = 'ar'
            real_id = decoded_id[11:]  # 移除 _fake://ar_
        elif decoded_id.startswith('s_'):
            item_type = 's'
            real_id = decoded_id[2:]
        elif decoded_id.startswith('al_'):
            item_type = 'al'
            real_id = decoded_id[3:]
        elif decoded_id.startswith('ar_'):
            item_type = 'ar'
            real_id = decoded_id[3:]
        elif decoded_id.startswith('pl_'):
            item_type = 'pl'
            real_id = decoded_id[3:]
        else:
            _LOGGER.warning(f"❌ Jellyfin GET_IMAGE: 未知 ID 格式 {decoded_id}")
            return web.Response(status=404)
        
        _LOGGER.debug(f"Image type: {item_type}, real_id: {real_id}")
        
        try:
            # 歌曲封面
            if item_type == 's':
                res = await self.cloud_music.netease_cloud_music(f'/song/detail?ids={real_id}')
                if res and res.get('songs'):
                    pic_url = res['songs'][0].get('al', {}).get('picUrl', '')
                    if pic_url:
                        _LOGGER.info(f"✅ Jellyfin GET_IMAGE: 歌曲封面 {pic_url[:50]}...")
                        raise web.HTTPFound(pic_url)
            
            # 专辑封面
            elif item_type == 'al':
                res = await self.cloud_music.netease_cloud_music(f'/album?id={real_id}')
                if res and res.get('album'):
                    pic_url = res['album'].get('picUrl', '')
                    if pic_url:
                        _LOGGER.info(f"✅ Jellyfin GET_IMAGE: 专辑封面 {pic_url[:50]}...")
                        raise web.HTTPFound(pic_url)
            
            # 歌手封面
            elif item_type == 'ar':
                res = await self.cloud_music.netease_cloud_music(f'/artist/detail?id={real_id}')
                if res and res.get('data'):
                    pic_url = res['data'].get('artist', {}).get('cover', '')
                    if pic_url:
                        _LOGGER.info(f"✅ Jellyfin GET_IMAGE: 歌手封面 {pic_url[:50]}...")
                        raise web.HTTPFound(pic_url)
            
            # 歌单封面
            elif item_type == 'pl':
                res = await self.cloud_music.netease_cloud_music(f'/playlist/detail?id={real_id}')
                if res and res.get('playlist'):
                    pic_url = res['playlist'].get('coverImgUrl', '')
                    if pic_url:
                        _LOGGER.info(f"✅ Jellyfin GET_IMAGE: 歌单封面 {pic_url[:50]}...")
                        raise web.HTTPFound(pic_url)
        
        except web.HTTPFound:
            raise  # 重新抛出重定向异常
        except Exception as e:
            _LOGGER.error(f"❌ Jellyfin GET_IMAGE 异常: {e}", exc_info=True)
        
        _LOGGER.warning(f"❌ Jellyfin GET_IMAGE 404: {decoded_id}")
        return web.Response(status=404)
    
    async def handle_audio_stream(self, request, item_id: str) -> web.Response:
        """
        GET /Audio/{itemId}/universal
        处理音频流请求，返回重定向到实际播放 URL
        """
        _LOGGER.info(f"Jellyfin Audio: 请求 item_id={item_id}")
        
        # 提取真实歌曲 ID (s_123456 -> 123456)
        if item_id.startswith('s_'):
            real_id = item_id[2:]
        else:
            real_id = item_id
        
        _LOGGER.info(f"Jellyfin Audio: 解析后 real_id={real_id}")
        
        try:
            # song_url 返回 (url, fee) 元组
            url, fee = await self.cloud_music.song_url(int(real_id))
            if url:
                _LOGGER.info(f"Jellyfin Audio: 获取到播放URL (fee={fee}), url={url[:50]}...")
                raise web.HTTPFound(url)
            else:
                _LOGGER.warning(f"Jellyfin Audio: 歌曲 {real_id} 无可用 URL")
        except web.HTTPFound:
            raise  # 重新抛出重定向异常
        except ValueError as e:
            _LOGGER.error(f"Jellyfin Audio: 无效的歌曲ID {real_id} - {e}")
        except Exception as e:
            _LOGGER.error(f"Jellyfin Audio: 获取播放URL失败 - {e}", exc_info=True)
        
        return web.Response(status=404)
