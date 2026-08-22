"""锁状态二进制传感器 (0 = 已锁定/设防, 1 = 解锁)。"""

import logging
from typing import Optional

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
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
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]
    client = entry_data["client"]

    entities = []
    for motor in client.motors:
        pke = get_pke(motor)
        if pke:
            entities.append(ZontesLockBinarySensor(coordinator, client, motor, pke))
    async_add_entities(entities)


class ZontesLockBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """锁状态: 接口值 0 = 已锁定/设防, 1 = 解锁。"""

    _attr_has_entity_name = True
    _attr_translation_key = "lock"
    _attr_device_class = BinarySensorDeviceClass.LOCK

    def __init__(self, coordinator, client, motor, pke: str) -> None:
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = pke
        self._item_name = get_motor_field(motor, "itemName")
        self._attr_unique_id = f"{pke}_lock"

    @property
    def device_info(self):
        return device_info(self._pke, self._motor)

    @property
    def available(self) -> bool:
        return motor_is_known(self._client, self._pke)

    @property
    def is_on(self) -> Optional[bool]:
        data = self.coordinator.data
        if not data:
            return None
        val = data.get("by_motor", {}).get(self._pke, {}).get("myCarData", {}).get("lock")
        if val is None:
            return None
        return str(val) == "1"

    @property
    def icon(self) -> str:
        return "mdi:lock-open" if self.is_on else "mdi:lock"
