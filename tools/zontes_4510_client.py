# -*- coding: utf-8 -*-
"""Zontes 4510 远程控制客户端 (独立参考实现, 无凭据, 凭据经环境变量注入).

通过逆向《骑仕》(ua.pp.kex.shiride) 与升仕官方 App 提取的私有 TCP 控制协议:

  协议通道 : TCP 61.145.9.116:4510 (自定义文本协议, 帧以 '#' 分隔)
  加密方式 : 每帧用内嵌 RSA-2048 公钥加密 (RSA/ECB/PKCS1Padding) -> 256 字节
  帧签名   : AES-128-ECB-NoPadding, 固定密钥 "TAYOBTa1YCWc2gTS"
    - 登录帧 *UL   : 签名输入 = userCode 去 Z 前缀补零到 32 hex
    - 控制帧       : 签名输入 = mcuid (32 hex)
    - 时间戳       : yyyyMMddHHmmss (14 位) + 0x00 + 0x00

帧纪律 (2026-09-09 与骑仕 1.56 抓包逐字节对齐, 8/8 帧哈希离线反算一致):
  - 哈希一律大写 32 hex; 登录帧 *UL 结尾不带 '#'; seq 为 8 位随机数字;
  - 成功判据 = 服务器指令回显 '*,OK#' (骑仕收到约 40ms 后即关闭连接,
    车辆异步执行; 设备确认 *AM 属长连接才有的附加回执, 不再强制等待).

配置 (全部经环境变量注入, 本文件不落任何真实凭据):
  ZONTES_USER_CODE  账号 userCode (如 Z2026xxxxxxxx)
  ZONTES_PKE        车辆 PKECode (如 8685xxxxxxxxx)
  ZONTES_MCUID      车辆 mcuid (32 hex)
  ZONTES_MAC_GUID   可选: 登录帧第三字段 GUID (默认由 userCode 派生)
  ZONTES_4510_HOST / ZONTES_4510_PORT / ZONTES_4510_VERSION  网关覆盖

用法:
  python zontes_4510_client.py lock|unlock
"""
import asyncio
import base64
import hashlib
import os
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
# 协议常量
# --------------------------------------------------------------------------
CONTROL_HOST = os.environ.get("ZONTES_4510_HOST", "61.145.9.116")
CONTROL_PORT = int(os.environ.get("ZONTES_4510_PORT", "4510"))
APP_VERSION = os.environ.get("ZONTES_4510_VERSION", "1.56")

RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmbPvFemEPV+0Qbl0kmUfIHIf"
    "lBdKvlp9CmIuAxxpkfMcvAS4DNqGd8xn7ce3FFeDoUixF8JEFgfsek+bcSXgbc3E8Uj1u"
    "iBY8MBHNz07C4W+iKQeywkspZhiR65cBJMQye7NQt69Lfc2Uqh66PElyEINg5P3iOLfR3"
    "zsqSRZe6RFItoowpEWA53VEWTvwGU26uqWJQFfVvb6KztxAvCUi+4U43kt1ejwmFLCLQh"
    "8EODQdJIYaCRfSeRl+EcFA1MG8egIFkd+0mbkKeerm5TvUHDWbUXrnXV5/QEWA7JcO2oX"
    "DomyHxIBTD9dQu4q79oSMpD+oXiAfaiy7Jv+RlUOJQIDAQAB"
)
AES_FIXED_KEY = b"TAYOBTa1YCWc2gTS"

# 指令帧专用 RSA 公钥 (K1, 2026-09-09 自官方 msbox v1.56 内存提取):
# 登录帧用上方 RSA_PUBLIC_KEY_B64(A 钥), 指令/保活帧必须用此钥, 否则被静默丢弃.
CMD_RSA_PUBLIC_KEY_B64 = os.environ.get("ZONTES_4510_CMD_KEY", "") or (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4dC+NDZ5+sLY6On61P3vhtb1kj1"
    "ESDmhUtI1pmusCteb5gyG+RZAwhTAX7laECNDUNMMmRpUsmO+9YJtGTPERXwWfmPM6YhgsD9"
    "3D4cYa0N6y9g+YFvfYZMuUplPYyAylP1Gj+MVidy0/xHw7KGKmwkARsJpUXmj89UqAomEhlL"
    "wXtT0s216zZR3o9CByFPqnOtjYPNRmP9tDfHeMGgkXIVCzG7/z4VbEBQ6s99XDvzncUQUI7N"
    "wLAi0ZMz9+poC7C7eHtQAQdX4Wwlbe91L49rac48tV0C5bF34QkYw0RtwHsLEgPpmfdaYF4"
    "1jlnZU5EVEDPzxkgb/zKfvgI6jpQIDAQAB"
)

USER_CODE = os.environ.get("ZONTES_USER_CODE", "")
PKE_CODE = os.environ.get("ZONTES_PKE", "")
MCUID = os.environ.get("ZONTES_MCUID", "")


def _derive_guid(seed: str) -> str:
    """userCode 派生稳定 GUID (未覆盖时使用)."""
    if not seed:
        return ""
    h = hashlib.md5(seed.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


MAC_GUID = os.environ.get("ZONTES_MAC_GUID", "") or _derive_guid(USER_CODE)


def _rsa_key():
    return RSA.import_key(base64.b64decode(RSA_PUBLIC_KEY_B64))


def _rsa_encrypt(plain: bytes) -> bytes:
    return PKCS1_v1_5.new(_rsa_key()).encrypt(plain)


def _rsa_encrypt_cmd(plain: bytes) -> bytes:
    """指令帧专用加密 (K1 钥)."""
    import base64 as _b64
    return PKCS1_v1_5.new(RSA.import_key(_b64.b64decode(CMD_RSA_PUBLIC_KEY_B64))).encrypt(plain)


def _hash_frame(seq: str, timestamp: str, hex_input: str) -> str:
    """实现 App xlb.f()/a(): AES(M(hex_input)) 派生密钥 -> 加密 时间戳+00+00."""
    mq = bytes.fromhex(seq)  # 8 位 seq -> 4 字节
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


def login_frame() -> bytes:
    """登录帧 *UL (骑仕逐字节对齐: 大写哈希, 版本后不带 '#')."""
    str_ul = USER_CODE.lstrip("Z").ljust(32, "0")
    q, ts = _rand_seq(), _now_ts14()
    frame = f"*UL,{USER_CODE},{MAC_GUID},0.0,0.0,{q},{_hash_frame(q, ts, str_ul).upper()},{APP_VERSION}"
    return frame.encode()


def heartbeat_frame() -> bytes:
    """心跳帧 *UH (官方 App 用于维持长连接, 每秒一次; 短会话无需发送)."""
    ts = time.strftime("%Y/%m/%d %H:%M:%S")
    return f"*UH,{USER_CODE},{ts}#".encode()


def command_frame(cmd: str) -> bytes:
    """控制帧 (哈希大写与骑仕一致; 指令帧本就不带结尾 '#')."""
    q, ts = _rand_seq(), _now_ts14()
    frame = f"*{cmd},{USER_CODE},{PKE_CODE},{q},{_hash_frame(q, ts, MCUID).upper()}"
    return frame.encode()


async def _read_until(reader, marker: bytes, timeout: float) -> bytes:
    """读取直到出现 marker 或总超时; 空闲等待绝不能提前放弃."""
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        try:
            chunk = await asyncio.wait_for(reader.read(4096), max(0.2, end - time.time()))
        except asyncio.TimeoutError:
            continue
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

    Returns: True = 服务器回显接受 '*,OK#' (骑仕同款成功判据, 车辆异步执行);
    *AM 设备确认属长连接才有的附加回执, 不再等待. 心跳 *UH 短会话下多余, 不发送.
    """
    assert command in ("UClear", "ULoc")
    if not (USER_CODE and PKE_CODE and MCUID):
        print("缺少环境变量 ZONTES_USER_CODE/ZONTES_PKE/ZONTES_MCUID")
        return False
    reader, writer = await asyncio.open_connection(CONTROL_HOST, CONTROL_PORT)
    try:
        writer.write(_rsa_encrypt(login_frame()))
        await writer.drain()
        resp = await _read_until(reader, b",OK#", 5.0)
        if b",OK#" not in resp:
            print("login 未获确认:", resp[-200:])
            return False
        writer.write(_rsa_encrypt_cmd(command_frame(command)))
        await writer.drain()
        resp = await _read_until(reader, b",OK#", timeout)
        ok = b",OK#" in resp
        print(("成功: 服务器回显接受 " if ok else "未获回显: ") + repr(resp[-200:]))
        return ok
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
    print(f"发送{label}指令 ({command})...", flush=True)
    ok = await async_send_command(command)
    print("RESULT: " + ("SUCCESS" if ok else "FAILURE"), flush=True)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    asyncio.run(main())
