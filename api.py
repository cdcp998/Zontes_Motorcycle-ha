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
# ---------------------------------------------------------------------------
# 4510 远程控制协议 (从官方 App com.tayo.msbox 逆向提取)
#   通道: TCP 61.145.9.116:4510, 自定义文本协议, 帧以 '#' 分隔
#   加密: 每帧用内嵌 RSA-2048 公钥加密 (RSA/ECB/PKCS1Padding) -> 256 字节
#   签名: AES-128-ECB-NoPadding, 固定密钥, 输入 = 时间戳14位 + 0x00 + 0x00
#     登录帧 *UL     签名输入 = userCode 去 Z 前缀补零到 32 hex
#     控制帧         签名输入 = mcuid (32 hex)
#   指令: 上锁 = *ULoc, 开锁 = *UClear
#   时序实测: 登录确认 ~4ms, 指令回显 ~10-20ms, 设备确认 *AM 0.8~5s
#     (车辆蜂窝唤醒, 物理瓶颈); 心跳 *UH 短会话下多余, 不发送
# ---------------------------------------------------------------------------
CONTROL_HOST = "61.145.9.116"
CONTROL_PORT = 4510
CONTROL_RSA_PUBLIC_KEY = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmbPvFemEPV+0Qbl0kmUfIHIf"
    "lBdKvlp9CmIuAxxpkfMcvAS4DNqGd8xn7ce3FFeDoUixF8JEFgfsek+bcSXgbc3E8Uj1u"
    "iBY8MBHNz07C4W+iKQeywkspZhiR65cBJMQye7NQt69Lfc2Uqh66PElyEINg5P3iOLfR3"
    "zsqSRZe6RFItoowpEWA53VEWTvwGU26uqWJQFfVvb6KztxAvCUi+4U43kt1ejwmFLCLQh"
    "8EODQdJIYaCRfSeRl+EcFA1MG8egIFkd+0mbkKeerm5TvUHDWbUXrnXV5/QEWA7JcO2oX"
    "DomyHxIBTD9dQu4q79oSMpD+oXiAfaiy7Jv+RlUOJQIDAQAB"
)
CONTROL_AES_KEY = b"TAYOBTa1YCWc2gTS"
CONTROL_APP_VERSION = "1.55"
CONTROL_TIMEOUT = 12.0
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

    async def _request_post(self, url: str, params: dict, is_retry: bool = False) -> dict:
        """统一 POST 请求: 与 _request_get 相同的健壮性 (Token 过期拦截/重试/异常不外抛)。

        任何失败都不向外抛出, 返回 {} 使上层优雅降级。
        """
        headers = GLOBAL_HEADERS.copy()
        headers["x-token"] = self.access_token
        try:
            async with self._session.post(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    if is_retry:
                        return {}
                    _LOGGER.info("HTTP 401 received on POST %s, attempting to relogin", url)
                    if await self._relogin():
                        return await self._request_post(url, params, is_retry=True)
                    return {}
                data = await self._parse_json_response(resp)
                code = data.get("code")
                msg = str(data.get("msg") or data.get("message") or "")
                if not is_retry and (code in (401, 403) or "token" in msg.lower() or "过期" in msg or "重新登录" in msg):
                    _LOGGER.info("Business token expired on POST %s (%s: %s), attempting to relogin", url, code, msg)
                    if await self._relogin():
                        return await self._request_post(url, params, is_retry=True)
                return data
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("POST request to %s failed: %s", url, err)
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

    # ------------------------------------------------------------------
    # 4510 远程控制
    # ------------------------------------------------------------------
    @staticmethod
    def _aes_ecb_encrypt(key: bytes, data: bytes) -> bytes:
        """AES-128-ECB 加密 (cryptography 优先, 回退 pycryptodome)."""
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

            enc = Cipher(algorithms.AES(key), modes.ECB()).encryptor()
            return enc.update(data) + enc.finalize()
        except ImportError:  # pragma: no cover - pycryptodome fallback
            from Crypto.Cipher import AES

            return AES.new(key, AES.MODE_ECB).encrypt(data)

    @staticmethod
    def _rsa_encrypt(plain: bytes) -> bytes:
        """RSA-2048 PKCS1 v1.5 加密 (App 内嵌公钥, 服务器持私钥解密)."""
        import base64

        try:
            from cryptography.hazmat.primitives import serialization
            from cryptography.hazmat.primitives.asymmetric import padding

            pub = serialization.load_der_public_key(
                base64.b64decode(CONTROL_RSA_PUBLIC_KEY)
            )
            return pub.encrypt(plain, padding.PKCS1v15())
        except ImportError:  # pragma: no cover - pycryptodome fallback
            from Crypto.Cipher import PKCS1_v1_5
            from Crypto.PublicKey import RSA

            return PKCS1_v1_5.new(RSA.import_key(CONTROL_RSA_PUBLIC_KEY)).encrypt(plain)

    @staticmethod
    def _control_hash(seq: str, timestamp: str, hex_input: str) -> str:
        """实现 App xlb.f()/a() 帧签名: AES(M(hex_input)) 派生密钥 -> 加密时间戳.

        hex_input: 登录帧用 userCode 去 Z 补零到 32 hex; 控制帧用 mcuid.
        """
        mq = bytes.fromhex(seq)
        d = ZontesApiClient._aes_ecb_encrypt(CONTROL_AES_KEY, bytes.fromhex(hex_input))
        key = bytearray(16)
        for i in range(16):
            if i < 12:
                key[i] = d[i]
            else:
                key[i] = mq[i - 12] ^ d[i]
        plain = bytearray(16)
        plain[0:14] = timestamp.encode()[:14]
        plain[14] = 0
        plain[15] = 0
        return ZontesApiClient._aes_ecb_encrypt(bytes(key), bytes(plain)).hex()

    def _control_user_code(self) -> Optional[str]:
        if isinstance(self.user_info, dict):
            # 登录响应 data.user.code 嵌套在 "user" 下 (data.user.code="Z202612345678")
            user = self.user_info.get("user")
            if isinstance(user, dict):
                code = user.get("code") or user.get("userCode")
                if code:
                    return str(code)
            # 兼容旧结构: 顶层 code/userCode
            code = self.user_info.get("code") or self.user_info.get("userCode")
            if code:
                return str(code)
        return None

    def _control_mcuid(self, pke_code: str) -> Optional[str]:
        for motor in self.motors:
            if get_pke(motor) == pke_code:
                mcuid = get_motor_field(motor, "mcuid")
                if mcuid:
                    return str(mcuid)
        return None

    @staticmethod
    def _control_mac_guid(user_code: str) -> str:
        """macGuid 服务器不校验, 用 userCode 派生稳定值即可."""
        import hashlib

        h = hashlib.md5(user_code.encode()).hexdigest()
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def _control_login_frame(self, user_code: str) -> bytes:
        import random
        import time as _time

        str_ul = user_code.lstrip("Z").ljust(32, "0")
        seq = "".join(str(random.randint(0, 9)) for _ in range(8))
        ts = _time.strftime("%Y%m%d%H%M%S")
        frame = (
            f"*UL,{user_code},{self._control_mac_guid(user_code)},0.0,0.0,"
            f"{seq},{self._control_hash(seq, ts, str_ul)},{CONTROL_APP_VERSION}#"
        )
        return frame.encode()

    @staticmethod
    def _control_heartbeat_frame(user_code: str) -> bytes:
        """心跳帧 *UH (官方 App 用于维持长连接, 每秒一次).

        本集成是"连接->指令->关闭"的短会话, 实测无需心跳指令即被接受,
        故 _send_4510_command 不再发送; 此方法保留作协议参考.
        """
        import time as _time

        ts = _time.strftime("%Y/%m/%d %H:%M:%S")
        return f"*UH,{user_code},{ts}#".encode()

    def _control_command_frame(self, command: str, user_code: str, pke_code: str, mcuid: str) -> bytes:
        import random
        import time as _time

        seq = "".join(str(random.randint(0, 9)) for _ in range(8))
        ts = _time.strftime("%Y%m%d%H%M%S")
        return f"*{command},{user_code},{pke_code},{seq},{self._control_hash(seq, ts, mcuid)}".encode()

    async def _send_4510_command(self, command: str, pke_code: str) -> bool:
        """连接 4510 通道: 登录 -> 发送指令 -> 等待设备确认.

        command: "ULoc" 上锁 / "UClear" 开锁

        实测结论 (2026-08-28, benchmark_4510.py):
        - 登录确认约 4ms, 指令回显约 10~20ms, 设备确认 *AM 为 0.8~5s
          (车辆蜂窝唤醒耗时, 属物理瓶颈, 只能等不能省)
        - 心跳帧 *UH 对本短会话是多余的: 无心跳时指令同样被接受 (实测成功),
          且登录后立刻补发旧格式心跳反而可能导致会话静默丢弃指令 (实测失败)
        - 服务器对短时间内的重复连接有限流 (登录帧无响应/命令 FAIL),
          故仍保留 1 次带 2s 退避的重试; 任何最终失败返回 False, 绝不外抛
        """
        import asyncio
        import time as _time

        user_code = self._control_user_code()
        mcuid = self._control_mcuid(pke_code)
        if not user_code or not mcuid:
            _LOGGER.warning("Control info missing (user_code=%s mcuid=%s)", user_code, mcuid)
            return False
        for attempt in range(2):
            if attempt > 0:
                _LOGGER.debug("4510 %s retry attempt %d", command, attempt + 1)
                await asyncio.sleep(2.0)
            try:
                reader, writer = await asyncio.open_connection(CONTROL_HOST, CONTROL_PORT)
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("4510 connect failed: %s", err)
                continue
            try:
                # 1. 登录
                writer.write(self._rsa_encrypt(self._control_login_frame(user_code)))
                await writer.drain()
                # 2. 等待登录确认; 未确认 (如被限流) 直接判本次失败,
                #    避免向服务器多发无效指令加重限流
                login_resp = await self._read_until(reader, b",OK#", 5.0)
                if b",OK#" not in login_resp:
                    _LOGGER.warning("4510 login not confirmed (attempt %d)", attempt + 1)
                    continue
                # 3. 发送控制指令 (无需心跳帧, 见方法 docstring)
                writer.write(self._rsa_encrypt(self._control_command_frame(command, user_code, pke_code, mcuid)))
                await writer.drain()
                # 4. 直接等待完整确认帧 (AM,2,1=UClear成功 / AM,1,1=ULoc成功),
                #    避免以 "AM," 作短 marker 读到半截帧导致误判失败
                want = b"AM,2,1" if command == "UClear" else b"AM,1,1"
                resp = await self._read_until(reader, want, CONTROL_TIMEOUT)
                ok = want in resp
                _LOGGER.debug("4510 %s response: %s -> %s", command, resp, ok)
                if ok:
                    return True
            except Exception as err:  # noqa: BLE001
                _LOGGER.error("4510 %s command failed: %s", command, err)
            finally:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
        return False

    @staticmethod
    async def _read_until(reader, marker: bytes, timeout: float) -> bytes:
        """读取直到出现 marker 或总超时; 任何失败返回已读内容.

        实测 (2026-08-28, benchmark_4510.py): 服务器在登录确认后,
        指令回显与设备确认帧 *AM 之间可能长达 0.8~5 秒没有任何数据
        (车辆蜂窝唤醒耗时), 期间也不会收到任何保活帧.
        因此这里的空闲等待**绝不能提前放弃**, 否则会把正常慢速确认误判为
        失败, 触发无谓的重连重试 (旧实现 0.2s 空闲即 break, 正是语音控制
        "秒开 vs 死等十几秒" 玄学延迟的根因).
        """
        import asyncio
        import time as _time

        buf = b""
        end = _time.time() + timeout
        while _time.time() < end:
            try:
                # 空闲超时只用于周期性检查剩余时间/连接状态, 超时后继续等待
                chunk = await asyncio.wait_for(reader.read(4096), max(0.2, end - _time.time()))
            except asyncio.TimeoutError:
                continue
            except Exception:  # noqa: BLE001
                break
            if not chunk:
                break
            buf += chunk
            if marker in buf:
                break
        return buf

    async def async_set_lock_state(self, pke_code: str, locked: bool) -> bool:
        """远程下发车辆锁状态指令 (4510 私有 TCP 协议).

        Args:
            pke_code: 车辆 PKE 码
            locked: True = 上锁/设防 (*ULoc), False = 解锁 (*UClear)

        Returns:
            True = 设备已确认执行成功; 任何失败返回 False, 绝不外抛.
        """
        if not self.access_token or not pke_code:
            return False
        command = "ULoc" if locked else "UClear"
        try:
            return await self._send_4510_command(command, pke_code)
        except Exception as err:  # noqa: BLE001
            _LOGGER.error("Error setting lock state (pke=%s, locked=%s): %s", pke_code, locked, err)
            return False

    async def close(self) -> None:
        """关闭底层 HTTP 会话 (卸载集成时调用)。"""
        await self._session.close()
