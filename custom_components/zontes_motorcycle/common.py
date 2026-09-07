"""跨平台通用辅助函数 (字段兼容读取 / 设备挂载 / 可用性判断)。"""
from __future__ import annotations

from typing import Any, Dict, Optional

from .const import DOMAIN, MANUFACTURER

# 旧版接口可能使用的大小写变体字段名
_FIELD_ALIASES: Dict[str, tuple] = {
    "itemName": ("ItemName",),
    "pkecode": ("PKECode", "pKECode"),
    "cheJia": ("CheJia",),
    "faDongJi": ("FaDongJi",),
    "iyear": ("IYear",),
    "ddate": ("DDate",),
    "versionCode": ("VersionCode",),
    "userCode": ("UserCode",),
    "contactOperator": ("ContactOperator",),
    "nickName": ("NickName",),
    "startTime": ("StartTime",),
    "endTime": ("EndTime",),
}


def get_motor_field(motor: Any, key: str, default: Any = None) -> Any:
    """读取车辆档案字段, 兼容接口大小写变体; 非 dict 输入安全返回 default。"""
    if not isinstance(motor, dict):
        return default
    value = motor.get(key)
    if value not in (None, ""):
        return value
    for alias in _FIELD_ALIASES.get(key, ()):
        value = motor.get(alias)
        if value not in (None, ""):
            return value
    return motor.get(key, default)


def get_pke(motor: Any) -> Optional[str]:
    """获取车辆 PKE 序列号 (设备唯一标识符)。"""
    return get_motor_field(motor, "pkecode")


def motor_is_known(client: Any, pke: str) -> bool:
    """车辆是否仍存在于账号的车辆列表中 (异常一律视为 False, 保证可用性判断不抛错)。"""
    try:
        motors = getattr(client, "motors", None)
        if not isinstance(motors, list):
            return False
        return any(get_pke(motor) == pke for motor in motors)
    except Exception:  # noqa: BLE001
        return False


def device_info(pke: str, motor: Any = None) -> Dict[str, Any]:
    """实体挂载到唯一设备所需的 device_info (HA 根据标识符自动创建设备)。

    制造商/型号/固件版本/硬件版本/序列号全部为干净的原值传值,
    不做任何冗余字符串拼接; 不带任何分类/板块设置。
    """
    info: Dict[str, Any] = {"identifiers": {(DOMAIN, pke)}}
    if not isinstance(motor, dict):
        return info
    item_name = get_motor_field(motor, "itemName")
    if item_name:
        info["name"] = item_name
        info["manufacturer"] = MANUFACTURER
        info["model"] = item_name
    fw = get_motor_field(motor, "versionCode")
    if fw:
        info["sw_version"] = fw
    engine = get_motor_field(motor, "faDongJi")
    if engine:
        info["hw_version"] = engine
    vin = get_motor_field(motor, "cheJia")
    if vin:
        info["serial_number"] = vin
    return info
