import logging
from typing import Optional
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE, UnitOfPressure, UnitOfLength, UnitOfSpeed,
    UnitOfElectricPotential, UnitOfTemperature, SIGNAL_STRENGTH_DECIBELS,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.translation import async_get_translations
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SENSORS = [
    ("Oil", "oil", PERCENTAGE, "mdi:fuel", SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, 0),
    ("PressureFront", "pressure_front", UnitOfPressure.KPA, "mdi:car-tire-alert", SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, 2),
    ("PressureRear", "pressure_rear", UnitOfPressure.KPA, "mdi:car-tire-alert", SensorDeviceClass.PRESSURE, SensorStateClass.MEASUREMENT, 2),
    ("Range", "range", UnitOfLength.KILOMETERS, "mdi:road-variant", SensorDeviceClass.DISTANCE, SensorStateClass.MEASUREMENT, 1),
    ("Speed", "speed", UnitOfSpeed.KILOMETERS_PER_HOUR, "mdi:speedometer", SensorDeviceClass.SPEED, SensorStateClass.MEASUREMENT, 1),
    ("Voltage", "voltage", UnitOfElectricPotential.VOLT, "mdi:car-battery", SensorDeviceClass.VOLTAGE, SensorStateClass.MEASUREMENT, 1),
    ("WaterTemp", "water_temp", UnitOfTemperature.CELSIUS, "mdi:coolant-temperature", SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, 1),
    ("ODOMileages", "odo_mileages", UnitOfLength.KILOMETERS, "mdi:counter", SensorDeviceClass.DISTANCE, SensorStateClass.TOTAL_INCREASING, 1),
    ("TripMileage", "trip_mileage", UnitOfLength.KILOMETERS, "mdi:road", SensorDeviceClass.DISTANCE, SensorStateClass.MEASUREMENT, 1),
    ("MaintenanceMileage", "maintenance_mileage", UnitOfLength.KILOMETERS, "mdi:wrench", SensorDeviceClass.DISTANCE, SensorStateClass.MEASUREMENT, 1),
    ("GSMRSSI", "gsm_rssi", SIGNAL_STRENGTH_DECIBELS, "mdi:signal", None, SensorStateClass.MEASUREMENT, 0),
    ("SatelliteNum", "satellite_num", None, "mdi:satellite-uplink", None, SensorStateClass.MEASUREMENT, 0),
    ("RideTimes", "ride_times", "h", "mdi:clock-outline", None, SensorStateClass.MEASUREMENT, 1),
    ("FaultCode", "fault_code", None, "mdi:alert", None, None, None),
    ("TripMaxSpeed", "trip_max_speed", UnitOfSpeed.KILOMETERS_PER_HOUR, "mdi:speedometer", SensorDeviceClass.SPEED, SensorStateClass.MEASUREMENT, 1),
    ("OilAuse", "oil_ause", "L/100km", "mdi:fuel", None, SensorStateClass.MEASUREMENT, 1),
    # ("Lock", "lock", None, "mdi:lock", None, None, None),
    ("vehicle_model", "vehicle_model", None, "mdi:motorbike", None, None, None),
]

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
        pke = motor.get("PKECode")
        if not pke:
            continue
        for idx, (api_key, trans_key, unit, icon, device_class, state_class, precision) in enumerate(SENSORS):
            entities.append(
                ZontesSensorMulti(
                    coordinator, client, motor, api_key, trans_key, unit, icon,
                    device_class, state_class, precision, idx + 1
                )
            )
    async_add_entities(entities)


class ZontesSensorMulti(CoordinatorEntity, SensorEntity):
    _attr_has_entity_name = True

    def __init__(self, coordinator, client, motor, api_key, translation_key, unit, icon,
                 device_class, state_class, precision, order):
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = motor["PKECode"]
        self._api_key = api_key
        self._attr_translation_key = translation_key
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_suggested_display_precision = precision
        self._attr_unique_id = f"{self._pke}_{api_key}"
        self._fault_no_fault_text = None  # 用于缓存故障码的“无故障”翻译

    async def async_added_to_hass(self) -> None:
        """当实体添加到 hass 时调用，获取本地化字符串。"""
        await super().async_added_to_hass()
        # 仅当是故障码传感器时预获取翻译
        if self._api_key == "FaultCode":
            language = self.hass.config.language
            translations = await async_get_translations(self.hass, language, "entity", [DOMAIN])
            # 构建完整的翻译键
            key = f"component.{DOMAIN}.entity.sensor.fault_code.no_fault"
            self._fault_no_fault_text = translations.get(key, "No Fault")
            _LOGGER.debug("FaultCode no_fault translation for %s: %s", language, self._fault_no_fault_text)

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._pke)},
            "name": self._motor.get("ItemName", "Zontes Motorcycle"),
            "manufacturer": "Zontes",
            "model": self._motor.get("ItemName", "Unknown"),
            "sw_version": self._motor.get("VersionCode") or "初始固件",
            "serial_number": self._motor.get("PKECode") or "未知PKE",
        }

    @property
    def available(self) -> bool:
        if not self._client.motors:
            return False
        return any(motor.get("PKECode") == self._pke for motor in self._client.motors)

    @property
    def native_value(self):
        if not self.available:
            return None
        data = self.coordinator.data
        if not data:
            return None
        if self._api_key == "vehicle_model":
            return self._motor.get("ItemName")

        motor_data = data.get("by_motor", {}).get(self._pke, {})
        index = motor_data.get("index")
        ds = motor_data.get("data_service")

        val = None
        if index and "myCarData" in index:
            val = index["myCarData"].get(self._api_key)
        if val is None and ds and "myCarData" in ds:
            val = ds["myCarData"].get(self._api_key)

        # 特殊处理故障码：如果为空或空字符串，返回本地化的"没有故障"
        if self._api_key == "FaultCode":
            if val is None or (isinstance(val, str) and val.strip() == ""):
                # 如果缓存尚未准备好，返回默认英文
                return self._fault_no_fault_text or "No Fault"
            else:
                return val

        if val is not None:
            parsed = self._parse_value(val)
            return self._apply_conversion(parsed)
        return None

    def _parse_value(self, val):
        """将字符串转换为数字，如果为空则返回 None"""
        if val is None:
            return None
        if isinstance(val, str):
            val = val.strip()
            if not val:  # 空字符串或仅空格
                return None
            if val.replace('.', '', 1).isdigit():
                return float(val) if '.' in val else int(val)
            return val
        return val

    def _apply_conversion(self, value):
        if value is None:
            return None
        if self._api_key in ["PressureFront", "PressureRear"]:
            return value * 2
        if self._api_key in ["ODOMileages", "TripMileage", "Voltage", "OilAuse"]:
            return value / 10
        if self._api_key == "RideTimes":
            return value / 60
        return value