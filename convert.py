"""纯数据转换工具 (无 Home Assistant 依赖, 可独立单元测试)。

集中处理原始接口数值解析、单位换算与 GPS 坐标纠偏 (GCJ-02 -> WGS-84)。
"""
from __future__ import annotations

import math
from typing import Any, Optional


# ---------------------------------------------------------------------------
# GCJ-02 -> WGS-84 (火星坐标系 -> 国际坐标系, 中国大陆坐标偏移修正)
# ---------------------------------------------------------------------------
_GCJ_A = 6378245.0
_GCJ_EE = 0.00669342162296594323
_PI = 3.14159265358979324


def _transform_lat(x: float, y: float) -> float:
    """GCJ-02 纬度偏移量。"""
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * _PI) + 40.0 * math.sin(y / 3.0 * _PI)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * _PI) + 320.0 * math.sin(y * _PI / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lon(x: float, y: float) -> float:
    """GCJ-02 经度偏移量。"""
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * _PI) + 20.0 * math.sin(2.0 * x * _PI)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * _PI) + 40.0 * math.sin(x / 3.0 * _PI)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * _PI) + 300.0 * math.sin(x * _PI / 30.0)) * 2.0 / 3.0
    return ret


def gcj02_to_wgs84(lat: float, lon: float) -> tuple[float, float]:
    """将 GCJ-02 (中国偏移坐标系) 坐标转换为 WGS-84, 供 HA 地图使用。"""
    if not (72.004 <= lon <= 137.8347 and 0.8293 <= lat <= 55.8271):
        # 超出中国大陆范围, 无需纠偏
        return lat, lon
    d_lat = _transform_lat(lon - 105.0, lat - 35.0)
    d_lon = _transform_lon(lon - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * _PI
    magic = math.sin(rad_lat)
    magic = 1 - _GCJ_EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_GCJ_A * (1 - _GCJ_EE)) / (magic * sqrt_magic) * _PI)
    d_lon = (d_lon * 180.0) / (_GCJ_A / sqrt_magic * math.cos(rad_lat) * _PI)
    return lat - d_lat, lon - d_lon


# ---------------------------------------------------------------------------
# 原始值解析与单位换算
# ---------------------------------------------------------------------------
def parse_value(val: Any) -> Optional[Any]:
    """将接口原始值转换为可展示的值: 数字字符串 -> 数值, 空值 -> None, 其余原样返回。"""
    if val is None:
        return None
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return None
        try:
            return float(val) if "." in val else int(val)
        except ValueError:
            return val
    return val


def convert_value(api_key: str, value: Any) -> Any:
    """按字段应用缩放换算 (仅对数值生效)。"""
    if api_key in ("pressureFront", "pressureRear"):
        # 原始值乘 2 -> kPa
        return value * 2
    if api_key == "voltage":
        # 原始值除以 10 -> V
        return value / 10
    if api_key == "rideTimes":
        # 原始值除以 60 -> 小时
        return value / 60
    return value
