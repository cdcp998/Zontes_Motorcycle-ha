import logging
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
import aiohttp

from .const import (
    DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL, ERROR_INVALID_AUTH, ERROR_NOT_REGISTERED,
    ERROR_WRONG_PASSWORD
)
from .api import ZontesApiClient, ZontesNotRegisteredError, ZontesWrongPasswordError, ZontesAuthError

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
})


class ZontesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            try:
                valid = await self._validate_login(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
                if valid:
                    return self.async_create_entry(
                        title=user_input[CONF_USERNAME],
                        data=user_input
                    )
            except ZontesNotRegisteredError:
                errors["base"] = ERROR_NOT_REGISTERED
            except ZontesWrongPasswordError:
                errors["base"] = ERROR_WRONG_PASSWORD
            except ZontesAuthError:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as e:
                _LOGGER.exception("Unexpected error during login validation: %s", e)
                errors["base"] = ERROR_INVALID_AUTH

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors
        )

    async def _validate_login(self, username: str, password: str) -> bool:
        session = aiohttp.ClientSession()
        client = ZontesApiClient(username, password, session)
        try:
            return await client.login()
        finally:
            await session.close()

    @staticmethod
    def async_get_options_flow(config_entry):
        """返回选项流处理器"""
        return ZontesOptionsFlowHandler(config_entry)


class ZontesOptionsFlowHandler(config_entries.OptionsFlow):
    """处理选项配置"""

    def __init__(self, config_entry):
        """初始化选项流，存储配置条目到私有变量。"""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema({
            vol.Optional(
                CONF_SCAN_INTERVAL,
                default = self._config_entry.options.get(
                    CONF_SCAN_INTERVAL,
                    self._config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ),
                description = {"translation_key": "scan_interval"},
            ): int,
        })

        return self.async_show_form(step_id="init", data_schema=options_schema)