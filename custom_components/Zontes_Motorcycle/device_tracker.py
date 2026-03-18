import logging
from typing import Optional
import math
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from .const import DOMAIN, CONF_COORD_TYPE, DEFAULT_COORD_TYPE

_LOGGER = logging.getLogger(__name__)

# 常量定义 (用于 GCJ-02 到 WGS-84 的转换)
a = 6378245.0
ee = 0.00669342162296594323
pi = 3.14159265358979324

def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * pi) + 20.0 * math.sin(2.0 * x * pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * pi) + 40.0 * math.sin(y / 3.0 * pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * pi) + 320.0 * math.sin(y * pi / 30.0)) * 2.0 / 3.0
    return ret

def _transform_lon(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * pi) + 20.0 * math.sin(2.0 * x * pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * pi) + 40.0 * math.sin(x / 3.0 * pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * pi) + 300.0 * math.sin(x * pi / 30.0)) * 2.0 / 3.0
    return ret

def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    if not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271):
        return lat, lon
    d_lat = _transform_lat(lon - 105.0, lat - 35.0)
    d_lon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * pi
    magic = math.sin(rad_lat)
    magic = 1 - ee * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((a * (1 - ee)) / (magic * sqrt_magic) * pi)
    d_lon = (d_lon * 180.0) / (a / sqrt_magic * math.cos(rad_lat) * pi)
    wgs_lat = lat - d_lat
    wgs_lon = lon - d_lon
    return wgs_lat, wgs_lon


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
        entities.append(ZontesDeviceTrackerMulti(coordinator, client, motor, config_entry))
    async_add_entities(entities)


class ZontesDeviceTrackerMulti(CoordinatorEntity, TrackerEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "location"

    def __init__(self, coordinator, client, motor, config_entry):
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = motor["PKECode"]
        self._config_entry = config_entry
        self._attr_unique_id = f"{self._pke}_location"
        self._attr_source_type = "gps"

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
        if not self._client.motors:
            return False
        return any(motor.get("PKECode") == self._pke for motor in self._client.motors)

    def _get_raw_coordinates(self):
        data = self.coordinator.data
        if not data:
            return None, None
        motor_data = data.get("by_motor", {}).get(self._pke, {})
        index = motor_data.get("index")
        if index is None:
            return None, None
        locations = index.get("CarLocation")
        if locations and isinstance(locations, list) and len(locations) > 0:
            try:
                lon = float(locations[0].get("Longitude"))
                lat = float(locations[0].get("Latitude"))
                return lat, lon
            except (ValueError, TypeError):
                return None, None
        return None, None

    @property
    def latitude(self) -> Optional[float]:
        lat, lon = self._get_raw_coordinates()
        if lat is None or lon is None:
            return None
        coord_type = self._config_entry.options.get(
            CONF_COORD_TYPE,
            self._config_entry.data.get(CONF_COORD_TYPE, DEFAULT_COORD_TYPE)
        )
        if coord_type == "gcj02":
            lat, lon = gcj02_to_wgs84(lat, lon)
        return lat

    @property
    def longitude(self) -> Optional[float]:
        lat, lon = self._get_raw_coordinates()
        if lat is None or lon is None:
            return None
        coord_type = self._config_entry.options.get(
            CONF_COORD_TYPE,
            self._config_entry.data.get(CONF_COORD_TYPE, DEFAULT_COORD_TYPE)
        )
        if coord_type == "gcj02":
            lat, lon = gcj02_to_wgs84(lat, lon)
        return lon

    @property
    def source_type(self) -> str:
        return "gps"

    @property
    def icon(self) -> str:
        return "mdi:motorbike"