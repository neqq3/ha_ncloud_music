from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_URL
import voluptuous as vol
import logging

import asyncio
from .const import PLATFORMS
from .manifest import manifest
from .http import HttpView, CloudMusicApiView, CloudMusicQRCodeView
from .cloud_music import CloudMusic

DOMAIN = manifest.domain
_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.deprecated(DOMAIN)

# ==================== Service Call 定义 ====================
# 服务参数 Schema
SERVICE_SEARCH_SCHEMA = vol.Schema({
    vol.Required('keyword'): cv.string,
    vol.Optional('type', default='song'): vol.In(['song', 'artist', 'playlist', 'djradio', 'album']),
    vol.Optional('entity_id'): cv.entity_id,
})

SERVICE_PLAY_BY_ID_SCHEMA = vol.Schema({
    vol.Required('id'): cv.string,
    vol.Required('type'): vol.In(['song', 'playlist', 'album', 'artist', 'djradio']),
    vol.Optional('entity_id'): cv.entity_id,
})

SERVICE_QUICK_PLAY_SCHEMA = vol.Schema({
    vol.Optional('entity_id'): cv.entity_id,
})

# FM 服务参数 Schema
SERVICE_PLAY_FM_SCHEMA = vol.Schema({
    vol.Optional('mode', default='默认推荐'): cv.string,
    vol.Optional('entity_id'): cv.entity_id,
})

SERVICE_FM_TRASH_SCHEMA = vol.Schema({
    vol.Optional('entity_id'): cv.entity_id,
})

SERVICE_STOP_SCHEMA = vol.Schema({
    vol.Optional('entity_id'): cv.entity_id,
})


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    data = entry.data
    api_url = data.get(CONF_URL)
    vip_url = entry.options.get(CONF_URL, '')
    
    # 读取音质配置
    from .const import CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY
    audio_quality = entry.options.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY)
    
    cloud_music = CloudMusic(hass, api_url, vip_url, audio_quality)
    # 立即加载用户信息（避免第一次访问时延迟）
    await cloud_music._ensure_userinfo_loaded()
    hass.data['cloud_music'] = cloud_music
    
    # 初始化共享搜索数据存储（用于 text、button、select 实体间的数据共享）
    from .const import DATA_SEARCH_RESULTS, DATA_LAST_UPDATE, DATA_KEYWORD
    search_data_key = f'{DOMAIN}_{entry.entry_id}_search_data'
    hass.data[search_data_key] = {
        DATA_SEARCH_RESULTS: [],
        DATA_LAST_UPDATE: 0,
        DATA_KEYWORD: ''
    }

    hass.http.register_view(HttpView)
    hass.http.register_view(CloudMusicApiView)
    hass.http.register_view(CloudMusicQRCodeView)
    
    # 注册 Subsonic API 视图（可选，异常隔离）
    try:
        from .subsonic import SubsonicApiView
        hass.http.register_view(SubsonicApiView)
        _LOGGER.info("✅ Subsonic API 已启用: /rest/rest/")
    except Exception as e:
        _LOGGER.warning(f"Subsonic API 启用失败（不影响主功能）: {e}")
    
    # 注册 Jellyfin API 视图（可选，异常隔离）
    try:
        from .http_jellyfin import JellyfinApiView
        hass.http.register_view(JellyfinApiView(cloud_music))
        _LOGGER.info("✅ Jellyfin API 已启用: /jellyfin/*")
    except Exception as e:
        _LOGGER.warning(f"Jellyfin API 启用失败（不影响主功能）: {e}")
    
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    
    # ==================== 注册 Service Call ====================
    async def _get_media_player(entity_id: str = None):
        """获取媒体播放器实体"""
        if entity_id:
            return hass.states.get(entity_id)
        # 默认查找集成创建的播放器
        for state in hass.states.async_all('media_player'):
            if DOMAIN in state.entity_id or 'yun_yin_le' in state.entity_id:
                return state
        return None
    
    async def _play_media_uri(entity_id: str, media_uri: str):
        """调用 media_player.play_media 播放指定 URI"""
        await hass.services.async_call(
            'media_player',
            'play_media',
            {
                'entity_id': entity_id,
                'media_content_id': media_uri,
                'media_content_type': 'music',
            }
        )
    
    async def handle_search(call: ServiceCall):
        """
        Service: ha_ncloud_music.search
        搜索并自动播放第一条结果（搜索即播放）
        """
        keyword = call.data.get('keyword')
        search_type = call.data.get('type', 'song')
        entity_id = call.data.get('entity_id')
        
        _LOGGER.info(f"🔍 Service Call: search - keyword='{keyword}', type='{search_type}'")
        
        # 获取播放器
        player = await _get_media_player(entity_id)
        if not player:
            _LOGGER.error("找不到可用的媒体播放器")
            hass.components.persistent_notification.async_create(
                f"搜索失败：找不到可用的播放器",
                title="云音乐",
                notification_id="ha_ncloud_music_error"
            )
            return
        
        target_entity_id = entity_id or player.entity_id
        
        # 构建 cloudmusic:// URI（复用原有的 URI 协议）
        type_uri_map = {
            'song': 'cloudmusic://play/song',
            'artist': 'cloudmusic://play/singer',
            'playlist': 'cloudmusic://play/list',
            'djradio': 'cloudmusic://play/radio',
            'album': 'cloudmusic://play/list',  # 专辑暂用歌单搜索
        }
        
        from urllib.parse import quote
        media_uri = f"{type_uri_map[search_type]}?kv={quote(keyword)}"
        
        _LOGGER.info(f"🎵 播放: {media_uri} -> {target_entity_id}")
        
        try:
            await _play_media_uri(target_entity_id, media_uri)
        except Exception as e:
            _LOGGER.error(f"播放失败: {e}")
            hass.components.persistent_notification.async_create(
                f"搜索 '{keyword}' 失败：{e}",
                title="云音乐",
                notification_id="ha_ncloud_music_error"
            )
    
    async def handle_play_by_id(call: ServiceCall):
        """
        Service: ha_ncloud_music.play_by_id
        通过 ID 精准播放
        """
        resource_id = call.data.get('id')
        resource_type = call.data.get('type')
        entity_id = call.data.get('entity_id')
        
        _LOGGER.info(f"🎯 Service Call: play_by_id - id='{resource_id}', type='{resource_type}'")
        
        # 获取播放器
        player = await _get_media_player(entity_id)
        if not player:
            _LOGGER.error("找不到可用的媒体播放器")
            return
        
        target_entity_id = entity_id or player.entity_id
        
        # 构建 URI
        type_uri_map = {
            'song': f'cloudmusic://163/single/song?id={resource_id}',
            'playlist': f'cloudmusic://163/playlist?id={resource_id}',
            'album': f'cloudmusic://163/album/playlist?id={resource_id}',
            'artist': f'cloudmusic://163/artist/playlist?id={resource_id}',
            'djradio': f'cloudmusic://163/radio/playlist?id={resource_id}',
        }
        
        media_uri = type_uri_map.get(resource_type)
        if not media_uri:
            _LOGGER.error(f"不支持的资源类型: {resource_type}")
            return
        
        _LOGGER.info(f"🎵 播放: {media_uri} -> {target_entity_id}")
        await _play_media_uri(target_entity_id, media_uri)
    
    async def handle_play_daily(call: ServiceCall):
        """
        Service: ha_ncloud_music.play_daily
        播放每日推荐
        """
        entity_id = call.data.get('entity_id')
        _LOGGER.info("📅 Service Call: play_daily")
        
        player = await _get_media_player(entity_id)
        if not player:
            _LOGGER.error("找不到可用的媒体播放器")
            return
        
        target_entity_id = entity_id or player.entity_id
        await _play_media_uri(target_entity_id, 'cloudmusic://163/my/daily')
    
    async def handle_play_favorites(call: ServiceCall):
        """
        Service: ha_ncloud_music.play_favorites
        播放我喜欢的音乐
        """
        entity_id = call.data.get('entity_id')
        _LOGGER.info("❤️ Service Call: play_favorites")
        
        player = await _get_media_player(entity_id)
        if not player:
            _LOGGER.error("找不到可用的媒体播放器")
            return
        
        target_entity_id = entity_id or player.entity_id
        await _play_media_uri(target_entity_id, 'cloudmusic://163/my/ilike')
    
    async def _get_media_player_entity(entity_id: str = None):
        """获取媒体播放器实体对象"""
        entity_registry = hass.data.get("entity_components", {}).get('media_player')
        if entity_registry:
            for entity in entity_registry.entities:
                if hasattr(entity, '_is_fm_playing'):  # CloudMusicMediaPlayer 特征
                    if entity_id is None or entity.entity_id == entity_id:
                        return entity
        return None
    
    async def handle_play_fm(call: ServiceCall):
        """
        Service: ha_ncloud_music.play_fm
        播放私人 FM
        """
        mode = call.data.get('mode', '默认推荐')
        entity_id = call.data.get('entity_id')
        
        _LOGGER.info(f"🎵 Service Call: play_fm - mode='{mode}'")
        
        media_player_obj = await _get_media_player_entity(entity_id)
        if not media_player_obj:
            _LOGGER.error("找不到云音乐媒体播放器")
            hass.components.persistent_notification.async_create(
                f"播放私人 FM 失败：找不到播放器",
                title="云音乐",
                notification_id="ha_ncloud_music_error"
            )
            return
        
        try:
            await media_player_obj.async_play_fm(mode)
        except Exception as e:
            _LOGGER.error(f"播放私人 FM 失败: {e}")
            hass.components.persistent_notification.async_create(
                f"播放私人 FM 失败：{e}",
                title="云音乐",
                notification_id="ha_ncloud_music_error"
            )
    
    async def handle_fm_trash(call: ServiceCall):
        """
        Service: ha_ncloud_music.fm_trash
        不喜欢当前歌曲并跳到下一首
        """
        entity_id = call.data.get('entity_id')
        
        _LOGGER.info("🗑️ Service Call: fm_trash")
        
        media_player_obj = await _get_media_player_entity(entity_id)
        if not media_player_obj:
            _LOGGER.error("找不到云音乐媒体播放器")
            return
        
        if not media_player_obj._is_fm_playing:
            _LOGGER.warning("当前不在 FM 模式，无法执行垃圾桶操作")
            hass.components.persistent_notification.async_create(
                "只有在私人 FM 模式下才能使用此功能",
                title="FM 不喜欢",
                notification_id="ha_ncloud_music_fm_trash"
            )
            return
        
        try:
            await media_player_obj.async_fm_trash()
        except Exception as e:
            _LOGGER.error(f"FM 垃圾桶操作失败: {e}")

    async def handle_stop(call: ServiceCall):
        """
        Service: ha_ncloud_music.stop
        停止播放
        """
        entity_id = call.data.get('entity_id')
        _LOGGER.info("⏹️ Service Call: stop")

        player = await _get_media_player(entity_id)
        if not player:
            _LOGGER.error("找不到可用的媒体播放器")
            return

        target_entity_id = entity_id or player.entity_id
        await hass.services.async_call(
            'media_player',
            'media_stop',
            {'entity_id': target_entity_id}
        )
    
    # 注册服务
    hass.services.async_register(
        DOMAIN, 'search', handle_search, schema=SERVICE_SEARCH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, 'play_by_id', handle_play_by_id, schema=SERVICE_PLAY_BY_ID_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, 'play_daily', handle_play_daily, schema=SERVICE_QUICK_PLAY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, 'play_favorites', handle_play_favorites, schema=SERVICE_QUICK_PLAY_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, 'play_fm', handle_play_fm, schema=SERVICE_PLAY_FM_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, 'fm_trash', handle_fm_trash, schema=SERVICE_FM_TRASH_SCHEMA
    )
    hass.services.async_register(
        DOMAIN, 'stop', handle_stop, schema=SERVICE_STOP_SCHEMA
    )
    
    _LOGGER.info("✅ 已注册 Service Call: search, play_by_id, play_daily, play_favorites, play_fm, fm_trash, stop")
    
    return True

async def update_listener(hass, entry):
    await async_unload_entry(hass, entry)
    await asyncio.sleep(1)
    await async_setup_entry(hass, entry)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # 注销服务
    hass.services.async_remove(DOMAIN, 'search')
    hass.services.async_remove(DOMAIN, 'play_by_id')
    hass.services.async_remove(DOMAIN, 'play_daily')
    hass.services.async_remove(DOMAIN, 'play_favorites')
    hass.services.async_remove(DOMAIN, 'play_fm')
    hass.services.async_remove(DOMAIN, 'fm_trash')
    hass.services.async_remove(DOMAIN, 'stop')
    
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
