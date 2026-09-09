# -*- coding: utf-8 -*-
"""zontes_4510_verify.py — 4510 pcap 帧解析与签名校验 (离线通用工具).

作用:
  解析 61.145.9.116:4510 抓包 (pcap 文件, 服务端回显为明文), 把每根 TCP 流
  重组为 *UL 登录 / *UClear|*ULoc 指令 / *BR / *DM / *AM 等帧, 并用与官方
  App 完全一致的 AES 派生哈希算法离线反算登录/指令帧签名, 输出 MATCH/NO.

何时使用:
  - 官方 App 升级后校验协议是否漂移 (帧格式/哈希算法是否变化);
  - 复现/调试"登录 OK 但指令无回显"类问题前, 先用本工具确认帧本身正确.

用法:
  python tools/zontes_4510_verify.py <capture.pcap>

依赖: 仅标准库 + pycryptodome; 纯离线, 不发包.
说明: 输出不含任何账号/凭据; 若 pcap 含真实会话请勿公开共享该 pcap.
"""
import datetime
import struct
import sys

_REPO_PKG = None


def _load_api():
    global _REPO_PKG
    if _REPO_PKG is not None:
        return _REPO_PKG
    import importlib.util
    import os
    import types

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    pkg_dir = os.path.join(base, "custom_components", "zontes_motorcycle")
    pkg = types.ModuleType("zontes_ut")
    pkg.__path__ = []
    sys.modules["zontes_ut"] = pkg
    for name in ("const", "common", "api"):
        spec = importlib.util.spec_from_file_location(
            "zontes_ut." + name, os.path.join(pkg_dir, name + ".py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["zontes_ut." + name] = mod
        spec.loader.exec_module(mod)
    _REPO_PKG = sys.modules["zontes_ut.api"]
    return _REPO_PKG


def _ascii(b):
    return "".join(chr(c) if 32 <= c < 127 else "." for c in b)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    data = open(sys.argv[1], "rb").read()
    endian = "<" if data[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    linktype = struct.unpack(endian + "I", data[20:24])[0]
    pos = 24
    # (ts, src, sport, dst, dport, payload)
    pkts = []
    while pos + 16 <= len(data):
        ts_s, ts_us, incl, _ = struct.unpack(endian + "IIII", data[pos : pos + 16])
        pos += 16
        pkt = data[pos : pos + incl]
        pos += incl
        off = 20 if linktype == 276 else (14 if linktype == 1 else 0)
        l3 = pkt[off:]
        if len(l3) < 20 or (l3[0] >> 4) != 4:
            continue
        ihl = (l3[0] & 0xF) * 4
        proto = l3[9]
        if proto != 6:
            continue
        src = ".".join(str(x) for x in l3[12:16])
        dst = ".".join(str(x) for x in l3[16:20])
        l4 = l3[ihl:]
        if len(l4) < 20:
            continue
        sport, dport = struct.unpack(">HH", l4[0:4])
        hlen = ((l4[12] >> 4) & 0xF) * 4
        payload = l4[hlen:]
        pkts.append((ts_s + ts_us / 1e6, src, sport, dst, dport, payload))
    # server->client plaintext frames on 4510
    api = _load_api()
    frames = []
    for ts, src, sport, dst, dport, p in pkts:
        if sport == 4510 and p:
            t = _ascii(p).strip()
            if t.startswith("*"):
                frames.append((ts, t))
    print("server plaintext frames:", len(frames))
    for ts, t in frames:
        hm = datetime.datetime.fromtimestamp(ts).strftime("%H:%M:%S")
        if t.startswith("*UL,"):
            seg = t.split(",")
            try:
                # *UL,user,guid,0.0,0.0,seq,hash,ver,OK#
                seq, exp = seg[5], seg[6].upper()
                user = seg[1]
                hin = user.lstrip("Z").ljust(32, "0")
            except Exception:
                print(hm, t[:120], "(login parse skip)")
                continue
            kind = "LOGIN"
        elif t.startswith(("*UClear", "*ULoc")):
            seg = t.split(",")
            try:
                seq, exp = seg[3], seg[4].upper()
            except Exception:
                print(hm, t[:120], "(cmd parse skip)")
                continue
            kind = "CMD(%s)" % seg[0][1:]
            hin = None  # mcuid unknown from echo; note
        else:
            print(hm, t[:120])
            continue
        if kind == "LOGIN":
            got = None
            for off in range(-3, 4):  # tiny clock-skew window
                d = datetime.datetime.fromtimestamp(ts) + datetime.timedelta(seconds=off)
                got = api.ZontesApiClient._control_hash(seq, d.strftime("%Y%m%d%H%M%S"), hin)
                if got.upper() == exp:
                    break
            print(f"{hm} {kind} seq={seq} hash={exp} -> {'MATCH' if got and got.upper() == exp else 'NO-MATCH'}")
        else:
            print(f"{hm} {kind} seq={seq} hash={exp} (command; mcuid-keyed, needs account ctx)")
    print("done")


if __name__ == "__main__":
    main()
