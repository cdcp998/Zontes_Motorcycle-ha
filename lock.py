"""远程锁控制实体 (lock 平台)。

与 binary_sensor.lock 共用同一状态源 (getHomeData.myCarData.lock):
接口值 0 = 已锁定/设防, 1 = 解锁 (已通过真实控车验证: ULoc -> 0, UClear -> 1)。
通过 4510 私有控制协议下发开锁/上锁指令, 指令成功后立即刷新协调器状态。
"""
import logging
from typing import Optional

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .common import device_info, get_motor_field, get_pke, motor_is_known
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """为每辆摩托车创建一个远程锁控制实体。"""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]
    client = entry_data["client"]

    entities = []
    for motor in client.motors:
        pke = get_pke(motor)
        if pke:
            entities.append(ZontesLockEntity(coordinator, client, motor, pke))
    async_add_entities(entities)


class ZontesLockEntity(CoordinatorEntity, LockEntity):
    """远程锁: 读取设防状态 (myCarData.lock), 支持开锁/上锁控制。

    状态约定与 binary_sensor 一致: 接口值 0 = 已锁定/设防, 1 = 解锁。
    """

    _attr_has_entity_name = True
    _attr_translation_key = "lock_control"

    def __init__(self, coordinator, client, motor, pke: str) -> None:
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = pke
        self._item_name = get_motor_field(motor, "itemName")
        self._attr_unique_id = f"{pke}_lock_control"

    @property
    def device_info(self):
        # 统一挂载到唯一主设备, 与传感器/二进制传感器/设备追踪器保持一致
        return device_info(self._pke, self._motor)

    @property
    def available(self) -> bool:
        # 可用性仅取决于账号车辆列表: 网络波动/数据缺失时保持可用, 状态优雅降级
        return motor_is_known(self._client, self._pke)

    @property
    def is_locked(self) -> Optional[bool]:
        """当前设防状态: 接口值 0 = 已锁定/设防, 1 = 解锁, 未知返回 None。"""
        data = self.coordinator.data
        if not data:
            return None
        val = data.get("by_motor", {}).get(self._pke, {}).get("myCarData", {}).get("lock")
        if val is None:
            return None
        return str(val) == "0"

    @property
    def icon(self) -> str:
        return "mdi:lock" if self.is_locked else "mdi:lock-open"

    async def async_lock(self, **kwargs) -> None:
        """上锁/设防。"""
        await self._set_lock_state(locked=True)

    async def async_unlock(self, **kwargs) -> None:
        """开锁/解锁。"""
        await self._set_lock_state(locked=False)

    async def _set_lock_state(self, locked: bool) -> None:
        """下发控制指令; 成功则立即刷新协调器状态, 失败抛出 HomeAssistantError。"""
        if not await self._client.async_set_lock_state(self._pke, locked):
            action = "lock" if locked else "unlock"
            raise HomeAssistantError(
                f"Failed to {action} motorcycle {self._item_name or self._pke}"
            )
        # 指令成功下发后主动刷新, 让实体状态尽快与服务端对齐
        await self.coordinator.async_request_refresh()
