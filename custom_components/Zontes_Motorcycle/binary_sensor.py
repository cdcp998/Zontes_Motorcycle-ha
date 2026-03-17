import logging
from typing import Optional
from homeassistant.components.binary_sensor import BinarySensorEntity, BinarySensorDeviceClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
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
        entities.append(ZontesLockBinarySensorMulti(coordinator, client, motor))
    async_add_entities(entities)


class ZontesLockBinarySensorMulti(CoordinatorEntity, BinarySensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "lock"
    _attr_device_class = BinarySensorDeviceClass.LOCK

    def __init__(self, coordinator, client, motor):
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = motor["PKECode"]
        self._attr_unique_id = f"{self._pke}_lock_binary"

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._pke)},
            "name": self._motor.get("ItemName", "Zontes Motorcycle"),
            "manufacturer": "Zontes",
            "model": self._motor.get("ItemName", "Unknown"),
            "sw_version": self._motor.get("VersionCode") or "初始固件",
        }
    @property
    def available(self) -> bool:
        """实体可用性：车辆仍在有效列表中"""
        if not self._client.motors:
            return False
        return any(motor.get("PKECode") == self._pke for motor in self._client.motors)

    @property
    def is_on(self) -> Optional[bool]:
        data = self.coordinator.data
        if not data:
            return None
        motor_data = data.get("by_motor", {}).get(self._pke, {})
        index = motor_data.get("index", {})
        ds = motor_data.get("data_service", {})
        val = None
        if index and "myCarData" in index:
            val = index["myCarData"].get("Lock")
        if val is None and ds and "myCarData" in ds:
            val = ds["myCarData"].get("Lock")
        if val is not None:
            return val == "1"
        return None

    @property
    def icon(self) -> str:
        return "mdi:lock-open" if self.is_on else "mdi:lock"