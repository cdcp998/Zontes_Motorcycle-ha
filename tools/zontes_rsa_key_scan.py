# -*- coding: utf-8 -*-
"""zontes_rsa_key_scan.py — 从内存/堆转储中提取 RSA-2048 公钥 (离线通用工具).

用途 (2026-09-09 实测立功):
  升仕 4510 协议服务端对"登录帧"与"指令帧"使用两把不同的 RSA 私钥
  (登录=A, 指令=K1)。K1 密钥随官方 App 版本可能轮换; App 更新导致
  控车失效时, 可用本工具从运行中的官方 App 内存转储里快速定位新的
  "活动指令公钥", 再经 ZONTES_4510_CMD_KEY 环境变量热替换上线。

用法:
  # 1) (平板 root) 导出运行中 App 的堆内存 (region space 常为 0x02000000-0x42000000)
  #    adb shell su -c "dd if=/proc/<PID>/mem of=/data/local/tmp/rspace.bin \
  #                      bs=4096 skip=8192 count=262144 conv=noerror,sync"
  #    adb pull /data/local/tmp/rspace.bin ./rspace.bin
  # 2) 本机扫描 (与内嵌 A/K1 比对, 自动标注新钥):
  python tools/zontes_rsa_key_scan.py ./rspace.bin [--show-known] [--all]

输出: 每个 RSA-2048 公钥的 sha256(modulus)/引用次数/起始偏移/DER-Base64;
      "NEW" 标注表示既非登录钥 A 也非指令钥 K1 的候选 (疑似新轮换钥).

说明: 仅依赖 pycryptodome; 不发起任何网络请求; 不含任何账号/凭据.
"""
import argparse
import base64
import hashlib
import mmap
import os
import sys

# 已知公钥 (公开 DER-Base64; A=登录帧钥, K1=指令帧钥)
KEY_A = ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAmbPvFemEPV+0Qbl0kmUfIHIf"
         "lBdKvlp9CmIuAxxpkfMcvAS4DNqGd8xn7ce3FFeDoUixF8JEFgfsek+bcSXgbc3E8Uj1u"
         "iBY8MBHNz07C4W+iKQeywkspZhiR65cBJMQye7NQt69Lfc2Uqh66PElyEINg5P3iOLfR3"
         "zsqSRZe6RFItoowpEWA53VEWTvwGU26uqWJQFfVvb6KztxAvCUi+4U43kt1ejwmFLCLQh"
         "8EODQdJIYaCRfSeRl+EcFA1MG8egIFkd+0mbkKeerm5TvUHDWbUXrnXV5/QEWA7JcO2oX"
         "DomyHxIBTD9dQu4q79oSMpD+oXiAfaiy7Jv+RlUOJQIDAQAB")
KEY_K1 = ("MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4dC+NDZ5+sLY6On61P3vhtb1kj1"
          "ESDmhUtI1pmusCteb5gyG+RZAwhTAX7laECNDUNMMmRpUsmO+9YJtGTPERXwWfmPM6YhgsD9"
          "3D4cYa0N6y9g+YFvfYZMuUplPYyAylP1Gj+MVidy0/xHw7KGKmwkARsJpUXmj89UqAomEhlL"
          "wXtT0s216zZR3o9CByFPqnOtjYPNRmP9tDfHeMGgkXIVCzG7/z4VbEBQ6s99XDvzncUQUI7N"
          "wLAi0ZMz9+poC7C7eHtQAQdX4Wwlbe91L49rac48tV0C5bF34QkYw0RtwHsLEgPpmfdaYF4"
          "1jlnZU5EVEDPzxkgb/zKfvgI6jpQIDAQAB")

_SPKI = bytes.fromhex("30820122300d06092a864886f70d01010105000382010f00")
_PKCS1 = bytes.fromhex("3082010a0282010100")


def to_mod(der: bytes) -> bytes:
    b = der[-257:]
    return b[1:] if b[0] == 0 else der[-256:]


def scan(path: str):
    with open(path, "rb") as f:
        mm = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    keys = {}
    for pname, pat in (("spki", _SPKI), ("pkcs1", _PKCS1)):
        start = 0
        ln = 294 if pname == "spki" else 270
        while True:
            i = mm.find(pat, start)
            if i < 0:
                break
            blob = mm[i : i + ln]
            if len(blob) == ln:
                mod = to_mod(blob)
                h = hashlib.sha256(mod).hexdigest()
                if h not in keys:
                    keys[h] = {"b64": base64.b64encode(blob).decode(), "n": 0, "first": i}
                keys[h]["n"] += 1
            start = i + 1
    mm.close()
    return keys


def main():
    ap = argparse.ArgumentParser(description="Extract RSA-2048 public keys from a memory dump")
    ap.add_argument("dump", help="path to raw memory/heap dump file")
    ap.add_argument("--all", action="store_true", help="print full DER base64 for every key")
    ap.add_argument("--show-known", action="store_true", help="also print known A/K1 rows when found")
    a = ap.parse_args()
    if not os.path.exists(a.dump):
        print("dump not found:", a.dump)
        sys.exit(2)
    keys = scan(a.dump)
    known = {
        hashlib.sha256(to_mod(base64.b64decode(KEY_A))).hexdigest(): "A (login)",
        hashlib.sha256(to_mod(base64.b64decode(KEY_K1))).hexdigest(): "K1 (command)",
    }
    print("distinct RSA-2048 keys:", len(keys))
    print(f"{'tag':16s} {'sha256(mod)':18s} {'count':>5s}  offset")
    for h, info in sorted(keys.items(), key=lambda kv: kv[1]["first"]):
        tag = known.get(h, "NEW (rotate?)")
        if tag != "A (login)" and tag != "K1 (command)" or a.show_known:
            print(f"{tag:16s} {h[:16]:18s} {info['n']:5d}  0x{info['first']:x}")
            if a.all or tag.startswith("NEW"):
                print("   DER-B64:", info["b64"])


if __name__ == "__main__":
    main()
