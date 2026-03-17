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
    scan_interval = entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)

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
                "motors": client.motors  # 传递过滤后的列表供实体使用
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

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
    return unload_ok