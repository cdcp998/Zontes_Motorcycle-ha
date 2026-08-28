# -*- coding: utf-8 -*-
"""Zontes 4510 远程控制客户端 (独立参考实现).

通过逆向升仕官方 App (com.tayo.msbox) 提取的私有 TCP 控制协议:

协议通道 : TCP 61.145.9.116:4510 (自定义文本协议, 帧以 '#' 分隔)
加密方式 : 每帧用内嵌 RSA-2048 公钥加密 (RSA/ECB/PKCS1Padding) -> 256 字节
帧签名   : AES-128-ECB-NoPadding, 固定密钥 "TAYOBTa1YCWc2gTS"
  - 登录帧 *UL   : 签名输入 = userCode 去 Z 前缀补零到 32 hex
  - 控制帧       : 签名输入 = mcuid (32 hex)
  - 时间戳       : yyyyMMddHHmmss (14 位) + 0x00 + 0x00

指令:
  - 上锁: *ULoc,  <userCode>,<pke>,<seq8>,<hash>
  - 开锁: *UClear,<userCode>,<pke>,<seq8>,<hash>

用法:
  python zontes_4510_client.py lock|unlock
"""
import asyncio
import hashlib
import random
import sys
import time

try:
    from Crypto.Cipher import PKCS1_v1_5, AES
    from Crypto.PublicKey import RSA
except ImportError:
    print("需要 pycryptodome: pip install pycryptodome")
    sys.exit(1)

# --------------------------------------------------------------------------
# 协议常量 (从 App 内存中提取)
# --------------------------------------------------------------------------
CONTROL_HOST = "61.145.9.116"
CONTROL_PORT = 4510

RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmbPvFemEPV+0Qbl0kmUfIHIf"
    "lBdKvlp9CmIuAxxpkfMcvAS4DNqGd8xn7ce3FFeDoUixF8JEFgfsek+bcSXgbc3E8Uj1u"
    "iBY8MBHNz07C4W+iKQeywkspZhiR65cBJMQye7NQt69Lfc2Uqh66PElyEINg5P3iOLfR3"
    "zsqSRZe6RFItoowpEWA53VEWTvwGU26uqWJQFfVvb6KztxAvCUi+4U43kt1ejwmFLCLQh"
    "8EODQdJIYaCRfSeRl+EcFA1MG8egIFkd+0mbkKeerm5TvUHDWbUXrnXV5/QEWA7JcO2oX"
    "DomyHxIBTD9dQu4q79oSMpD+oXiAfaiy7Jv+RlUOJQIDAQAB"
)
AES_FIXED_KEY = b"TAYOBTa1YCWc2gTS"

# 账号相关 (来自 REST 登录与车辆档案)
USER_CODE = "Z202601280382"          # 登录返回 data.user.code
PKE_CODE = "868508086783183"         # 车辆 PKECode
MCUID = "EAAD2C99E71F45F117F87030B1195498"  # 车辆 mcuid
APP_VERSION = "1.55"

# macGuid 不被服务器校验, 可任意稳定值 (此处由 userCode 派生)
def _make_mac_guid(seed: str) -> str:
    h = hashlib.md5(seed.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

MAC_GUID = _make_mac_guid(USER_CODE)


def _rsa_key():
    import base64
    return RSA.import_key(base64.b64decode(RSA_PUBLIC_KEY_B64))


def _rsa_encrypt(plain: bytes) -> bytes:
    return PKCS1_v1_5.new(_rsa_key()).encrypt(plain)


def _hash_frame(seq: str, timestamp: str, hex_input: str) -> str:
    """实现 xlb.f()/a(): AES(M(hex_input)) 派生密钥 -> 加密 时间戳+00+00."""
    mq = bytes.fromhex(seq)                       # 8 位 seq -> 4 字节
    d = AES.new(AES_FIXED_KEY, AES.MODE_ECB).encrypt(bytes.fromhex(hex_input))
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
    return AES.new(bytes(key), AES.MODE_ECB).encrypt(bytes(plain)).hex()


def _now_ts14() -> str:
    return time.strftime("%Y%m%d%H%M%S")


def _rand_seq() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(8))


def _login_frame() -> bytes:
    str_ul = USER_CODE.lstrip("Z").ljust(32, "0")
    q, ts = _rand_seq(), _now_ts14()
    frame = f"*UL,{USER_CODE},{MAC_GUID},0.0,0.0,{q},{_hash_frame(q, ts, str_ul)},{APP_VERSION}#"
    return frame.encode()


def _heartbeat_frame() -> bytes:
    ts = time.strftime("%Y/%m/%d %H:%M:%S")
    return f"*UH,{USER_CODE},{ts}#".encode()


def _command_frame(cmd: str) -> bytes:
    q, ts = _rand_seq(), _now_ts14()
    frame = f"*{cmd},{USER_CODE},{PKE_CODE},{q},{_hash_frame(q, ts, MCUID)}"
    return frame.encode()


async def _read_until(reader, marker: bytes, timeout: float) -> bytes:
    """读取直到出现 marker 或总超时.

    实测 (2026-08-28): 指令回显与设备确认 *AM 之间可能长达 0.8~5s 无数据
    (车辆蜂窝唤醒), 空闲等待绝不能提前放弃, 否则误判失败触发无谓重试.
    """
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), max(0.2, end - time.time()))
        except asyncio.TimeoutError:
            continue  # 空闲期间继续等待, 直到总超时
        except Exception:
            break
        if not chunk:
            break
        buf += chunk
        if marker in buf:
            return buf
    return buf


async def async_send_command(command: str, timeout: float = 12.0) -> bool:
    """发送控制指令 (lock='ULoc' 上锁 / unlock='UClear' 开锁).

    Returns: True 表示设备确认执行成功 (AM,2,1 或 AM,1,1).
    实测结论: 心跳帧 *UH 对短会话多余 (无心跳指令同样被接受), 已移除.
    """
    assert command in ("UClear", "ULoc")
    reader, writer = await asyncio.open_connection(CONTROL_HOST, CONTROL_PORT)
    try:
        # 1. 登录
        writer.write(_rsa_encrypt(_login_frame()))
        await writer.drain()
        # 2. 等待登录确认 (未确认则直接失败, 避免多发无效指令)
        resp = await _read_until(reader, b",OK#", 5.0)
        if b",OK#" not in resp:
            return False
        # 3. 发送控制指令
        writer.write(_rsa_encrypt(_command_frame(command)))
        await writer.drain()
        # 4. 等待设备确认 (UClear 成功=AM,2,1 / ULoc 成功=AM,1,1)
        want = b"AM,2,1" if command == "UClear" else b"AM,1,1"
        resp = await _read_until(reader, want, timeout)
        return want in resp
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "unlock"
    if cmd in ("lock", "ULoc"):
        command, label = "ULoc", "上锁"
    else:
        command, label = "UClear", "开锁"
    print(f"发送{label}指令...", flush=True)
    ok = await async_send_command(command)
    print("RESULT: " + ("SUCCESS" if ok else "FAILURE"), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
