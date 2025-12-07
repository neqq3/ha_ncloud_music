"""Button platform for ha_ncloud_music integration.
提供搜索触发按钮和快捷操作按钮（每日推荐、我喜欢的音乐等）。
"""
import logging
import time
from homeassistant.components.button import ButtonEntity
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN, MediaType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    ENTITY_NAME_SEARCH_BUTTON,
    ENTITY_NAME_DAILY_RECOMMEND,
    ENTITY_NAME_MY_FAVORITES,
    ENTITY_NAME_SEARCH_INPUT,
    ENTITY_NAME_SEARCH_TYPE,
    DATA_SEARCH_RESULTS,
    DATA_LAST_UPDATE,
    DATA_KEYWORD,
    DATA_SEARCH_TYPE,
    URI_DAILY_RECOMMEND,
    URI_MY_FAVORITES,
    SEARCH_TYPE_MAP,
    SEARCH_TYPE_SONG,
    SEARCH_TYPE_ALBUM,
)
from .manifest import manifest

_LOGGER = logging.getLogger(__name__)

DOMAIN = manifest.domain


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置 button 实体平台"""
    async_add_entities([
        CloudMusicSearchButton(hass, entry),
        CloudMusicDailyRecommendButton(hass, entry),
        CloudMusicMyFavoritesButton(hass, entry),
    ])


class CloudMusicButton(ButtonEntity):
    """云音乐按钮基类"""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, entity_name: str, friendly_name: str, icon: str) -> None:
        """初始化按钮实体"""
        self.hass = hass
        self._entry = entry
        self._attr_name = f"{manifest.name} {friendly_name}"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{entity_name}"
        self._attr_icon = icon

    @property
    def device_info(self):
        """返回设备信息"""
        return {
            "identifiers": {(DOMAIN, self._entry.entry_id)},
            "name": manifest.name,
            "manufacturer": "shaonianzhentan",
            "model": "Cloud Music",
            "sw_version": manifest.version,
        }


class CloudMusicSearchButton(CloudMusicButton):
    """搜索触发按钮
    
    点击时读取 Text 实体的关键词，调用 API 搜索，并将结果存储到共享数据中。
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, ENTITY_NAME_SEARCH_BUTTON, "搜索", "mdi:cloud-search")

    async def async_press(self) -> None:
        """执行搜索操作"""
        # 1. 读取 Text 实体的搜索关键词
        # entity_id 与 text.py 中显式设置的保持一致
        text_entity_id = f"text.{DOMAIN}_{ENTITY_NAME_SEARCH_INPUT}"
        text_state = self.hass.states.get(text_entity_id)
        
        if text_state is None:
            _LOGGER.warning(f"搜索输入实体 {text_entity_id} 不存在")
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": "搜索输入实体未找到，请重新加载集成",
                    "title": "云音乐搜索失败"
                }
            )
            return

        keyword = text_state.state
        if not keyword or keyword.strip() == "":
            _LOGGER.info("搜索关键词为空，跳过搜索")
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": "请先输入搜索关键词",
                    "title": "云音乐搜索提示"
                }
            )
            return

        # 2. 读取搜索类型
        type_entity_id = f"select.{DOMAIN}_{ENTITY_NAME_SEARCH_TYPE}"
        type_state = self.hass.states.get(type_entity_id)
        search_type_name = type_state.state if type_state else "歌曲"
        
        # 获取 API 参数
        search_config = SEARCH_TYPE_MAP.get(search_type_name, SEARCH_TYPE_MAP["歌曲"])
        api_type = search_config["type"]
        search_key = search_config["key"]

        # 3. 获取 CloudMusic API 实例
        cloud_music = self.hass.data.get('cloud_music')
        if cloud_music is None:
            _LOGGER.error("CloudMusic 实例未找到")
            return

        # 3. 调用搜索 API
        _LOGGER.info(f"开始搜索: 类型={search_type_name}, 关键词={keyword}")
        try:
            # 使用 /cloudsearch API（文档说明它比 /search 更全）
            # 支持 type 参数：1=单曲, 10=专辑, 100=歌手, 1000=歌单, 1009=电台
            res = await cloud_music.netease_cloud_music(f'/cloudsearch?keywords={keyword}&type={api_type}&limit=20')
            
            if res.get('code') != 200:
                _LOGGER.warning(f"搜索 API 返回异常: {res}")
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": f"搜索失败: {res.get('message', '未知错误')}",
                        "title": "云音乐搜索失败"
                    }
                )
                return

            # 4. 根据搜索类型获取对应的结果字段
            result_data = res.get('result', {})
            
            # 不同类型的API返回字段不同
            if search_key == SEARCH_TYPE_SONG:
                items = result_data.get('songs', [])
                item_type_name = "歌曲"
            elif search_key == SEARCH_TYPE_ALBUM:
                items = result_data.get('albums', [])
                item_type_name = "专辑"
            elif search_key == SEARCH_TYPE_ARTIST:
                items = result_data.get('artists', [])
                item_type_name = "歌手"
            elif search_key == SEARCH_TYPE_PLAYLIST:
                items = result_data.get('playlists', [])
                item_type_name = "歌单"
            else:  # SEARCH_TYPE_RADIO
                items = result_data.get('djRadios', [])
                item_type_name = "电台"
            
            _LOGGER.debug(f"API返回了 {len(items)} 个{item_type_name}结果")
            
            if not items:
                _LOGGER.info(f"未找到搜索结果: {keyword}")
                # 存储空结果
                search_data_key = f'{DOMAIN}_{self._entry.entry_id}_search_data'
                self.hass.data[search_data_key][DATA_SEARCH_RESULTS] = []
                self.hass.data[search_data_key][DATA_KEYWORD] = keyword
                self.hass.data[search_data_key][DATA_SEARCH_TYPE] = search_key
                self.hass.data[search_data_key][DATA_LAST_UPDATE] = time.time()
                
                await self.hass.services.async_call(
                    "persistent_notification",
                    "create",
                    {
                        "message": f"未找到相关{item_type_name}: {keyword}",
                        "title": "云音乐搜索结果"
                    }
                )
                return

            # 5. 格式化结果 - 根据类型使用不同的处理方式
            from .models.music_info import MusicInfo, MusicSource
            music_list = []
            
            if search_key == SEARCH_TYPE_SONG:
                # 歌曲类型：创建MusicInfo对象，可以直接播放
                for item in items[:20]:
                    song_id = item['id']
                    song_name = item['name']
                    singer_name = item['ar'][0]['name'] if item.get('ar') else '未知歌手'
                    album_name = item['al']['name'] if item.get('al') else ''
                    duration = item.get('dt', 0)
                    pic_url = item['al']['picUrl'] if item.get('al') else ''
                    url = cloud_music.get_play_url(song_id, song_name, singer_name, MusicSource.PLAYLIST.value)
                    music_info = MusicInfo(song_id, song_name, singer_name, album_name, duration, url, pic_url, MusicSource.PLAYLIST.value)
                    music_list.append(music_info)
            else:
                # 其他类型：存储基本信息和媒体库URI，选择后打开媒体库
                for item in items[:20]:
                    item_id = item['id']
                    item_name = item['name']
                    
                    # 根据类型构建媒体库URI和显示名称
                    if search_key == SEARCH_TYPE_PLAYLIST:
                        media_uri = f"cloudmusic://163/playlist/{item_id}"
                        cover_url = item.get('coverImgUrl', '')
                        creator = item.get('creator', {}).get('nickname', '')
                        display_name = f"{item_name} [{creator}]"
                    elif search_key == SEARCH_TYPE_ARTIST:
                        media_uri = f"cloudmusic://163/artists/{item_id}"
                        cover_url = item.get('picUrl', '')
                        album_size = item.get('albumSize', 0)
                        display_name = f"{item_name} ({album_size}张专辑)"
                    elif search_key == SEARCH_TYPE_ALBUM:
                        media_uri = f"cloudmusic://163/playlist/{item_id}"  # 专辑也用playlist处理
                        cover_url = item.get('picUrl', '')
                        artist_name = item.get('artist', {}).get('name', '')
                        display_name = f"{item_name} - {artist_name}"
                    else:  # SEARCH_TYPE_RADIO
                        media_uri = f"cloudmusic://163/djradio/{item_id}"
                        cover_url = item.get('picUrl', '')
                        display_name = item_name
                    
                    # 存储为字典格式（包含媒体库URI）
                    item_info = {
                        'id': item_id,
                        'name': display_name,
                        'type': search_key,
                        'media_uri': media_uri,
                        'cover': cover_url,
                    }
                    music_list.append(item_info)
                    
            _LOGGER.info(f"已格式化 {len(music_list)} 条{item_type_name}结果")

            # 6. 存储到共享数据
            search_data_key = f'{DOMAIN}_{self._entry.entry_id}_search_data'
            self.hass.data[search_data_key][DATA_SEARCH_RESULTS] = music_list
            self.hass.data[search_data_key][DATA_KEYWORD] = keyword
            self.hass.data[search_data_key][DATA_SEARCH_TYPE] = search_key
            self.hass.data[search_data_key][DATA_LAST_UPDATE] = time.time()

            _LOGGER.info(f"搜索成功，找到 {len(music_list)} 首歌曲")
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": f"找到 {len(music_list)} 首相关歌曲，请在搜索结果中选择播放",
                    "title": f'搜索"{keyword}"成功'
                }
            )

        except Exception as e:
            _LOGGER.error(f"搜索过程中出错: {e}", exc_info=True)
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": f"搜索出错: {str(e)}",
                    "title": "云音乐搜索失败"
                }
            )


class CloudMusicDailyRecommendButton(CloudMusicButton):
    """每日推荐按钮
    
    点击后直接播放每日推荐歌单。
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, ENTITY_NAME_DAILY_RECOMMEND, "每日推荐", "mdi:calendar-star")

    async def async_press(self) -> None:
        """播放每日推荐歌单"""
        await self._play_media(URI_DAILY_RECOMMEND, "每日推荐")


class CloudMusicMyFavoritesButton(CloudMusicButton):
    """我喜欢的音乐按钮
    
    点击后播放"我喜欢的音乐"歌单。
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(hass, entry, ENTITY_NAME_MY_FAVORITES, "我喜欢的音乐", "mdi:heart-multiple")

    async def async_press(self) -> None:
        """播放我喜欢的音乐歌单"""
        await self._play_media(URI_MY_FAVORITES, "我喜欢的音乐")


# 快捷按钮的通用播放方法（混入到基类中）
async def _play_media(self, media_id: str, playlist_name: str) -> None:
    """调用媒体播放器播放指定 URI
    
    Args:
        media_id: 播放协议 URI，如 cloudmusic://163/my/daily
        playlist_name: 歌单名称，用于日志和通知
    """
    # 查找第一个可用的云音乐媒体播放器
    media_player_entity_id = None
    for entity_id in self.hass.states.async_entity_ids(MEDIA_PLAYER_DOMAIN):
        state = self.hass.states.get(entity_id)
        if state and state.attributes.get('platform') == 'cloud_music':
            media_player_entity_id = entity_id
            break

    if media_player_entity_id is None:
        _LOGGER.warning("未找到云音乐媒体播放器")
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": "未找到云音乐媒体播放器，请先配置媒体播放器",
                "title": "播放失败"
            }
        )
        return

    # 调用媒体播放器的 play_media 服务
    _LOGGER.info(f"播放 {playlist_name}: {media_id}")
    try:
        await self.hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            'play_media',
            {
                'entity_id': media_player_entity_id,
                'media_content_id': media_id,
                'media_content_type': MediaType.PLAYLIST,
            },
            blocking=True
        )
        _LOGGER.info(f"{playlist_name} 播放成功")
    except Exception as e:
        _LOGGER.error(f"播放 {playlist_name} 失败: {e}", exc_info=True)
        await self.hass.services.async_call(
            "persistent_notification",
            "create",
            {
                "message": f"播放失败: {str(e)}",
                "title": f"{playlist_name}播放错误"
            }
        )


# 将通用播放方法添加到基类
CloudMusicButton._play_media = _play_media
