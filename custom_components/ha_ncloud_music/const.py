# 支持的平台列表
PLATFORMS = ["media_player", "text", "button", "select"]

# 数据存储键
DATA_SEARCH_RESULTS = "search_results"
DATA_LAST_UPDATE = "last_update"
DATA_KEYWORD = "keyword"

# 实体名称常量
ENTITY_NAME_SEARCH_INPUT = "search_input"
ENTITY_NAME_SEARCH_BUTTON = "search_trigger"
ENTITY_NAME_SEARCH_RESULTS = "search_results"
ENTITY_NAME_DAILY_RECOMMEND = "daily_recommend"
ENTITY_NAME_MY_FAVORITES = "my_favorites"

# 快捷操作协议 URI
URI_DAILY_RECOMMEND = "cloudmusic://163/my/daily"
URI_MY_FAVORITES = "cloudmusic://163/my/ilike"

# 搜索类型相关
ENTITY_NAME_SEARCH_TYPE = "search_type"
ENTITY_NAME_BROWSE_SEARCH = "browse_search"

# 搜索类型键值
SEARCH_TYPE_SONG = "song"
SEARCH_TYPE_ALBUM = "album"
SEARCH_TYPE_ARTIST = "artist"
SEARCH_TYPE_PLAYLIST = "playlist"
SEARCH_TYPE_RADIO = "radio"

# 搜索类型显示名称到API参数的映射
SEARCH_TYPE_MAP = {
    "歌曲": {"type": 1, "key": SEARCH_TYPE_SONG},
    "专辑": {"type": 10, "key": SEARCH_TYPE_ALBUM},
    "歌手": {"type": 100, "key": SEARCH_TYPE_ARTIST},
    "歌单": {"type": 1000, "key": SEARCH_TYPE_PLAYLIST},
    "电台": {"type": 1009, "key": SEARCH_TYPE_RADIO},
}

# 存储搜索类型的数据键
DATA_SEARCH_TYPE = 'search_type'

# 音质级别常量
CONF_AUDIO_QUALITY = "audio_quality"

# 音质选项 (API level 参数)
AUDIO_QUALITY_STANDARD = "standard"    # 标准 128k
AUDIO_QUALITY_HIGHER = "higher"        # 较高 192k
AUDIO_QUALITY_EXHIGH = "exhigh"        # 极高 320k
AUDIO_QUALITY_LOSSLESS = "lossless"    # 无损 FLAC
AUDIO_QUALITY_HIRES = "hires"          # Hi-Res
AUDIO_QUALITY_JYEFFECT = "jyeffect"    # 高清环绕声
AUDIO_QUALITY_SKY = "sky"              # 沉浸环绕声
AUDIO_QUALITY_DOLBY = "dolby"          # 杜比全景声
AUDIO_QUALITY_JYMASTER = "jymaster"    # 超清母带

# 默认音质
DEFAULT_AUDIO_QUALITY = AUDIO_QUALITY_EXHIGH

# 音质显示名称映射
AUDIO_QUALITY_OPTIONS = {
    # 免费 ⚪
    "标准 (128k) ⚪": AUDIO_QUALITY_STANDARD,
    "较高 (192k) ⚪": AUDIO_QUALITY_HIGHER,
    "极高 (320k) ⚪": AUDIO_QUALITY_EXHIGH,
    # 🔴
    "无损 (FLAC) 🔴": AUDIO_QUALITY_LOSSLESS,
    "Hi-Res 🔴": AUDIO_QUALITY_HIRES,
    # 👑
    "高清环绕声 👑": AUDIO_QUALITY_JYEFFECT,
    "沉浸环绕声 👑": AUDIO_QUALITY_SKY,
    "杜比全景声 👑": AUDIO_QUALITY_DOLBY,
    "超清母带  👑": AUDIO_QUALITY_JYMASTER,
}