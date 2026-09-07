"""Sensors for the Zontes Smart Motorcycle integration.

全部数据均以常规传感器实体呈现 (无 EntityCategory、无板块/分类设置):
- 动态行驶遥测 (来自 getHomeData / getDataService)
- 车辆静态档案 (来自 getMyMotorList)
- 人员与服务信息 (来自 getUserCenterData / ma/service/info)

所有实体 device_info 统一返回 {"identifiers": {(DOMAIN, pke)}},
HA 自动创建唯一设备, 界面即实体列表。
"""
import logging
from typing import Any, Optional

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS,
    UnitOfElectricPotential,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .common import device_info, get_motor_field, get_pke, motor_is_known
from .const import DOMAIN
from .convert import convert_value, parse_value

_LOGGER = logging.getLogger(__name__)


# 动态行驶遥测与车况数据 (getHomeData / getDataService)
#   (api_key, translation_key, unit, icon, device_class, state_class, precision)
TELEMETRY_SENSORS = [
    ("oil",                "oil",                 PERCENTAGE,                      "mdi:fuel",                None,                          SensorStateClass.MEASUREMENT,     0),
    # 实时胎压: 刻意不使用 SensorDeviceClass.PRESSURE —— HA 会按单位系统把 kPa 自动
    # 换算为 bar/psi, 此处去掉 device_class 以保证界面始终显示 kPa (188/230)。
    ("pressureFront",      "pressure_front",      UnitOfPressure.KPA,              "mdi:car-tire-alert",      None,                          SensorStateClass.MEASUREMENT,     0),
    ("pressureRear",       "pressure_rear",       UnitOfPressure.KPA,              "mdi:car-tire-alert",      None,                          SensorStateClass.MEASUREMENT,     0),
    ("voltage",            "voltage",             UnitOfElectricPotential.VOLT,    "mdi:car-battery",         SensorDeviceClass.VOLTAGE,     SensorStateClass.MEASUREMENT,     1),
    ("speed",              "current_speed",       UnitOfSpeed.KILOMETERS_PER_HOUR, "mdi:speedometer",         SensorDeviceClass.SPEED,       SensorStateClass.MEASUREMENT,     1),
    ("speedAverage",       "speed_average",       UnitOfSpeed.KILOMETERS_PER_HOUR, "mdi:speedometer",         SensorDeviceClass.SPEED,       SensorStateClass.MEASUREMENT,     1),
    ("tripMaxSpeed",       "trip_max_speed",      UnitOfSpeed.KILOMETERS_PER_HOUR, "mdi:speedometer",         SensorDeviceClass.SPEED,       SensorStateClass.MEASUREMENT,     1),
    ("oilAuse",            "oil_ause",            "L/100km",                       "mdi:fuel",                None,                          SensorStateClass.MEASUREMENT,     1),
    ("odomileages",        "odo_mileages",        UnitOfLength.KILOMETERS,         "mdi:counter",             SensorDeviceClass.DISTANCE,    SensorStateClass.TOTAL_INCREASING, 1),
    ("tripMileage",        "trip_mileage",        UnitOfLength.KILOMETERS,         "mdi:road",                SensorDeviceClass.DISTANCE,    SensorStateClass.MEASUREMENT,     1),
    ("maintenanceMileage", "maintenance_mileage", UnitOfLength.KILOMETERS,         "mdi:wrench",              SensorDeviceClass.DISTANCE,    SensorStateClass.MEASUREMENT,     1),
    ("range",              "range",               UnitOfLength.KILOMETERS,         "mdi:road-variant",        SensorDeviceClass.DISTANCE,    SensorStateClass.MEASUREMENT,     1),
    ("rideTimes",          "ride_times",          UnitOfTime.HOURS,                "mdi:clock-outline",       None,                          SensorStateClass.MEASUREMENT,     1),
    ("tripTimes",          "trip_times",          None,                            "mdi:counter",             None,                          SensorStateClass.MEASUREMENT,     0),
    ("waterTemp",          "water_temp",          UnitOfTemperature.CELSIUS,       "mdi:coolant-temperature", SensorDeviceClass.TEMPERATURE, SensorStateClass.MEASUREMENT, 1),
    ("gsmrssi",            "gsm_rssi",            SIGNAL_STRENGTH_DECIBELS,        "mdi:signal",              None,                          SensorStateClass.MEASUREMENT,     0),
    ("satelliteNum",       "satellite_num",       "颗",                            "mdi:satellite-uplink",    None,                          SensorStateClass.MEASUREMENT,     0),
    ("faultCode",          "fault_code",          None,                            "mdi:alert",               None,                          None,                             None),
]

# 车辆静态档案 (getMyMotorList)
#   (api_key, translation_key, icon, precision, unit)
MOTOR_INFO_SENSORS = [
    ("itemName",          "vehicle_model",       "mdi:motorbike",       None, None),
    ("pkecode",           "serial_no",           "mdi:qrcode",          None, None),
    ("cheJia",            "vin",                 "mdi:barcode",         None, None),
    ("faDongJi",          "engine_no",           "mdi:engine",          None, None),
    ("iyear",             "vehicle_year",        "mdi:calendar",        0,    None),
    ("ddate",             "build_date",          "mdi:calendar-check",  None, None),
    ("serviceValidTime",  "service_valid_time",  "mdi:shield-check",    None, None),
    ("versionCode",       "firmware_version",    "mdi:update",          None, None),
    ("liencePlate",       "license_plate",       "mdi:car",             None, None),
    ("ratedFrontPressure","rated_front_pressure","mdi:car-tire-alert",  None, UnitOfPressure.KPA),
    ("ratedRearPressure", "rated_rear_pressure", "mdi:car-tire-alert",  None, UnitOfPressure.KPA),
]

# 人员与服务信息 (getUserCenterData / getMyMotorList / ma/service/info)
#   (source, api_key, translation_key, icon)
PERSONNEL_SENSORS = [
    ("user_center",  "userCode",         "user_id",       "mdi:identifier"),
    ("user_center",  "nickName",         "owner",         "mdi:account"),
    ("motor",        "createBy",         "creator",       "mdi:account-hard-hat"),
    ("motor",        "contactOperator",  "after_sales",   "mdi:card-account-phone"),
    ("service_info", "userName",         "user_name",     "mdi:account-details"),
    ("service_info", "userMobile",       "user_mobile",   "mdi:cellphone"),
]


def sensor_unique_ids(pke: str) -> set[str]:
    """该车辆全部传感器实体的 unique_id (用于清理历史遗留实体)。"""
    keys = [item[0] for item in TELEMETRY_SENSORS]
    keys += [item[0] for item in MOTOR_INFO_SENSORS]
    keys += [item[1] for item in PERSONNEL_SENSORS]  # (source, api_key, translation_key, icon)
    return {f"{pke}_{key}" for key in keys}


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """创建全部传感器实体 (遥测 + 档案 + 人员), 均挂载到唯一设备。"""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator = entry_data["coordinator"]
    client = entry_data["client"]

    entities = []
    for motor in client.motors:
        pke = get_pke(motor)
        if not pke:
            continue

        for api_key, trans_key, unit, icon, device_class, state_class, precision in TELEMETRY_SENSORS:
            entities.append(
                ZontesSensor(
                    coordinator, client, motor, pke, "telemetry",
                    api_key, trans_key, icon, precision, unit, device_class, state_class,
                )
            )
        for api_key, trans_key, icon, precision, unit in MOTOR_INFO_SENSORS:
            entities.append(
                ZontesSensor(
                    coordinator, client, motor, pke, "motor",
                    api_key, trans_key, icon, precision, unit, None, None,
                )
            )
        for source, api_key, trans_key, icon in PERSONNEL_SENSORS:
            entities.append(
                ZontesSensor(
                    coordinator, client, motor, pke, source,
                    api_key, trans_key, icon, None, None, None, None,
                )
            )

    async_add_entities(entities)


class ZontesSensor(CoordinatorEntity, SensorEntity):
    """Zontes 传感器 (遥测 / 档案 / 人员统一使用本类, 按数据源取值)。"""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator,
        client,
        motor,
        pke: str,
        source: str,
        api_key: str,
        translation_key: str,
        icon: str,
        precision: Optional[int],
        unit: Optional[str],
        device_class: Optional[str],
        state_class: Optional[str],
    ) -> None:
        super().__init__(coordinator)
        self._client = client
        self._motor = motor
        self._pke = pke
        self._source = source
        self._api_key = api_key
        self._item_name = get_motor_field(motor, "itemName")
        self._attr_translation_key = translation_key
        self._attr_icon = icon
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_suggested_display_precision = precision
        self._attr_unique_id = f"{pke}_{api_key}"
        self._no_fault_text: Optional[str] = None

    async def async_added_to_hass(self) -> None:
        """实体加入 hass 后预取故障码本地化文案。"""
        await super().async_added_to_hass()
        if self._api_key == "faultCode":
            language = self.hass.config.language
            translations = await async_get_translations(self.hass, language, "entity", [DOMAIN])
            key = f"component.{DOMAIN}.entity.sensor.fault_code.no_fault"
            self._no_fault_text = translations.get(key, "No Fault")

    @property
    def device_info(self):
        # 干净传值: 制造商/型号/固件/硬件/序列号均为原值, 不做字符串拼接
        return device_info(self._pke, self._motor)

    @property
    def available(self) -> bool:
        # 可用性仅取决于账号车辆列表: 网络波动/数据缺失时保持可用, 数值优雅降级
        return motor_is_known(self._client, self._pke)

    def _get_raw_value(self) -> Any:
        """按数据源获取接口原始值。"""
        if self._source == "telemetry":
            data = self.coordinator.data
            if not data:
                return None
            my_car = data.get("by_motor", {}).get(self._pke, {}).get("myCarData", {})
            val = my_car.get(self._api_key)
            # 兼容接口可能的字段名变更
            if self._api_key == "range" and val in (None, 0, 0.0, "0", ""):
                val = my_car.get("remainMileage", val)
            return val
        if self._source == "motor":
            if self._api_key == "pkecode":
                return self._pke
            if self._api_key == "createBy":
                key_list = self._motor.get("gxPKEFactoryEntryUKeyList") or []
                if isinstance(key_list, list) and key_list:
                    return key_list[0].get("createBy")
                return None
            return get_motor_field(self._motor, self._api_key)
        if self._source == "user_center":
            user = self._client.user_center_data
            return user.get(self._api_key) if isinstance(user, dict) else None
        if self._source == "service_info":
            info = self._client.service_info
            return info.get(self._api_key) if isinstance(info, dict) else None
        return None

    @property
    def native_value(self):
        if not self.available:
            return None
        try:
            raw = self._get_raw_value()
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Failed to read value for %s: %s", self._api_key, err)
            return None
        if self._api_key == "faultCode":
            # 无故障时显示本地化的“没有故障”
            if raw is None or str(raw).strip() == "":
                return self._no_fault_text or "No Fault"
            return raw
        parsed = parse_value(raw)
        if parsed is None:
            return None
        if isinstance(parsed, (int, float)):
            return convert_value(self._api_key, parsed)
        return parsed
