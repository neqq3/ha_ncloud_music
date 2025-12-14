import logging, datetime

from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.event import async_track_time_interval, async_track_state_change_event
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

from .const import CONF_NEXT_TRACK_TIMING, DEFAULT_NEXT_TRACK_TIMING, FM_MODES, DEFAULT_FM_MODE

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
      entities.append(CloudMusicMediaPlayer(hass, source_media_player, entry))

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

    def __init__(self, hass, source_media_player, entry):
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

        # 读取切歌时机配置
        self._next_track_timing = entry.options.get(CONF_NEXT_TRACK_TIMING, DEFAULT_NEXT_TRACK_TIMING) if hasattr(entry, 'options') else DEFAULT_NEXT_TRACK_TIMING

        self.cloud_music = hass.data['cloud_music']
        self.before_state = None
        self.current_state = None
        self._last_position_update = None
        

        # 播放列表管理 - 方案C随机播放

        self._playlist_origin = []   # 原始顺序列表

        self._playlist_active = []   # 实际播放队列（随机或原始）

        self._play_index = 0         # 当前播放索引

        # ========== 私人 FM 状态管理 ==========
        self._fm_mode = None           # 当前 FM 模式名称（None = 普通模式）
        self._is_fm_playing = False    # 是否处于 FM 播放模式
        self._fm_preloading = False    # 是否正在预加载 FM 歌曲（防止重复请求）


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
        
        # 从底层读取duration（优先使用播放列表的 duration，回退到底层播放器）
        media_player = self.media_player
        if media_player is not None:
            # 优先从播放列表获取 duration（云音乐 API 返回的准确值）
            playlist_duration = 0
            if hasattr(self, 'playlist') and len(self.playlist) > 0:
                try:
                    music_info = self.playlist[self.playindex]
                    playlist_duration = int(music_info.duration / 1000) if music_info.duration > 1000 else int(music_info.duration)
                except (IndexError, AttributeError):
                    pass
            
            # 从底层播放器获取 duration
            attrs = media_player.attributes
            player_duration = int(attrs.get('media_duration', 0))
            
            # 优先使用播放列表的 duration（如果有效）
            if playlist_duration > 0:
                self._attr_media_duration = playlist_duration
            elif player_duration > 0:
                self._attr_media_duration = player_duration
            # 否则保持原值，避免设为 0
            
            # 判断是否下一曲（可配置切歌时机 - 自定义秒数）
            if self.before_state is not None:
                if self.before_state['media_duration'] > 0:
                    delta = self._attr_media_duration - self._attr_media_position
                    
                    # 计算触发窗口：
                    # 如果是延迟(>0)，窗口为 1秒 (在结束前1秒触发调度)
                    # 如果是提前(<0)，窗口为 提前量 + 1秒 (例如提前5秒，窗口为6秒)
                    trigger_threshold = max(1, -self._next_track_timing + 1)
                    
                    if delta <= trigger_threshold and self._attr_media_duration > 1:
                        if not getattr(self, '_next_track_scheduled', False):
                            # 计算实际需要等待的时间
                            # wait_time = 剩余时间 + 设定时间
                            # 例1 (延迟1.2s): 剩余1s + 1.2s = 等待2.2s
                            # 例2 (提前0.5s): 剩余1s + (-0.5s) = 等待0.5s
                            wait_time = delta + self._next_track_timing
                            
                            self._next_track_scheduled = True
                            
                            if wait_time <= 0:
                                # 立即切歌
                                self.hass.loop.call_soon_threadsafe(
                                    lambda: self.hass.create_task(self.async_media_next_track())
                                )
                            else:
                                # 延迟切歌
                                self.hass.loop.call_later(
                                    wait_time, 
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
        await self.async_call('volume_mute', { 'is_volume_muted': mute })

    async def async_set_volume_level(self, volume: float):
        self._attr_volume_level = volume
        await self.async_call('volume_set', { 'volume_level': volume })

    async def async_play_media(self, media_type, media_id, **kwargs):

        self._attr_state = STATE_PAUSED
        # 重置进度计时
        self._attr_media_position = 0
        self._attr_media_position_updated_at = datetime.datetime.now(datetime.timezone.utc)
        self._attr_media_position = 0
        self._attr_media_position_updated_at = datetime.datetime.now(datetime.timezone.utc)
        self._last_position_update = None
        self._next_track_scheduled = False  # 重置切歌调度标志
        
        # 判断是否为 FM 内部播放（播放 FM 播放列表中的歌曲）
        # 只有当播放非 FM 内容时才退出 FM 模式
        is_fm_internal = False
        if self._is_fm_playing and hasattr(self, 'playlist') and self.playlist:
            # 检查 media_id 是否是当前 FM 播放列表中的歌曲 URL
            for song in self.playlist:
                if hasattr(song, 'url') and song.url and media_id:
                    # 使用更宽松的匹配：检查 song.url 或 media_id 是否包含对方
                    if song.url in str(media_id) or str(media_id) in song.url:
                        is_fm_internal = True
                        break
        
        if self._is_fm_playing and not is_fm_internal:
            _LOGGER.warning(f"FM 模式检测到外部播放，退出 FM 模式。media_id={media_id[:50] if media_id else 'None'}...")
            self.exit_fm_mode()
        elif self._is_fm_playing:
            _LOGGER.debug(f"FM 内部播放: {media_id[:50] if media_id else 'None'}...")
        
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
        
        # 立即更新元数据（避免等待 interval）
        if hasattr(self, 'playlist') and len(self.playlist) > 0:
            music_info = self.playlist[self.playindex]
            self._attr_app_name = music_info.singer
            self._attr_media_image_url = music_info.thumbnail
            self._attr_media_album_name = music_info.album
            self._attr_media_title = music_info.song
            self._attr_media_artist = music_info.singer
            # 同步更新 duration（云音乐 API 返回毫秒，需转换）
            if music_info.duration > 0:
                self._attr_media_duration = int(music_info.duration / 1000) if music_info.duration > 1000 else int(music_info.duration)
            # 存储 song_id 供前端歌词卡片使用
            self._current_song_id = str(music_info.id)
        
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

        # ========== FM 模式拦截器 ==========
        if self._is_fm_playing and shuffle:
            _LOGGER.warning("用户尝试在 FM 模式下开启随机，操作已拦截")
            # 强制回滚状态
            self._attr_shuffle = False
            self.async_write_ha_state()
            # 抛出异常显示底部 toast 提示
            from homeassistant.exceptions import HomeAssistantError
            raise HomeAssistantError("私人 FM 模式基于算法推荐，无法开启随机播放")
        # =====================================

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


    # ==================== 私人 FM 核心方法 ====================

    async def async_play_fm(self, mode_name: str = None):
        """
        启动私人 FM 播放
        
        Args:
            mode_name: FM 模式名称（中文），如"默认推荐"、"AI DJ"等
        """
        mode_name = mode_name or "默认推荐"  # 使用真实的默认模式，不用占位符
        
        # 验证模式名称
        if mode_name not in FM_MODES:
            _LOGGER.warning(f"无效的 FM 模式: {mode_name}，使用默认模式")
            mode_name = "默认推荐"  # 回退到真实的默认模式
        
        mode, submode = FM_MODES[mode_name]
        
        _LOGGER.info(f"🎵 启动私人 FM: {mode_name} (mode={mode}, submode={submode})")
        
        # 1. 进入 FM 模式（自动关闭随机）
        self._is_fm_playing = True
        self._fm_mode = mode_name
        if self._attr_shuffle:
            self._attr_shuffle = False
            _LOGGER.info("进入 FM 模式，自动关闭随机播放")
        
        # 2. 获取 FM 歌曲
        tracks = await self.cloud_music.async_get_personal_fm_mode(mode, submode)
        
        if not tracks:
            _LOGGER.error("获取私人 FM 歌曲失败")
            self._is_fm_playing = False
            self._fm_mode = None
            self.cloud_music.notification("获取私人 FM 失败，请检查登录状态")
            return
        
        # 3. 设置播放列表
        self.playlist = tracks
        self._playlist_origin = list(tracks)
        self._playlist_active = list(tracks)
        self._play_index = 0
        
        # 4. 开始播放第一首
        first_song = tracks[0]
        self._attr_state = STATE_PLAYING
        self._attr_media_position = 0
        self._last_position_update = None
        self._next_track_scheduled = False
        
        # 更新元数据
        self._attr_app_name = first_song.singer
        self._attr_media_image_url = first_song.thumbnail
        self._attr_media_album_name = first_song.album
        self._attr_media_title = first_song.song
        self._attr_media_artist = first_song.singer
        self._current_song_id = str(first_song.id)
        
        if first_song.duration > 0:
            self._attr_media_duration = int(first_song.duration / 1000) if first_song.duration > 1000 else int(first_song.duration)
        
        # 播放音频
        await self.async_call('play_media', {
            'media_content_id': first_song.url,
            'media_content_type': 'music'
        })
        
        self.before_state = None
        self.async_write_ha_state()
        
        _LOGGER.info(f"私人 FM 开始播放: {first_song.song} - {first_song.singer}")

    async def _async_preload_fm_tracks(self):
        """
        预加载 FM 歌曲（当剩余歌曲不足时调用）
        
        触发条件：播放列表剩余 ≤ 2 首歌
        """
        if not self._is_fm_playing or self._fm_preloading:
            return
        
        if self._fm_mode not in FM_MODES:
            return
        
        # 检查剩余歌曲数
        remaining = len(self.playlist) - self._play_index - 1
        if remaining > 2:
            return
        
        self._fm_preloading = True
        _LOGGER.info(f"🔄 FM 预加载：剩余 {remaining} 首，开始获取更多歌曲")
        
        try:
            mode, submode = FM_MODES[self._fm_mode]
            new_tracks = await self.cloud_music.async_get_personal_fm_mode(mode, submode)
            
            if new_tracks:
                # 去重：过滤掉已存在于播放列表中的歌曲
                existing_ids = {str(song.id) for song in self.playlist if hasattr(song, 'id')}
                unique_tracks = [t for t in new_tracks if str(t.id) not in existing_ids]
                
                if unique_tracks:
                    # 追加到播放列表
                    self.playlist.extend(unique_tracks)
                    self._playlist_origin.extend(unique_tracks)
                    self._playlist_active.extend(unique_tracks)
                    _LOGGER.info(f"FM 预加载完成：追加 {len(unique_tracks)} 首新歌曲（过滤 {len(new_tracks) - len(unique_tracks)} 首重复），总计 {len(self.playlist)} 首")
                else:
                    _LOGGER.warning(f"FM 预加载：API 返回 {len(new_tracks)} 首歌曲，但都是重复的")
            else:
                _LOGGER.warning("FM 预加载失败：API 返回空列表")
        except Exception as e:
            _LOGGER.error(f"FM 预加载异常: {e}")
        finally:
            self._fm_preloading = False

    async def async_fm_trash(self):
        """
        不喜欢当前歌曲并跳到下一首
        
        调用 fm_trash API 将歌曲移入垃圾桶，然后自动切歌
        """
        if not self._is_fm_playing:
            _LOGGER.warning("当前不在 FM 模式，无法执行垃圾桶操作")
            return
        
        if not hasattr(self, '_current_song_id') or not self._current_song_id:
            _LOGGER.warning("无法获取当前歌曲 ID")
            return
        
        song_id = self._current_song_id
        song_name = self._attr_media_title or "未知歌曲"
        
        _LOGGER.info(f"🗑️ FM 垃圾桶：{song_name} ({song_id})")
        
        # 1. 调用 API
        success = await self.cloud_music.async_fm_trash(song_id)
        
        # 2. 无论成功与否，都跳到下一首（用户不想听这首歌）
        await self.async_media_next_track()
        
        if success:
            self.cloud_music.notification(f"已将「{song_name}」移入私人 FM 垃圾桶", "ncloud_fm_trash")

    def exit_fm_mode(self):
        """退出 FM 模式（切换到普通歌单时调用）"""
        if self._is_fm_playing:
            _LOGGER.info("退出私人 FM 模式")
            self._is_fm_playing = False
            self._fm_mode = None
            self._fm_preloading = False


    async def async_media_next_track(self):
        self._attr_state = STATE_PAUSED
        await self.cloud_music.async_media_next_track(self, self._attr_shuffle)
        
        # FM 模式：检查是否需要预加载
        if self._is_fm_playing:
            await self._async_preload_fm_tracks()

    async def async_media_previous_track(self):
        self._attr_state = STATE_PAUSED
        await self.cloud_music.async_media_previous_track(self, self._attr_shuffle)

    async def async_media_seek(self, position):
        # 先执行 seek 操作
        await self.async_call('media_seek', { 'seek_position': position })
        
        # seek 完成后更新位置和时间戳
        self._attr_media_position = position
        self._last_position_update = datetime.datetime.now()
        self._attr_media_position_updated_at = datetime.datetime.now(datetime.timezone.utc)
        
        # 通知 HA 更新状态（歌词卡片会根据这个时间戳计算位置）
        self.async_write_ha_state()

    async def async_clear_playlist(self):
        if hasattr(self, 'playlist'):
            del self.playlist

    async def async_media_stop(self):
        await self.async_call('media_stop')

    # 更新属性
    async def async_update(self):
        # 重新读取配置（支持动态修改选项）
        if self.entity_id:
            entry = self.hass.config_entries.async_get_entry(self.registry_entry.config_entry_id)
            if entry:
                self._next_track_timing = entry.options.get(CONF_NEXT_TRACK_TIMING, DEFAULT_NEXT_TRACK_TIMING)

    async def async_added_to_hass(self):
        """当实体添加到 HA 时调用"""
        # 监听底层播放器状态变化
        self.async_on_remove(
            async_track_state_change_event(
                self.hass, [self.source_media_player], self._on_source_player_state_change
            )
        )
        # 初始化同步一次状态
        self._update_source_player_attributes()

    def _on_source_player_state_change(self, event):
        """底层播放器状态变化回调"""
        self._update_source_player_attributes()
        # 使用线程安全的方式调用 async_write_ha_state
        self.hass.loop.call_soon_threadsafe(self.async_write_ha_state)

    def _update_source_player_attributes(self):
        """从底层播放器同步属性"""
        state = self.hass.states.get(self.source_media_player)
        if state:
            # 检查底层播放器是否离线
            if state.state == 'unavailable':
                self._attr_available = False
                return
            else:
                self._attr_available = True
            
            # 同步音量
            volume = state.attributes.get('volume_level')
            if volume is not None:
                self._attr_volume_level = volume
            
            # 同步静音状态
            muted = state.attributes.get('is_volume_muted')
            if muted is not None:
                self._attr_is_volume_muted = muted
        else:
            # 底层播放器不存在
            self._attr_available = False

    # 调用服务
    async def async_call(self, service, service_data={}):
        media_player = self.media_player
        if media_player is not None:
            service_data.update({ 'entity_id': media_player.entity_id })
            await self.hass.services.async_call('media_player', service, service_data)