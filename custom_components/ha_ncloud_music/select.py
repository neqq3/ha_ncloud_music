"""Select platform for ha_ncloud_music integration.
提供搜索结果选择实体，用户选择后直接播放。
"""
import logging
from typing import List
from homeassistant.components.select import SelectEntity
from homeassistant.components.media_player import DOMAIN as MEDIA_PLAYER_DOMAIN, MediaType
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_time_interval
from datetime import timedelta

from .const import (
    ENTITY_NAME_SEARCH_RESULTS,
    ENTITY_NAME_SEARCH_TYPE,
    DATA_SEARCH_RESULTS,
    DATA_LAST_UPDATE,
    DATA_KEYWORD,
    DATA_SEARCH_TYPE,
    SEARCH_TYPE_MAP,
)
from .manifest import manifest

_LOGGER = logging.getLogger(__name__)

DOMAIN = manifest.domain


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置 select 实体平台"""
    async_add_entities([
        CloudMusicSearchResults(hass, entry),
        CloudMusicSearchType(hass, entry),  # 新增：搜索类型选择器
    ])


class CloudMusicSearchResults(SelectEntity):
    """云音乐搜索结果选择实体
    
    动态监听共享数据的变化，更新选项列表。
    用户选择歌曲后，自动调用媒体播放器播放。
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """初始化搜索结果选择实体"""
        self.hass = hass
        self._entry = entry
        self._attr_name = f"{manifest.name} 搜索结果"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{ENTITY_NAME_SEARCH_RESULTS}"
        self._attr_icon = "mdi:playlist-music"
        
        # 初始状态
        self._attr_options = ["暂无搜索结果"]
        self._attr_current_option = "暂无搜索结果"
        
        # 缓存：存储选项到 MusicInfo 的映射
        self._music_map = {}
        self._last_update_time = 0
        
        # 共享数据键
        self._search_data_key = f'{DOMAIN}_{entry.entry_id}_search_data'

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

    async def async_added_to_hass(self) -> None:
        """实体添加到 Home Assistant 时设置轮询更新"""
        await super().async_added_to_hass()
        
        # 每秒检查一次共享数据是否更新
        async_track_time_interval(
            self.hass,
            self._async_check_update,
            timedelta(seconds=1)
        )
        
        # 立即检查一次
        await self._async_check_update(None)

    @callback
    async def _async_check_update(self, now) -> None:
        """检查共享数据是否有更新"""
        search_data = self.hass.data.get(self._search_data_key)
        if search_data is None:
            return

        # 检查时间戳是否更新
        last_update = search_data.get(DATA_LAST_UPDATE, 0)
        if last_update <= self._last_update_time:
            return

        # 数据已更新，刷新选项列表
        self._last_update_time = last_update
        await self._async_refresh_options()

    async def _async_refresh_options(self) -> None:
        """从共享数据刷新选项列表"""
        search_data = self.hass.data.get(self._search_data_key)
        if search_data is None:
            return

        music_list = search_data.get(DATA_SEARCH_RESULTS, [])
        keyword = search_data.get(DATA_KEYWORD, '')

        if not music_list:
            # 空结果
            self._attr_options = [f'未找到"{keyword}"的搜索结果']
            self._attr_current_option = self._attr_options[0]
            self._music_map = {}
            _LOGGER.debug("搜索结果为空，清空选项列表")
        else:
            # 格式化选项：歌手 - 歌名
            new_options = []
            new_music_map = {}
            
            for music_info in music_list:
                # 格式：歌手 - 歌名
                option_text = f"{music_info.singer} - {music_info.song}"
                new_options.append(option_text)
                new_music_map[option_text] = music_info

            self._attr_options = new_options
            self._attr_current_option = new_options[0] if new_options else "暂无搜索结果"
            self._music_map = new_music_map
            
            _LOGGER.info(f"已更新搜索结果选项，共 {len(new_options)} 首歌曲")

        # 通知 Home Assistant 状态已更新
        self.async_write_ha_state()

    async def async_select_option(self, option: str) -> None:
        """用户选择歌曲时触发播放
        
        这是核心交互逻辑：选择即播放，无需二次确认。
        """
        # 保存选中的选项
        selected_option = option
        self._attr_current_option = option
        self.async_write_ha_state()

        # 检查是否是占位符选项
        if option.startswith("未找到") or option == "暂无搜索结果":
            _LOGGER.debug(f"选择了占位符选项: {option}")
            return

        # 从映射中获取对应的 MusicInfo
        music_info = self._music_map.get(option)
        if music_info is None:
            _LOGGER.warning(f"未找到选项对应的歌曲信息: {option}")
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": f"歌曲信息丢失: {option}",
                    "title": "播放失败"
                }
            )
            return

        # 查找云音乐媒体播放器
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

        # 获取 CloudMusic 实例和 media_player 实体
        cloud_music = self.hass.data.get('cloud_music')
        if cloud_music is None:
            _LOGGER.error("CloudMusic 实例未找到")
            return

        # 查找 media_player 实体对象
        # 使用 entity registry 来获取实体对象
        media_player_obj = None
        entity_registry = self.hass.data.get("entity_components", {}).get(MEDIA_PLAYER_DOMAIN)
        
        if entity_registry:
            # EntityComponent 有 entities 属性
            for entity in entity_registry.entities:
                if entity.entity_id == media_player_entity_id:
                    media_player_obj = entity
                    break

        # 使用原作者的 playlist 机制播放
        # 这样可以保留封面图、歌词等信息，并加入播放列表
        _LOGGER.info(f"准备播放选中歌曲: {music_info.song} - {music_info.singer}")
        try:
            # 设置 media_player 的 playlist 和 playindex
            if media_player_obj:
                media_player_obj.playlist = [music_info]
                media_player_obj.playindex = 0
                _LOGGER.info(f"已设置 playlist: {music_info.song}, 封面: {music_info.picUrl}")
            else:
                _LOGGER.warning("未找到 media_player 对象，直接使用 URL 播放")

            # 使用 media_player 的 async_play_media 方法
            # 传入 URL 让它使用我们刚设置的 playlist
            await self.hass.services.async_call(
                MEDIA_PLAYER_DOMAIN,
                'play_media',
                {
                    'entity_id': media_player_entity_id,
                    'media_content_id': music_info.url,
                    'media_content_type': MediaType.MUSIC,
                },
                blocking=True
            )
            _LOGGER.info(f"开始播放: {music_info.singer} - {music_info.song}")
            
        except Exception as e:
            _LOGGER.error(f"播放歌曲失败: {e}", exc_info=True)
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "message": f"播放失败: {str(e)}",
                    "title": "云音乐播放错误"
                }
            )


class CloudMusicSearchType(SelectEntity):
    """云音乐搜索类型选择器
    
    允许用户选择搜索类型：歌曲、歌手、歌单、电台。
    """

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """初始化搜索类型选择器"""
        self.hass = hass
        self._entry = entry
        self._attr_name = f"{manifest.name} 搜索类型"
        self._attr_unique_id = f"{DOMAIN}_{entry.entry_id}_{ENTITY_NAME_SEARCH_TYPE}"
        self.entity_id = f"select.{DOMAIN}_{ENTITY_NAME_SEARCH_TYPE}"
        self._attr_icon = "mdi:format-list-bulleted-type"
        
        # 选项列表：歌曲、歌手、歌单、电台
        self._attr_options = list(SEARCH_TYPE_MAP.keys())
        self._attr_current_option = "歌曲"  # 默认搜索歌曲

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

    async def async_select_option(self, option: str) -> None:
        """用户选择搜索类型"""
        self._attr_current_option = option
        self.async_write_ha_state()
        _LOGGER.info(f"搜索类型已变更为: {option}")
