import logging
from datetime import timedelta
from typing import Dict, Any
import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.exceptions import ConfigEntryNotReady
from .const import (
    DOMAIN, CONF_USERNAME, CONF_PASSWORD, CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL
)
from .api import ZontesApiClient, ZontesAuthError

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "device_tracker"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    # 优先使用选项中的 scan_interval，否则使用配置数据中的，最后回退到默认值
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))

    session = aiohttp.ClientSession()
    client = ZontesApiClient(username, password, session)

    try:
        await client.login()
        await client.get_my_motors()
        if not client.motors:
            raise ConfigEntryNotReady("No motorcycles found for this account")
    except ZontesAuthError as e:
        raise ConfigEntryNotReady(f"Authentication failed: {e}") from e
    except Exception as err:
        raise ConfigEntryNotReady(f"Error during initialization: {err}") from err

    async def async_update_data() -> Dict[str, Any]:
        try:
            # 每次更新前重新过滤有效车辆（授权可能过期）
            client.filter_valid_motors()
            if not client.motors:
                _LOGGER.warning("No valid motors after filtering")
                return {"by_motor": {}, "motors": []}

            data_by_motor = await client.get_all_motors_data()
            return {
                "by_motor": data_by_motor,
                "motors": client.motors
            }
        except Exception as err:
            raise UpdateFailed(f"Error communicating with API: {err}")

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"Zontes {username}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "coordinator": coordinator,
        "client": client,
    }

    # 添加选项变更监听器
    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """当选项变更时调用，更新 coordinator 的间隔。"""
    _LOGGER.debug("Options for %s changed: %s", entry.entry_id, entry.options)
    scan_interval = entry.options.get(CONF_SCAN_INTERVAL, entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.update_interval = timedelta(seconds=scan_interval)
    _LOGGER.info("Update interval changed to %s seconds", scan_interval)