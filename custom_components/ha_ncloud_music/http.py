import base64
import time
import requests
from urllib.parse import parse_qsl, quote
from homeassistant.components.http import HomeAssistantView
from aiohttp import web
from .models.music_info import MusicSource
from .manifest import manifest

DOMAIN = manifest.domain

# 缓存过期时间（秒）
CACHE_EXPIRE_SECONDS = 300  # 5 分钟

class HttpView(HomeAssistantView):

    url = "/cloud_music/url"
    name = f"cloud_music:url"
    requires_auth = False

    play_key = None
    play_url = None
    play_time = None  # 缓存时间戳

    async def get(self, request):

        hass = request.app["hass"]
        cloud_music = hass.data['cloud_music']

        query = {}
        data = request.query.get('data')
        if data is not None:
            decoded_data = base64.b64decode(data).decode('utf-8')
            qsl = parse_qsl(decoded_data)
            for q in qsl:
                query[q[0]] = q[1]

        id = query.get('id')
        source = query.get('source')
        song = query.get('song')
        singer = query.get('singer')

        not_found_tips = quote(f'当前没有找到编号是{id}，歌名为{song}，作者是{singer}的播放链接')
        play_url = f'http://fanyi.baidu.com/gettts?lan=zh&text={not_found_tips}&spd=5&source=web'

        # 缓存KEY + 过期检查
        play_key = f'{id}{song}{singer}{source}'
        current_time = time.time()
        cache_valid = (
            self.play_key == play_key 
            and self.play_time is not None 
            and (current_time - self.play_time) < CACHE_EXPIRE_SECONDS
        )
        if cache_valid:
            return web.HTTPFound(self.play_url)

        source = int(source)
        if source == MusicSource.PLAYLIST.value \
                or source == MusicSource.ARTISTS.value \
                or source == MusicSource.DJRADIO.value \
                or source == MusicSource.CLOUD.value:
            # 获取播放链接
            url, fee = await cloud_music.song_url(id)
            if url is not None:
                # 收费音乐
                if fee == 1:
                    url = await hass.async_add_executor_job(self.getVipMusic, id)
                    if url is None or url == '':
                        result = await cloud_music.async_music_source(song, singer)
                        if result is not None:
                            url = result.url

                play_url = url
            else:
                # 从云盘里获取
                url = await cloud_music.cloud_song_url(id)
                if url is not None:
                    play_url = url
                else:
                    result = await cloud_music.async_music_source(song, singer)
                    if result is not None:
                        play_url = result.url

        self.play_key = play_key
        self.play_url = play_url
        self.play_time = time.time()  # 记录缓存时间
        # 重定向到可播放链接
        return web.HTTPFound(play_url)

    # VIP音乐资源
    def getVipMusic(self, id):
        try:
            res = requests.post('https://music.dogged.cn/api.php', data={
                'types': 'url',
                'id': id,
                'source': 'netease'
            })
            data = res.json()
            return data.get('url')
        except Exception as ex:
            pass


class CloudMusicApiView(HomeAssistantView):
    """
    统一 API 入口 (Layer 2 Proxy)
    
    解决问题：
    1. 外网穿透 - 复用 HA 安全通道
    2. CORS/HTTPS - 同源请求
    3. 认证 - 自动携带 Cookie
    
    用法：
        /cloud_music/api?action=lyric&id=123456
        /cloud_music/api?action=song_detail&id=123456
    """
    
    url = "/cloud_music/api"
    name = "cloud_music:api"
    requires_auth = False  # 前端卡片需要无认证访问

    async def get(self, request):
        hass = request.app["hass"]
        cloud_music = hass.data.get('cloud_music')
        
        if cloud_music is None:
            return web.json_response({'error': 'Cloud Music not initialized'}, status=503)
        
        action = request.query.get('action')
        song_id = request.query.get('id')
        
        if not action:
            return web.json_response({'error': 'Missing action parameter'}, status=400)
        
        # 歌词
        if action == 'lyric':
            if not song_id:
                return web.json_response({'error': 'Missing id parameter'}, status=400)
            result = await cloud_music.async_get_lyric(song_id)
            # 总是返回结构化数据，前端根据 type 判断可用性
            return web.json_response(result)
        
        # 歌曲详情（预留）
        elif action == 'song_detail':
            if not song_id:
                return web.json_response({'error': 'Missing id parameter'}, status=400)
            result = await cloud_music.netease_cloud_music(f'/song/detail?ids={song_id}')
            return web.json_response(result)
        
        # 未知 action
        else:
            return web.json_response({
                'error': f'Unknown action: {action}',
                'available_actions': ['lyric', 'song_detail']
            }, status=400)


class CloudMusicQRCodeView(HomeAssistantView):
    """Serve the cached QR login image locally for the HA media browser."""

    url = "/cloud_music/qrcode"
    name = "cloud_music:qrcode"
    requires_auth = False

    async def get(self, request):
        hass = request.app["hass"]
        cloud_music = hass.data.get('cloud_music')
        if cloud_music is None:
            return web.Response(status=503)

        qr = cloud_music.login_qrcode
        if request.query.get('key') != qr.get('key'):
            return web.Response(status=404)

        qrimg = qr.get('img')
        if not qrimg:
            return web.Response(status=404)

        if ',' in qrimg:
            qrimg = qrimg.split(',', 1)[1]

        try:
            image = base64.b64decode(qrimg)
        except Exception:
            return web.Response(status=500)

        return web.Response(
            body=image,
            content_type='image/png',
            headers={'Cache-Control': 'no-store'}
        )
