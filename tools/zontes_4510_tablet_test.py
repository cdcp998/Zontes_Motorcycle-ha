# -*- coding: utf-8 -*-
"""平板(安卓)端 4510 网络出口控制变量探针 (脱敏, 凭据走环境变量).

目的 (2026-09-09 实测背景): PC 与平板内直发同一逐字节帧, 登录均 OK, 但指令
均静默无回显; 而同账号《骑仕》App 在平板上可正常控车。本工具用于把与本仓库
完全一致的帧从平板自身网络栈发出 (adb push + toybox nc), 排除宿主机网络干扰,
复现/验证该会话态差异。

前置: 安卓平板已 root 并开启 adb (nc 与 sh 即可, 无需 python)。

环境变量:
  ZONTES_USER_CODE / ZONTES_PKE / ZONTES_MCUID / ZONTES_MAC_GUID(可选)
  ZONTES_ADB              可选: adb 可执行文件路径 (默认取仓库 platform-tools)
  ZONTES_SERIAL           可选: adb 设备序列号 (默认第一台)
  ZONTES_4510_HOST / ZONTES_4510_PORT  网关覆盖 (默认 61.145.9.116:4510)

用法:
  python zontes_4510_tablet_test.py gen --cmd ULoc [--outdir ./tpayloads]
      # 生成本地 RSA 载荷 (login.bin/cmd.bin), 帧内容与仓库 api.py 逐字节一致
  python zontes_4510_tablet_test.py send --gap-ms 250 [--serial xxx]
      # 推送到平板并以平板网络栈单连接发出 (login -> 间隔 -> cmd), 打印服务端回显
  python zontes_4510_tablet_test.py full --cmd ULoc --gap-ms 250
      # gen + send 一步完成
"""
import argparse
import base64
import hashlib
import os
import random
import subprocess
import sys
import time

try:
    from Crypto.Cipher import PKCS1_v1_5, AES
    from Crypto.PublicKey import RSA
except ImportError:
    print("需要 pycryptodome: pip install pycryptodome")
    sys.exit(1)

# ---------------------------------------------------------------------------
HOST = os.environ.get("ZONTES_4510_HOST", "61.145.9.116")
PORT = os.environ.get("ZONTES_4510_PORT", "4510")
VERSION = os.environ.get("ZONTES_4510_VERSION", "1.56")

USER_CODE = os.environ.get("ZONTES_USER_CODE", "")
PKE_CODE = os.environ.get("ZONTES_PKE", "")
MCUID = os.environ.get("ZONTES_MCUID", "")

RSA_PUBLIC_KEY_B64 = (
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmbPvFemEPV+0Qbl0kmUfIHIf"
    "lBdKvlp9CmIuAxxpkfMcvAS4DNqGd8xn7ce3FFeDoUixF8JEFgfsek+bcSXgbc3E8Uj1u"
    "iBY8MBHNz07C4W+iKQeywkspZhiR65cBJMQye7NQt69Lfc2Uqh66PElyEINg5P3iOLfR3"
    "zsqSRZe6RFItoowpEWA53VEWTvwGU26uqWJQFfVvb6KztxAvCUi+4U43kt1ejwmFLCLQh"
    "8EODQdJIYaCRfSeRl+EcFA1MG8egIFkd+0mbkKeerm5TvUHDWbUXrnXV5/QEWA7JcO2oX"
    "DomyHxIBTD9dQu4q79oSMpD+oXiAfaiy7Jv+RlUOJQIDAQAB"
)
AES_FIXED_KEY = b"TAYOBTa1YCWc2gTS"


def _guid() -> str:
    g = os.environ.get("ZONTES_MAC_GUID", "")
    if g:
        return g
    h = hashlib.md5(USER_CODE.encode()).hexdigest()
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _rsa_key():
    return RSA.import_key(base64.b64decode(RSA_PUBLIC_KEY_B64))


def _rsa_encrypt(plain: bytes) -> bytes:
    return PKCS1_v1_5.new(_rsa_key()).encrypt(plain)


def _hash_frame(seq: str, timestamp: str, hex_input: str) -> str:
    mq = bytes.fromhex(seq)
    d = AES.new(AES_FIXED_KEY, AES.MODE_ECB).encrypt(bytes.fromhex(hex_input))
    key = bytearray(16)
    for i in range(16):
        key[i] = d[i] if i < 12 else mq[i - 12] ^ d[i]
    plain = bytearray(16)
    plain[0:14] = timestamp.encode()[:14]
    plain[14] = 0
    plain[15] = 0
    return AES.new(bytes(key), AES.MODE_ECB).encrypt(bytes(plain)).hex()


def _rand_seq() -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(8))


def login_plain() -> str:
    str_ul = USER_CODE.lstrip("Z").ljust(32, "0")
    q, ts = _rand_seq(), time.strftime("%Y%m%d%H%M%S")
    return f"*UL,{USER_CODE},{_guid()},0.0,0.0,{q},{_hash_frame(q, ts, str_ul).upper()},{VERSION}"


def cmd_plain(cmd: str) -> str:
    q, ts = _rand_seq(), time.strftime("%Y%m%d%H%M%S")
    return f"*{cmd},{USER_CODE},{PKE_CODE},{q},{_hash_frame(q, ts, MCUID).upper()}"


def gen_payloads(outdir: str, cmd: str):
    os.makedirs(outdir, exist_ok=True)
    for name, plain in (("login.bin", login_plain()), (f"{cmd}.bin", cmd_plain(cmd))):
        with open(os.path.join(outdir, name), "wb") as f:
            f.write(_rsa_encrypt(plain.encode()))
        print(f"{name} <- {plain}")


def _adb(args: list) -> str:
    adb = os.environ.get("ZONTES_ADB", "")
    if not adb:
        cand = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "_probe", "platform-tools", "platform-tools", "adb.exe")
        adb = cand if os.path.exists(cand) else "adb"
    serial = os.environ.get("ZONTES_SERIAL", "")
    full = [adb] + (["-s", serial] if serial else []) + args
    return subprocess.run(full, capture_output=True, text=True, timeout=90).stdout


def send_payloads(outdir: str, cmd: str, gap_ms: int, keep: bool = False):
    gap_s = gap_ms / 1000.0
    login_bin = os.path.join(outdir, "login.bin")
    cmd_bin = os.path.join(outdir, f"{cmd}.bin")
    print(_adb(["push", login_bin, "/data/local/tmp/zt_login.bin"]).strip())
    print(_adb(["push", cmd_bin, "/data/local/tmp/zt_cmd.bin"]).strip())
    shell = (f"(cat /data/local/tmp/zt_login.bin; sleep {gap_s}; cat /data/local/tmp/zt_cmd.bin) "
             f"| nc -q 35 -4 {HOST} {PORT} > /data/local/tmp/zt_reply.bin")
    print(_adb(["shell", shell]).strip() or "(sent)")
    out = _adb(["shell", "cat /data/local/tmp/zt_reply.bin"]).encode()
    if not keep:
        _adb(["shell", "rm -f /data/local/tmp/zt_login.bin /data/local/tmp/zt_cmd.bin /data/local/tmp/zt_reply.bin"])
    text = "".join(chr(c) if 32 <= c < 127 else ("\n" if c == 10 else ".") for c in out)
    print("\n=== 平板 nc 收到的服务端回显 ===")
    print(text.strip())
    print("==================================")
    print()
    # 判定
    ok_login = b",OK#" in out and b"*BR,1#" in out
    ok_cmd = (cmd.encode() in out) and b",OK#" in out
    print(f"登录确认={ok_login} 指令回显={ok_cmd}")
    if ok_login and not ok_cmd:
        print("提示: 与 2026-09-09 观测一致 — 登录通过但指令被服务端静默丢弃,")
        print("      差异在服务端会话态而非帧内容/网络出口. 参见 api.py 头注释.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["gen", "send", "full"])
    ap.add_argument("--cmd", default="ULoc", help="ULoc(上锁) / UClear(开锁)")
    ap.add_argument("--outdir", default="tablet_payloads")
    ap.add_argument("--gap-ms", type=int, default=250, help="平板内 login 与 cmd 间隔(毫秒)")
    ap.add_argument("--keep", action="store_true", help="保留平板上的载荷/回显文件")
    a = ap.parse_args()
    if not (USER_CODE and PKE_CODE and MCUID):
        print("缺少环境变量 ZONTES_USER_CODE/ZONTES_PKE/ZONTES_MCUID")
        sys.exit(2)
    if a.mode in ("gen", "full"):
        gen_payloads(a.outdir, a.cmd)
    if a.mode in ("send", "full"):
        send_payloads(a.outdir, a.cmd, a.gap_ms, a.keep)


if __name__ == "__main__":
    main()
