import hashlib
import logging
import json
from typing import Optional, Dict, Any, List
import aiohttp
from datetime import datetime

BASE_URL = "https://m.zontes.com"
LOGIN_PATH = "/BoxApp/ashx/UserCenter/UserInfo.ashx"
INDEX_PATH = "/BoxApp/ashx/Index/Index.ashx"
DATA_SERVICE_PATH = "/BoxApp/ashx/DataService/DataService.ashx"
USER_CENTER_PATH = "/BoxApp/ashx/UserCenter/UserCenter.ashx"

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
        self.current_motor: Optional[Dict[str, Any]] = None  # 保留以备将来
        self._current_pke: Optional[str] = None  # 记录当前车辆的 PKE 码
        self._original_pke: Optional[str] = None  # 记录最初默认车辆的 PKE 码

    async def login(self) -> bool:
        salt = "Tk9@#Auth2026!*"
        pwd_salted = self._password + salt
        md5_pwd = hashlib.md5(pwd_salted.encode("utf-8")).hexdigest()
        url = f"{BASE_URL}{LOGIN_PATH}"
        payload = {
            "method": "nowlogin",
            "logintype": "SPLogin",
            "name": self._username,
            "pwd": md5_pwd,
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15",
            "Referer": "https://m.zontes.com/BoxApp/UserCenter/Login.html",
            "Origin": "https://m.zontes.com",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "X-Requested-With": "XMLHttpRequest",
        }
        try:
            async with self._session.post(url, data=payload, headers=headers) as resp:
                text = await resp.text()
                _LOGGER.debug("Login raw response: %s", text)
                if text.startswith("logincallback(") and text.endswith(")"):
                    json_str = text[14:-1]
                else:
                    json_str = text
                data = json.loads(json_str)
                if data.get("result") == "success" and data.get("isLogin") == "True":
                    self.access_token = data["accessToken"]
                    self.user_info = data
                    return True
                else:
                    failmsg = data.get("failMsg") or data.get("failmsg") or data.get("msg") or ""
                    if "尚未注册" in failmsg:
                        raise ZontesNotRegisteredError(failmsg)
                    elif "密码错误" in failmsg or "输入密码错误" in failmsg:
                        raise ZontesWrongPasswordError(failmsg)
                    else:
                        raise ZontesAuthError(failmsg or "Unknown authentication error")
        except Exception as e:
            _LOGGER.error("Login error: %s", e)
            raise

    async def get_my_motors(self) -> List[Dict[str, Any]]:
        if not self.access_token:
            return []
        url = f"{BASE_URL}{USER_CENTER_PATH}"
        params = {
            "ACCESSTOKEN": self.access_token,
            "method": "myMotorNew",
            "source": "myList",
        }
        try:
            async with self._session.post(url, params=params) as resp:
                text = await resp.text()
                _LOGGER.debug("myMotorNew raw response: %s", text)
                data = json.loads(text)
                if data.get("isLogin") == "True":
                    motors = data.get("MyMotor", [])
                    self.motors = motors
                    # 过滤有效车辆
                    self.filter_valid_motors()
                    # 根据 isCurrentPke 更新当前车辆记录，同时记录最初默认车辆
                    self._update_current_and_original_pke()
                    return self.motors
                else:
                    _LOGGER.warning("Failed to get motors: %s", data)
                    return []
        except Exception as e:
            _LOGGER.error("Error fetching motors: %s", e)
            return []

    def filter_valid_motors(self) -> List[Dict[str, Any]]:
        """根据授权时间过滤出当前有效的车辆列表，并更新 self.motors 为有效列表"""
        now = datetime.now()
        valid_motors = []
        for motor in self.motors:
            pke = motor.get("PKECode")
            start_str = motor.get("StartTime")
            end_str = motor.get("EndTime")
            # 如果不存在 StartTime/EndTime，视为永久有效
            if not start_str or not end_str:
                valid_motors.append(motor)
                _LOGGER.debug("Motor %s (%s) has no time limit, considered valid", motor.get("ItemName"), pke)
                continue
            try:
                start = datetime.strptime(start_str, "%Y/%m/%d %H:%M:%S")
                end = datetime.strptime(end_str, "%Y/%m/%d %H:%M:%S")
                if start <= now <= end:
                    valid_motors.append(motor)
                    _LOGGER.debug("Motor %s (%s) is within authorization period", motor.get("ItemName"), pke)
                else:
                    _LOGGER.info("Motor %s (%s) is outside authorization period (start=%s, end=%s), removing",
                                 motor.get("ItemName"), pke, start_str, end_str)
            except ValueError as e:
                _LOGGER.error("Error parsing time for motor %s: %s", pke, e)
                # 解析失败时保留车辆，避免误删
                valid_motors.append(motor)
        self.motors = valid_motors
        return valid_motors

    def _update_current_and_original_pke(self):
        """从当前 motors 列表中找出 isCurrentPke 为 True 的车辆，更新 _current_pke 和 _original_pke（只在首次设置）"""
        for motor in self.motors:
            is_current = motor.get("isCurrentPke")
            if is_current in (True, "True"):
                pke = motor.get("PKECode")
                self._current_pke = pke
                # 如果 _original_pke 尚未设置，则记录为最初默认车辆
                if self._original_pke is None:
                    self._original_pke = pke
                    _LOGGER.debug("Original default motor set to %s", pke)
                _LOGGER.debug("Current motor set to %s", pke)
                return
        # 如果没有找到，则设为 None
        self._current_pke = None
        _LOGGER.debug("No current motor found")

    def _get_auth_type_for_motor(self, pke_code: str) -> Optional[str]:
        """根据车辆信息获取切换时需要的 auth_type ('auth' 或 'mine')，如果找不到车辆返回 None"""
        for motor in self.motors:
            if motor.get("PKECode") == pke_code:
                if motor.get("StartTime") and motor.get("EndTime"):
                    return "auth"
                else:
                    return "mine"
        return None

    async def change_motor(self, pke_code: str, auth_type: str = "auth") -> Optional[Dict[str, Any]]:
        """切换到指定车辆

        Args:
            pke_code: 车辆PKE码
            auth_type: 授权类型，'auth' 或 'mine'
        """
        if not self.access_token:
            return None
        url = f"{BASE_URL}{USER_CENTER_PATH}"
        params = {
            "ACCESSTOKEN": self.access_token,
            "isAuth": auth_type,
            "method": "changeMotor",
            "pkecode": pke_code,
        }
        try:
            async with self._session.post(url, params=params) as resp:
                text = await resp.text()
                _LOGGER.debug("changeMotor raw response (auth_type=%s): %s", auth_type, text)
                data = json.loads(text)
                if data.get("result") == "success":
                    # 更新 current_motor 和 _current_pke
                    for motor in self.motors:
                        if motor.get("PKECode") == pke_code:
                            self.current_motor = motor
                            break
                    self._current_pke = pke_code
                    return data
                else:
                    _LOGGER.warning("Change motor failed (auth_type=%s): %s", auth_type, data)
                    return None
        except Exception as e:
            _LOGGER.error("Error changing motor: %s", e)
            return None

    async def get_index(self) -> Optional[Dict[str, Any]]:
        if not self.access_token:
            return None
        url = f"{BASE_URL}{INDEX_PATH}"
        params = {"ACCESSTOKEN": self.access_token, "method": "getIndex"}
        try:
            async with self._session.post(url, params=params) as resp:
                text = await resp.text()
                _LOGGER.debug("Index raw response: %s", text)
                data = json.loads(text)
                if data.get("isLogin") == "True":
                    return data
                else:
                    _LOGGER.warning("Index request failed: %s", data)
                    return None
        except Exception as e:
            _LOGGER.error("Error fetching index: %s", e)
            return None

    async def get_data_service(self) -> Optional[Dict[str, Any]]:
        if not self.access_token:
            return None
        url = f"{BASE_URL}{DATA_SERVICE_PATH}"
        params = {"ACCESSTOKEN": self.access_token, "method": "getDataService"}
        try:
            async with self._session.post(url, params=params) as resp:
                text = await resp.text()
                _LOGGER.debug("DataService raw response: %s", text)
                data = json.loads(text)
                if data.get("isLogin") == "True":
                    return data
                else:
                    _LOGGER.warning("DataService request failed: %s", data)
                    return None
        except Exception as e:
            _LOGGER.error("Error fetching data service: %s", e)
            return None

    async def get_motor_data(self, pke_code: str) -> Dict[str, Optional[Dict[str, Any]]]:
        """获取指定车辆的 index 和 data_service 数据（如果已是当前车辆则跳过切换）"""
        _LOGGER.debug("Getting data for motor %s", pke_code)

        # 如果尚未加载 motors，先尝试加载（通常用户应先调用 get_my_motors）
        if not self.motors:
            _LOGGER.warning("Motor list is empty, attempting to fetch motors first")
            await self.get_my_motors()
            if not self.motors:
                _LOGGER.error("No motors available")
                return {"index": None, "data_service": None}

        # 如果目标车辆已是当前车辆，直接获取数据
        if self._current_pke == pke_code:
            _LOGGER.debug("Motor %s is already current, skipping change", pke_code)
            index = await self.get_index()
            data_service = await self.get_data_service()
            return {"index": index, "data_service": data_service}

        # 否则需要切换
        auth_type = self._get_auth_type_for_motor(pke_code)
        if auth_type is None:
            _LOGGER.warning("Motor %s not found in current motor list", pke_code)
            return {"index": None, "data_service": None}

        change_result = await self.change_motor(pke_code, auth_type)
        if not change_result:
            _LOGGER.warning("Change motor failed for %s, skipping data fetch", pke_code)
            return {"index": None, "data_service": None}

        # 切换成功后获取数据
        index = await self.get_index()
        data_service = await self.get_data_service()
        return {"index": index, "data_service": data_service}

    async def get_all_motors_data(self) -> Dict[str, Dict[str, Optional[Dict[str, Any]]]]:
        """获取所有有效车辆的数据，并在完成后恢复默认车辆（如果存在）"""
        result = {}
        # 确保 motors 已加载
        if not self.motors:
            _LOGGER.info("Motor list is empty, fetching now")
            await self.get_my_motors()
            if not self.motors:
                return result

        for motor in self.motors:
            pke = motor.get("PKECode")
            if not pke:
                continue

            data = await self.get_motor_data(pke)
            # 如果数据无效，尝试重新登录后重试一次
            if data.get("index") is None and data.get("data_service") is None:
                _LOGGER.info("Retrying motor %s after relogin", pke)
                await self.login()
                data = await self.get_motor_data(pke)
            result[pke] = data

        # 获取完所有数据后，恢复默认车辆（如果存在且不是当前车辆）
        if self._original_pke and self._original_pke != self._current_pke:
            _LOGGER.info("Restoring original default motor %s", self._original_pke)
            auth_type = self._get_auth_type_for_motor(self._original_pke)
            if auth_type:
                await self.change_motor(self._original_pke, auth_type)
            else:
                _LOGGER.warning("Original motor %s not found in list, cannot restore", self._original_pke)

        return result

    async def restore_default_motor(self) -> bool:
        """手动恢复到最初默认车辆（isCurrentPke=True 的那辆）"""
        if not self._original_pke:
            _LOGGER.warning("No original default motor recorded")
            return False
        if self._current_pke == self._original_pke:
            _LOGGER.debug("Already on default motor")
            return True
        auth_type = self._get_auth_type_for_motor(self._original_pke)
        if auth_type is None:
            _LOGGER.warning("Original motor %s not found in current motor list", self._original_pke)
            return False
        result = await self.change_motor(self._original_pke, auth_type)
        return result is not None
