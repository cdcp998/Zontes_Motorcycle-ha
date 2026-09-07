"""车辆位置 GPS 设备追踪器 (carLocation, GCJ-02 -> WGS-84 纠偏)。"""

import logging
from typing import Optional, Tuple

from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .common import device_info, get_motor_field, get_pke, motor_is_known
from .const import CONF_COORD_TYPE, DEFAULT_COORD_TYPE, DOMAIN
from .convert import gcj02_to_wgs84

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
            entities.append(
                ZontesDeviceTracker(coordinator, client, motor, pke, config_entry)
            )
    async_add_entities(entities)


class ZontesDeviceTracker(CoordinatorEntity, TrackerEntity):
    """车辆位置 (GPS 设备追踪器, 接口返回 GCJ-02, 统一纠偏为 WGS-84)。"""

    _attr_has_entity_name = True
    _attr_translation_key = "location"
    _attr_source_type = "gps"
    _attr_icon = "mdi:motorbike"

    def __init__(
        self, coordinator, client, motor, pke: str, config_entry: ConfigEntry
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = pke
        self._config_entry = config_entry
        self._item_name = get_motor_field(motor, "itemName")
        self._attr_unique_id = f"{pke}_location"

    @property
    def device_info(self):
        # 统一挂载到唯一主设备; 无任何 EntityCategory, 不生成分类卡片
        return device_info(self._pke, self._motor)

    @property
    def available(self) -> bool:
        return motor_is_known(self._client, self._pke)

    def _get_coordinates(self) -> Tuple[Optional[float], Optional[float]]:
        """读取 carLocation 并统一将 GCJ-02 纠偏为 WGS-84, 返回 (lat, lon)。"""
        data = self.coordinator.data
        if not data:
            return None, None
        car_location = data.get("by_motor", {}).get(self._pke, {}).get("carLocation", {})
        if not isinstance(car_location, dict):
            return None, None
        try:
            lon = float(car_location.get("longitude") or 0)
            lat = float(car_location.get("latitude") or 0)
        except (TypeError, ValueError):
            return None, None
        if not lon or not lat:
            return None, None
        coord_type = self._config_entry.options.get(
            CONF_COORD_TYPE,
            self._config_entry.data.get(CONF_COORD_TYPE, DEFAULT_COORD_TYPE),
        )
        if coord_type == "gcj02":
            return gcj02_to_wgs84(lat, lon)
        return lat, lon

    @property
    def latitude(self) -> Optional[float]:
        lat, _ = self._get_coordinates()
        return lat

    @property
    def longitude(self) -> Optional[float]:
        _, lon = self._get_coordinates()
        return lon
