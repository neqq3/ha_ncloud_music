from __future__ import annotations

from typing import Any
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow, ConfigEntry
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import CONF_URL, CONF_USERNAME, CONF_PASSWORD
from homeassistant.helpers.storage import STORAGE_DIR
from urllib.parse import quote
from homeassistant.core import callback
from homeassistant.helpers.selector import selector

from .manifest import manifest
from .http_api import fetch_data

DOMAIN = manifest.domain

class SimpleConfigFlow(ConfigFlow, domain=DOMAIN):

    VERSION = 3

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial step."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")
        
        errors = {}
        if user_input is not None:
            url = user_input.get(CONF_URL).strip('/')
            # 检查接口是否可用
            try:
                res = await fetch_data(f'{url}/login/status')
                if res['data']['code'] == 200:
                    user_input[CONF_URL] = url
                    return self.async_create_entry(title=DOMAIN, data=user_input)
            except Exception as ex:
                errors = {'base': 'api_failed'}
        
        # 防止第一次 user_input 为 None 时报错
        default_url = user_input.get(CONF_URL) if user_input else ""

        DATA_SCHEMA = vol.Schema({
            vol.Required(CONF_URL, default=default_url): str
        })
        
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry):
        return OptionsFlowHandler(entry)


class OptionsFlowHandler(OptionsFlow):
    def __init__(self, config_entry: ConfigEntry):
        """Initialize options flow."""
        # 这里不能用 self.config_entry，因为会跟父类属性冲突
        # 改名为 self._config_entry (加了下划线)
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input=None):
        # 这里读取也要改成 self._config_entry
        options = self._config_entry.options
        errors = {}
        
        if user_input is not None:
            return self.async_create_entry(title='', data=user_input)
        
        media_states = self.hass.states.async_all('media_player')
        media_entities = []

        for state in media_states:
            friendly_name = state.attributes.get('friendly_name', state.entity_id)
            platform = state.attributes.get('platform')
            entity_id = state.entity_id
            value = f'{friendly_name}（{entity_id}）'

            if platform != 'cloud_music' and state.state != 'unavailable':
                media_entities.append({'label': value, 'value': entity_id})

        # 防止 options 中没有 media_player 键时报错
        current_media_players = options.get('media_player', [])

        DATA_SCHEMA = vol.Schema({
            vol.Required('media_player', default=current_media_players): selector({
                "select": {
                    "options": media_entities,
                    "multiple": True
                }
            })
        })
        
        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)