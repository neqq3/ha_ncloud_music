from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
import homeassistant.helpers.config_validation as cv
from homeassistant.const import CONF_URL

import asyncio
from .const import PLATFORMS
from .manifest import manifest
from .http import HttpView, CloudMusicApiView
from .cloud_music import CloudMusic

DOMAIN = manifest.domain

CONFIG_SCHEMA = cv.deprecated(DOMAIN)

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:

    data = entry.data
    api_url = data.get(CONF_URL)
    vip_url = entry.options.get(CONF_URL, '')
    
    # 读取音质配置
    from .const import CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY
    audio_quality = entry.options.get(CONF_AUDIO_QUALITY, DEFAULT_AUDIO_QUALITY)
    
    hass.data['cloud_music'] = CloudMusic(hass, api_url, vip_url, audio_quality)
    
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
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(update_listener))
    return True

async def update_listener(hass, entry):
    await async_unload_entry(hass, entry)
    await asyncio.sleep(1)
    await async_setup_entry(hass, entry)

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)