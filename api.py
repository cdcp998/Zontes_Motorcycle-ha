"""Zontes 官方 App 私有接口客户端 (5 个数据源接口)。

数据源:
1. POST /auth/oauth2/token                    登录获取 access_token
2. GET  /pkeapp/motor/getMyMotorList           车辆档案列表 (getMyMotorList)
3. GET  /pkeapp/gx/pke/carData/getUserCenterData  用户中心
4. GET  /pkeapp/gx/pke/carData/getHomeData     首页遥测 (含位置)
5. GET  /pkeapp/gx/pke/carData/getDataService  数据服务 (最高速度等)
6. GET  /zontesapp/api/ma/service/info         售后/服务信息 (说明书等)

请求层做到“绝不外抛”: 任何网络波动 / 超时 / JSON 解析失败 / 401 重试失败,
统一返回空 dict 或空列表, 由上层协调器与实体优雅降级。
"""
from datetime import datetime
import json
import logging
from typing import Any, Dict, List, Optional

import aiohttp

from .common import get_motor_field, get_pke

BASE_URL = "https://www.ifino.com:8081/zontespkeapp/api"
BASE_URL_SERVICE = "https://www.ifino.com:8081/zontesapp/api"
LOGIN_PATH = "/auth/oauth2/token"
MOTOR_LIST_PATH = "/pkeapp/motor/getMyMotorList"
HOME_DATA_PATH = "/pkeapp/gx/pke/carData/getHomeData"
DATA_SERVICE_PATH = "/pkeapp/gx/pke/carData/getDataService"
USER_CENTER_DATA_PATH = "/pkeapp/gx/pke/carData/getUserCenterData"
SERVICE_INFO_PATH = "/ma/service/info"
GLOBAL_HEADERS = {
    "User-Agent": "okhttp/4.9.3",
    "Accept": "application/json",
}

# 统一请求超时 (连接 + 读取), 防止网络异常时轮询无限挂起
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=30)

_LOGGER = logging.getLogger(__name__)


class ZontesAuthError(Exception):
    pass


class ZontesNotRegisteredError(ZontesAuthError):
    pass


class ZontesWrongPasswordError(ZontesAuthError):
    pass


class ZontesApiClient:
    def __init__(self, username: str, password: str, session: aiohttp.ClientSession):
        self._username = username
        self._password = password
        self._session = session
        self.access_token: Optional[str] = None
        self.user_info: Optional[Dict[str, Any]] = None
        self.motors: List[Dict[str, Any]] = []
        self.user_center_data: Dict[str, Any] = {}
        self.service_info: Dict[str, Any] = {}

    async def login(self) -> bool:
        """登录并获取 access_token; 认证失败抛出具体鉴权异常 (供配置流程识别)。"""
        url = f"{BASE_URL}{LOGIN_PATH}"
        payload = {
            "password": self._password,
            "grant_type": "password",
            "sys": "209",
            "lang": "CH",
            "usercode": self._username,
            "brand": "升仕",
        }
        headers = GLOBAL_HEADERS.copy()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
        try:
            async with self._session.post(url, data=payload, headers=headers) as resp:
                if resp.status != 200:
                    raise ZontesAuthError(f"Server returned HTTP status {resp.status}")
                text = await resp.text()
                _LOGGER.debug("Login raw response: %s", text)
                try:
                    data = json.loads(text)
                except Exception:
                    raise ZontesAuthError(f"Invalid JSON response: {text[:100]}")
                if data.get("code") == 200 or (data.get("data") and data["data"].get("accessToken")):
                    self.access_token = data["data"]["accessToken"]
                    self.user_info = data.get("data")
                    return True
                failmsg = data.get("msg") or data.get("message") or ""
                if "未注册" in failmsg or "不存在" in failmsg:
                    raise ZontesNotRegisteredError(failmsg)
                if "密码错误" in failmsg or "错误" in failmsg:
                    raise ZontesWrongPasswordError(failmsg)
                raise ZontesAuthError(failmsg or "Unknown authentication error")
        except Exception as e:
            _LOGGER.error("Login error: %s", e)
            raise

    async def _relogin(self) -> bool:
        """重新登录以刷新 Token。"""
        try:
            return await self.login()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Relogin failed: %s", err)
            return False

    async def _request_get(self, url: str, params: dict, is_retry: bool = False) -> dict:
        """统一 GET 请求: 自带 Token 过期拦截、重试与健全的异常容错。

        任何失败都不向外抛出, 返回 {} 使上层优雅降级。
        """
        headers = GLOBAL_HEADERS.copy()
        headers["x-token"] = self.access_token
        try:
            async with self._session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    if is_retry:
                        return {}
                    _LOGGER.info("HTTP 401 received, attempting to relogin")
                    if await self._relogin():
                        return await self._request_get(url, params, is_retry=True)
                    return {}
                data = await self._parse_json_response(resp)
                code = data.get("code")
                msg = str(data.get("msg") or data.get("message") or "")
                if not is_retry and (code in (401, 403) or "token" in msg.lower() or "过期" in msg or "重新登录" in msg):
                    _LOGGER.info("Business token expired (%s: %s), attempting to relogin", code, msg)
                    if await self._relogin():
                        return await self._request_get(url, params, is_retry=True)
                return data
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Request to %s failed: %s", url, err)
            return {}

    @staticmethod
    async def _parse_json_response(resp: aiohttp.ClientResponse) -> dict:
        """读取并解析 JSON 响应; 任何失败均返回 {} 而非抛异常。"""
        try:
            text = await resp.text()
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Failed to read response body: %s", err)
            return {}
        try:
            data = json.loads(text)
        except (json.JSONDecodeError, TypeError) as err:
            _LOGGER.error("Invalid JSON response from %s: %s", resp.url, err)
            return {}
        if not isinstance(data, dict):
            _LOGGER.error("Unexpected response shape from %s: %r", resp.url, data)
            return {}
        return data

    def filter_valid_motors(self) -> List[Dict[str, Any]]:
        """根据授权时间过滤出当前有效的车辆列表。"""
        now = datetime.now()
        valid_motors = []
        for motor in self.motors:
            start_str = get_motor_field(motor, "startTime")
            end_str = get_motor_field(motor, "endTime")
            if not start_str or not end_str:
                valid_motors.append(motor)
                continue
            try:
                cleaned_start = str(start_str).replace("-", "/")
                cleaned_end = str(end_str).replace("-", "/")
                start = datetime.strptime(cleaned_start, "%Y/%m/%d %H:%M:%S")
                end = datetime.strptime(cleaned_end, "%Y/%m/%d %H:%M:%S")
                if start <= now <= end:
                    valid_motors.append(motor)
                else:
                    _LOGGER.info(
                        "Motor %s is outside authorization period, removing",
                        get_pke(motor),
                    )
            except Exception:
                valid_motors.append(motor)
        self.motors = valid_motors
        return valid_motors

    async def get_my_motors(self) -> List[Dict[str, Any]]:
        """获取车辆档案列表 (getMyMotorList)。失败返回 [] 且不清空已有数据。"""
        if not self.access_token:
            return []
        url = f"{BASE_URL}{MOTOR_LIST_PATH}"
        params = {"source": "myList"}
        try:
            data = await self._request_get(url, params)
            _LOGGER.debug("myMotorList raw response: %s", data)
            if data.get("code") == 200:
                motors = data.get("data") or []
                if isinstance(motors, list):
                    self.motors = motors
                    self.filter_valid_motors()
                else:
                    _LOGGER.warning("Unexpected motors payload shape: %r", motors)
                    self.motors = []
                return self.motors
            _LOGGER.warning("Failed to get motors: %s", data)
            return []
        except Exception as e:
            _LOGGER.error("Error fetching motors: %s", e)
            return []

    async def get_user_center_data(self) -> Dict[str, Any]:
        """获取用户中心数据 (getUserCenterData: userCode/nickName 等)。失败保留旧数据。"""
        if not self.access_token:
            return {}
        url = f"{BASE_URL}{USER_CENTER_DATA_PATH}"
        try:
            data = await self._request_get(url, {})
            if data.get("code") == 200 and isinstance(data.get("data"), dict):
                self.user_center_data = data["data"]
            return self.user_center_data
        except Exception as e:
            _LOGGER.error("Error fetching user center data: %s", e)
            return {}

    async def get_service_info(self, pke_code: str) -> Dict[str, Any]:
        """获取售后/服务信息 (zontesapp/api/ma/service/info: 用户姓名/手机号/说明书等)。"""
        if not self.access_token:
            return {}
        url = f"{BASE_URL_SERVICE}{SERVICE_INFO_PATH}"
        try:
            data = await self._request_get(url, {"pkecode": pke_code})
            _LOGGER.debug("service/info response for %s: %s", pke_code, data)
            if data.get("code") == 200 and isinstance(data.get("data"), dict):
                self.service_info = data["data"]
            return self.service_info
        except Exception as e:
            _LOGGER.error("Error fetching service info: %s", e)
            return {}

    async def get_motor_data(self, pke_code: str) -> Dict[str, Any]:
        """获取指定车辆的 getHomeData 与 getDataService 数据并无缝合并。"""
        if not self.access_token:
            return {}
        params = {"pkecode": pke_code}
        result: Dict[str, Any] = {}
        try:
            # 1. 首页遥测 (含 carLocation 与 myCarData)
            home_data = await self._request_get(f"{BASE_URL}{HOME_DATA_PATH}", params)
            _LOGGER.debug("getHomeData response for %s: %s", pke_code, home_data)
            if home_data.get("code") == 200 and isinstance(home_data.get("data"), dict):
                result.update(home_data["data"])

            # 2. 数据服务 (最高速度等扩展字段), 融合进 myCarData
            ds_data = await self._request_get(f"{BASE_URL}{DATA_SERVICE_PATH}", params)
            _LOGGER.debug("getDataService response for %s: %s", pke_code, ds_data)
            if ds_data.get("code") == 200 and isinstance(ds_data.get("data"), dict):
                result.setdefault("myCarData", {}).update(ds_data["data"])

            return result
        except Exception as e:
            _LOGGER.error("Error fetching motor data for %s: %s", pke_code, e)
            return {}

    async def get_all_motors_data(self) -> Dict[str, Dict[str, Any]]:
        """获取所有车辆的实时数据 (单台数据失败不影响其他车辆)。"""
        result: Dict[str, Dict[str, Any]] = {}
        if not self.motors:
            await self.get_my_motors()

        for motor in self.motors:
            pke = get_pke(motor)
            if not pke:
                continue
            data = await self.get_motor_data(pke)
            if not data:
                _LOGGER.info("Empty data received for motor %s, retrying after relogin", pke)
                if await self._relogin():
                    data = await self.get_motor_data(pke)
            result[pke] = data

        return result

    async def close(self) -> None:
        """关闭底层 HTTP 会话 (卸载集成时调用)。"""
        await self._session.close()
