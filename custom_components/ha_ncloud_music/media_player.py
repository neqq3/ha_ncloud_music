import logging, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.components.media_player import MediaPlayerEntity
from homeassistant.components.media_player.const import MediaPlayerEntityFeature
from homeassistant.const import (
    CONF_URL,
    CONF_NAME,
    STATE_OFF, 
    STATE_ON, 
    STATE_PLAYING,
    STATE_PAUSED,
    STATE_IDLE,
)

from .manifest import manifest

DOMAIN = manifest.domain

_LOGGER = logging.getLogger(__name__)

SUPPORT_FEATURES = MediaPlayerEntityFeature.VOLUME_STEP | MediaPlayerEntityFeature.VOLUME_MUTE | MediaPlayerEntityFeature.VOLUME_SET | \
    MediaPlayerEntityFeature.PLAY_MEDIA | MediaPlayerEntityFeature.PLAY | MediaPlayerEntityFeature.PAUSE | MediaPlayerEntityFeature.PREVIOUS_TRACK | MediaPlayerEntityFeature.NEXT_TRACK | \
    MediaPlayerEntityFeature.BROWSE_MEDIA | MediaPlayerEntityFeature.SEEK | MediaPlayerEntityFeature.CLEAR_PLAYLIST | MediaPlayerEntityFeature.SHUFFLE_SET | MediaPlayerEntityFeature.REPEAT_SET

# 定时器时间
TIME_BETWEEN_UPDATES = datetime.timedelta(seconds=1)
UNSUB_INTERVAL = None

async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:

    entities = []
    for source_media_player in entry.options.get('media_player', []):
      entities.append(CloudMusicMediaPlayer(hass, source_media_player))

    def media_player_interval(now):
      for mp in entities:
        mp.interval(now)

    # 开启定时器
    global UNSUB_INTERVAL
    if UNSUB_INTERVAL is not None:
      UNSUB_INTERVAL()
    UNSUB_INTERVAL = async_track_time_interval(hass, media_player_interval, TIME_BETWEEN_UPDATES)

    async_add_entities(entities, True)

class CloudMusicMediaPlayer(MediaPlayerEntity):

    def __init__(self, hass, source_media_player):
        self.hass = hass
        self._attributes = {
            'platform': 'cloud_music'
        }
        # fixed attribute
        self._attr_media_image_remotely_accessible = True
        self._attr_device_class = 'tv'
        self._attr_supported_features = SUPPORT_FEATURES

        # default attribute
        self.source_media_player = source_media_player
        self._attr_name = f'{manifest.name} {source_media_player.split(".")[1]}'
        self._attr_unique_id = f'{manifest.domain}{source_media_player}'
        self._attr_state =  STATE_ON
        self._attr_volume_level = 1
        self._attr_repeat = 'all'
        self._attr_shuffle = False

        self.cloud_music = hass.data['cloud_music']
        self.before_state = None
        self.current_state = None
        self._last_position_update = None
        

        # 播放列表管理 - 方案C随机播放

        self._playlist_origin = []   # 原始顺序列表

        self._playlist_active = []   # 实际播放队列（随机或原始）

        self._play_index = 0         # 当前播放索引


    def interval(self, now):
        """定时器回调 - 参考lsCoding666实现"""
        # 暂停时不更新
        if self._attr_state != STATE_PLAYING:
            return
        
        # 获取当前时间
        new_updated_at = datetime.datetime.now()
        
        # 自主计时（每秒+1）
        if not hasattr(self, '_last_position_update') or self._last_position_update is None:
            self._last_position_update = new_updated_at
            self._attr_media_position = 0
        else:
            self._attr_media_position += 1
            self._last_position_update = new_updated_at
            self._attr_media_position_updated_at = datetime.datetime.now(datetime.timezone.utc)
        
        # 从底层读取duration
        media_player = self.media_player
        if media_player is not None:
            attrs = media_player.attributes
            self._attr_media_duration = int(attrs.get('media_duration', 0))
            
            # 判断是否下一曲（lsCoding666的逻辑）
            if self.before_state is not None:
                if self.before_state['media_duration'] > 0:
                    delta = self._attr_media_duration - self._attr_media_position
                    if delta <= 1 and self._attr_media_duration > 1:
                        self._attr_state = STATE_PAUSED
                        self.before_state = None
                        self.hass.loop.call_soon_threadsafe(
                            lambda: self.hass.create_task(self.async_media_next_track())
                        )
                        return
                
                # 补充：如果底层变off（MPD播完后）
                if media_player.state == STATE_OFF and self._attr_state == STATE_PLAYING:
                    self._attr_state = STATE_PAUSED
                    self.before_state = None
                    self.hass.loop.call_soon_threadsafe(
                        lambda: self.hass.create_task(self.async_media_next_track())
                    )
                    return
            
            # 更新状态记录
            self.before_state = {
                'media_position': int(self._attr_media_position),
                'media_duration': int(self._attr_media_duration),
                'state': media_player.state
            }
            self.current_state = media_player.state
        
        # 更新元数据
        if hasattr(self, 'playlist'):
            music_info = self.playlist[self.playindex]
            self._attr_app_name = music_info.singer
            self._attr_media_image_url = music_info.thumbnail
            self._attr_media_album_name = music_info.album
            self._attr_media_title = music_info.song
            self._attr_media_artist = music_info.singer
        
        # 静默定时器：不再每秒通知 HA 更新界面
        # 状态更新由切歌、播放、暂停等操作自动触发
        # 这样可以避免数据库每秒写入，大幅降低系统负载

    @property
    def media_player(self):
        if self.entity_id is not None and self.source_media_player is not None:
            return self.hass.states.get(self.source_media_player)

    @property
    def playindex(self):
        """当前播放索引（只读，自动计算）"""
        if self._attr_shuffle and hasattr(self, '_playlist_active') and hasattr(self, '_play_index'):
            try:
                if 0 <= self._play_index < len(self._playlist_active):
                    current_song = self._playlist_active[self._play_index]
                    return self.playlist.index(current_song)
            except (ValueError, AttributeError, IndexError):
                pass
        return getattr(self, '_play_index', 0)
    
    @playindex.setter
    def playindex(self, value):
        """保留setter用于向后兼容"""
        pass

    @property
    def device_info(self):
        return {
            'identifiers': {
                (DOMAIN, manifest.documentation)
            },
            'name': self.name,
            'manufacturer': 'shaonianzhentan',
            'model': 'CloudMusic',
            'sw_version': manifest.version
        }

    @property
    def extra_state_attributes(self):
        """返回额外状态属性 - 精简版
        
        移除了 next_tracks 以降低数据库负载。
        歌词功能通过 API 接口 /lyric?id=xxx 提供。
        """
        attributes = dict(self._attributes)
        
        # 保留 song_id 供 Layer 2 API 使用
        if hasattr(self, '_current_song_id') and self._current_song_id:
            attributes['song_id'] = self._current_song_id
        
        return attributes

    async def async_browse_media(self, media_content_type=None, media_content_id=None):
        return await self.cloud_music.async_browse_media(self, media_content_type, media_content_id)

    async def async_volume_up(self):
        await self.async_call('volume_up')

    async def async_volume_down(self):
        await self.async_call('volume_down')

    async def async_mute_volume(self, mute):
        self._attr_is_volume_muted = mute
        await self.async_call('mute_volume', { 'is_volume_muted': mute })

    async def async_set_volume_level(self, volume: float):
        self._attr_volume_level = volume
        await self.async_call('volume_set', { 'volume_level': volume })

    async def async_play_media(self, media_type, media_id, **kwargs):

        self._attr_state = STATE_PAUSED
        # 重置进度计时
        self._attr_media_position = 0
        self._attr_media_position_updated_at = datetime.datetime.now(datetime.timezone.utc)
        self._last_position_update = None
        
        media_content_id = media_id
        result = await self.cloud_music.async_play_media(self, self.cloud_music, media_id)
        if result is not None:
            if result == 'index':
                # 播放当前列表指定项
                # 根据shuffle状态选择正确的歌曲
                if self._attr_shuffle and hasattr(self, '_playlist_active') and len(self._playlist_active) > 0:
                    media_content_id = self._playlist_active[self._play_index].url
                else:
                    media_content_id = self.playlist[self.playindex].url
            elif result.startswith('http'):
                # HTTP播放链接
                media_content_id = result
            else:
                # 添加播放列表到播放器（新歌单的第一首）
                # 根据shuffle状态选择正确的第一首歌
                if self._attr_shuffle and hasattr(self, '_playlist_active') and len(self._playlist_active) > 0:
                    media_content_id = self._playlist_active[self._play_index].url
                else:
                    media_content_id = self.playlist[self.playindex].url

        self._attr_media_content_id = media_content_id
        await self.async_call('play_media', {
            'media_content_id': media_content_id,
            'media_content_type': 'music'
        })
        self._attr_state = STATE_PLAYING
        
        # 通知 HA 更新状态（播放新歌时立即刷新）
        self.async_write_ha_state()
        
        self.before_state = None

    async def async_media_play(self):
        self._attr_state = STATE_PLAYING
        await self.async_call('media_play')
        self.async_write_ha_state()  # 通知 HA 更新状态

    async def async_media_pause(self):
        self._attr_state = STATE_PAUSED
        await self.async_call('media_pause')
        self.async_write_ha_state()  # 通知 HA 更新状态

    async def async_set_repeat(self, repeat):
        self._attr_repeat = repeat


    def _smart_shuffle(self):

        """智能打乱播放列表 - Spotify风格，避免边界重复

        

        策略：

        - 小歌单(≤3首)：完全随机，不做限制

        - 中等歌单(4-20首)：避免上一轮最后N首出现在本轮前N首（N=min(歌单*40%, 5)）

        - 大歌单(>20首)：避免最后5首出现在前5首

        """

        import random

        

        playlist_len = len(self._playlist_origin)

        

        # 小歌单特殊处理：完全随机

        if playlist_len <= 3:

            self._playlist_active = list(self._playlist_origin)

            random.shuffle(self._playlist_active)

            _LOGGER.debug(f"小歌单({playlist_len}首)完全随机打乱")

            return

        

        # 记住上一轮的最后几首（最多5首或40%）

        avoid_count = min(5, max(1, int(playlist_len * 0.4)))

        

        # 获取上一轮的最后几首歌（如果存在）

        last_songs = []

        if hasattr(self, '_playlist_active') and len(self._playlist_active) > 0:

            last_songs = self._playlist_active[-avoid_count:]

        

        # 打乱整个列表

        self._playlist_active = list(self._playlist_origin)

        random.shuffle(self._playlist_active)

        

        # 如果有上一轮记录，尝试避免边界重复

        if last_songs:

            max_retries = 10

            retry_count = 0

            

            while retry_count < max_retries:

                first_songs = self._playlist_active[:avoid_count]

                # 检查前N首是否和上一轮最后N首有重复

                has_overlap = any(song in last_songs for song in first_songs)

                

                if not has_overlap:

                    _LOGGER.debug(f"智能打乱成功：避免了上一轮最后{avoid_count}首出现在本轮前{avoid_count}首")

                    break

                

                # 有重复，重新打乱

                random.shuffle(self._playlist_active)

                retry_count += 1

            

            if retry_count >= max_retries:

                _LOGGER.debug(f"智能打乱({max_retries}次尝试后)：接受当前随机结果")

        else:

            _LOGGER.debug(f"首次打乱播放列表({playlist_len}首)")


    async def async_set_shuffle(self, shuffle):

        """设置随机播放 - 方案C实现"""

        import random

        

        self._attr_shuffle = shuffle

        

        # 如果没有播放列表，只设置标志

        if not hasattr(self, 'playlist') or len(self.playlist) == 0:

            return

        

        # 获取当前播放的歌曲（使用 _playlist_active 和 _play_index）
        try:
            current_song = self._playlist_active[self._play_index]
        except (AttributeError, IndexError):
            # 如果未初始化，使用playlist[0]
            current_song = self.playlist[0] if hasattr(self, 'playlist') and len(self.playlist) > 0 else None
            if current_song is None:
                return

        

        if shuffle:

            # 开启随机：整个列表打乱



            # 使用智能打乱方法（避免边界重复）
            self._smart_shuffle()



            # 将当前歌移到第一个位置，确保不跳过任何歌曲
            try:
                current_index = self._playlist_active.index(current_song)
                # 将当前歌和第一首歌交换位置
                if current_index != 0:
                    self._playlist_active[0], self._playlist_active[current_index] = \
                        self._playlist_active[current_index], self._playlist_active[0]
                self._play_index = 0
                _LOGGER.debug(f"开启随机播放，打乱 {len(self._playlist_active)} 首歌，当前歌已移到索引 0")
            except ValueError:
                # 当前歌不在列表中，从头开始
                self._play_index = 0
                _LOGGER.warning(f"开启随机播放时未找到当前歌，从索引 0 开始")



        else:

            # 关闭随机：恢复原始顺序

            self._playlist_active = list(self.playlist)

            # 找到当前歌在原始列表中的位置（安全处理）

            try:

                self._play_index = self._playlist_active.index(current_song)

            except ValueError:

                # 极端情况兜底

                self._play_index = self.playindex

            _LOGGER.debug(f"关闭随机播放，恢复原始顺序")



    async def async_media_next_track(self):
        self._attr_state = STATE_PAUSED
        await self.cloud_music.async_media_next_track(self, self._attr_shuffle)

    async def async_media_previous_track(self):
        self._attr_state = STATE_PAUSED
        await self.cloud_music.async_media_previous_track(self, self._attr_shuffle)

    async def async_media_seek(self, position):
        # 重置自主计时
        self._attr_media_position = position
        self._last_position_update = datetime.datetime.now()
        self._attr_media_position_updated_at = datetime.datetime.now(datetime.timezone.utc)
        await self.async_call('media_seek', { 'seek_position': position })

    async def async_clear_playlist(self):
        if hasattr(self, 'playlist'):
            del self.playlist

    async def async_media_stop(self):
        await self.async_call('media_stop')

    # 更新属性
    async def async_update(self):
        pass

    # 调用服务
    async def async_call(self, service, service_data={}):
        media_player = self.media_player
        if media_player is not None:
            service_data.update({ 'entity_id': media_player.entity_id })
            await self.hass.services.async_call('media_player', service, service_data)