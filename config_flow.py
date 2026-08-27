"""Config flow for the Zontes Smart Motorcycle integration."""

import logging

import aiohttp
import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .api import (
    DEFAULT_TIMEOUT,
    ZontesApiClient,
    ZontesAuthError,
    ZontesNotRegisteredError,
    ZontesWrongPasswordError,
)
from .const import (
    CONF_COORD_TYPE,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    COORD_TYPES,
    DEFAULT_COORD_TYPE,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    ERROR_INVALID_AUTH,
    ERROR_NOT_REGISTERED,
    ERROR_WRONG_PASSWORD,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_DATA_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
        vol.Optional(CONF_COORD_TYPE, default=DEFAULT_COORD_TYPE): vol.In(COORD_TYPES),
    }
)


class ZontesConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """处理初始化配置流程。"""

    VERSION = 1

    async def async_step_user(self, user_input=None) -> FlowResult:
        errors = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME])
            self._abort_if_unique_id_configured()

            try:
                valid = await self._validate_login(
                    user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
                )
                if valid:
                    return self.async_create_entry(
                        title=user_input[CONF_USERNAME],
                        data=user_input,
                    )
            except ZontesNotRegisteredError:
                errors["base"] = ERROR_NOT_REGISTERED
            except ZontesWrongPasswordError:
                errors["base"] = ERROR_WRONG_PASSWORD
            except ZontesAuthError:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during login validation: %s", err)
                errors["base"] = ERROR_INVALID_AUTH

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_DATA_SCHEMA,
            errors=errors,
        )

    async def _validate_login(self, username: str, password: str) -> bool:
        session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
        client = ZontesApiClient(username, password, session)
        try:
            return await client.login()
        finally:
            await session.close()

    @staticmethod
    def async_get_options_flow(config_entry):
        return ZontesOptionsFlowHandler(config_entry)


class ZontesOptionsFlowHandler(config_entries.OptionsFlow):
    """处理选项流程 (仅轮询间隔)。"""

    def __init__(self, config_entry):
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        options_schema = vol.Schema(
            {
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=self._config_entry.options.get(
                        CONF_SCAN_INTERVAL,
                        self._config_entry.data.get(
                            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL
                        ),
                    ),
                    description={"translation_key": "scan_interval"},
                ): int,
                vol.Optional(
                    CONF_COORD_TYPE,
                    default=self._config_entry.options.get(
                        CONF_COORD_TYPE,
                        self._config_entry.data.get(
                            CONF_COORD_TYPE, DEFAULT_COORD_TYPE
                        ),
                    ),
                    description={"translation_key": "coord_type"},
                ): vol.In(COORD_TYPES),
            }
        )

        return self.async_show_form(step_id="init", data_schema=options_schema)
