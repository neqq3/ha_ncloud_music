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
SEARCH_TYPE_ARTIST = "artist"
SEARCH_TYPE_PLAYLIST = "playlist"
SEARCH_TYPE_RADIO = "radio"

# 搜索类型显示名称到API参数的映射
SEARCH_TYPE_MAP = {
    "歌曲": {"type": 1, "key": SEARCH_TYPE_SONG},
    "歌手": {"type": 100, "key": SEARCH_TYPE_ARTIST},
    "歌单": {"type": 1000, "key": SEARCH_TYPE_PLAYLIST},
    "电台": {"type": 1009, "key": SEARCH_TYPE_RADIO},
}

# 存储搜索类型的数据键
DATA_SEARCH_TYPE = 'search_type'