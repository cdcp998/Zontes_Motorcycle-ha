"""Zontes Smart Motorcycle integration (全新实现)。

从 5 个官方接口抓取数据, 全部以实体形式呈现 (传感器 + 锁 + 车辆位置),
不做任何设备卡片 / 板块 / 分类的设置, 界面即实体列表。
"""
import logging
from datetime import timedelta
from typing import Any, Dict

import aiohttp

from homeassistant.config_entries import ConfigEntry, ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api import DEFAULT_TIMEOUT, ZontesApiClient, ZontesAuthError
from .common import get_pke
from .sensor import sensor_unique_ids
from .const import (
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_USERNAME,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS = ["sensor", "binary_sensor", "device_tracker"]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置集成: 登录、抓取静态档案、启动数据协调器。"""
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )

    session = aiohttp.ClientSession(timeout=DEFAULT_TIMEOUT)
    client = ZontesApiClient(username, password, session)

    try:
        await client.login()
        await client.get_my_motors()
        await client.get_user_center_data()
        if not client.motors:
            raise ConfigEntryNotReady("No motorcycles found for this account")
        # 静态售后信息 (每台车抓取一次)
        for motor in client.motors:
            pke = get_pke(motor)
            if pke:
                await client.get_service_info(pke)
    except ZontesAuthError as err:
        # 认证失败是永久性错误, 提示用户重新授权而非无限重试
        raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
    except Exception as err:
        raise ConfigEntryNotReady(f"Error during initialization: {err}") from err

    # 最近一次成功数据 (供网络异常时优雅降级, 避免协调器崩溃)
    last_data: Dict[str, Any] = {"by_motor": {}, "motors": client.motors}

    async def async_update_data() -> Dict[str, Any]:
        """轮询更新; 任何异常都绝不外抛, 保证协调器稳定运行。"""
        nonlocal last_data
        try:
            client.filter_valid_motors()
            if not client.motors:
                await client.get_my_motors()
            if not client.motors:
                _LOGGER.warning("No valid motors found")
                return {"by_motor": {}, "motors": []}
            data_by_motor = await client.get_all_motors_data()
            last_data = {"by_motor": data_by_motor, "motors": client.motors}
            return last_data
        except Exception as err:  # noqa: BLE001
            # 安全网: 返回最近一次成功数据, 实体保持可用
            _LOGGER.exception("Unexpected error during data update: %s", err)
            return last_data

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

    entry.async_on_unload(entry.add_update_listener(update_listener))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 清理历史版本遗留的实体/空设备 (如带【诊断】分类的旧实体), 保证设备页规整
    known_pkes = [pke for motor in client.motors if (pke := get_pke(motor))]
    hass.async_create_task(_cleanup_stale_registry(hass, entry, known_pkes))
    return True


async def _cleanup_stale_registry(
    hass: HomeAssistant, entry: ConfigEntry, known_pkes: list[str]
) -> None:
    """清理历史版本遗留的实体与空设备 (避免设备页出现多余的分类卡片/子设备)。"""
    ent_reg = er.async_get(hass)

    expected = set()
    for pke in known_pkes:
        expected |= sensor_unique_ids(pke)
        expected.add(f"{pke}_lock")
        expected.add(f"{pke}_location")

    removed_any = False
    for entity in list(ent_reg.entities.values()):
        if entity.config_entry_id != entry.entry_id:
            continue
        if entity.domain not in ("sensor", "binary_sensor", "device_tracker"):
            continue
        if entity.unique_id not in expected:
            _LOGGER.info("Removing stale entity %s (unique_id=%s)", entity.entity_id, entity.unique_id)
            ent_reg.async_remove_entity(entity.entity_id)
            removed_any = True

    if removed_any:
        # 顺带删除已无实体的历史遗留空设备
        dev_reg = dr.async_get(hass)
        known_ids = {(DOMAIN, pke) for pke in known_pkes}
        for device in list(dev_reg.devices.values()):
            if entry.entry_id not in device.config_entries:
                continue
            if any(identifier in known_ids for identifier in device.identifiers):
                continue
            if not any(e.device_id == device.id for e in ent_reg.entities.values()):
                _LOGGER.info("Removing stale empty device %s", device.id)
                dev_reg.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载平台并释放 HTTP 会话。"""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        entry_data = hass.data[DOMAIN].pop(entry.entry_id, None)
        client = entry_data.get("client") if entry_data else None
        if client is not None:
            await client.close()
    return unload_ok


async def update_listener(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """选项变更时应用新的轮询间隔并立即刷新。"""
    _LOGGER.debug("Options for %s changed: %s", entry.entry_id, entry.options)
    scan_interval = entry.options.get(
        CONF_SCAN_INTERVAL,
        entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
    )
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    coordinator.update_interval = timedelta(seconds=scan_interval)
    await coordinator.async_refresh()
    _LOGGER.info("Update interval changed to %s seconds", scan_interval)
