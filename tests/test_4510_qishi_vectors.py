# -*- coding: utf-8 -*-
"""4510 帧签名哈希与帧格式 — 离线回归测试 (无网络/无真实凭据).

背景
----
本测试源自对升仕私有 4510 TCP 控制协议的逆向 (协议细节与踩坑记录见
PROTOCOL_REVERSE_PLAYBOOK.md)。哈希算法在真实抓包上做过逐帧反算验证;
为保证开源仓库零隐私泄露, 本文件中的账号/车辆标识已全部替换为虚构值,
期望摘要由已验证的实现算法对"虚构标识 + 固定 seq/timestamp"重新计算并冻结,
因此任何对算法的误改都会导致断言失败, 依然具备完整回归价值.

运行:
    python tests/test_4510_qishi_vectors.py   # 独立运行
    pytest tests/test_4510_qishi_vectors.py   # pytest 兼容
"""
import importlib.util
import os
import sys
import types

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PKG_DIR = os.path.join(_REPO, "custom_components", "zontes_motorcycle")


def _load_api():
    """以包方式加载 custom_components 下的 const/common/api (api 内含相对导入)."""
    pkg = types.ModuleType("zontes_ut")
    pkg.__path__ = []
    sys.modules["zontes_ut"] = pkg
    for name in ("const", "common", "api"):
        spec = importlib.util.spec_from_file_location(
            f"zontes_ut.{name}", os.path.join(_PKG_DIR, f"{name}.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules[f"zontes_ut.{name}"] = mod
        spec.loader.exec_module(mod)
    return sys.modules["zontes_ut.api"]


API = _load_api()

# 虚构测试标识 (已脱敏; 算法输入必须为合法 hex)
USER_CODE = "Z2000ABC00000000"
MCUID = "0123456789ABCDEF0123456789ABCDEF"
PKE_CODE = "999999999999999"
STR_UL = USER_CODE.lstrip("Z").ljust(32, "0")

# 冻结向量: (seq, 时间戳, 期望哈希, 签名输入, 说明)
# 由已验证实现算法对虚构标识计算并冻结 (时间戳为真实抓包发生的秒级时刻, 不涉敏).
VECTORS = [
    ("76937714", "20260909192822", "8268561F84D3C5D39B9C9C59D0DDF5B4", STR_UL, "会话1 登录"),
    ("55681990", "20260909192822", "51DC0AE03793190DB31AA1FF22BC56EB", MCUID, "会话1 UClear"),
    ("12743087", "20260909192835", "94CC8EFC7DEB4DB172DC79ECC1C1F5DE", STR_UL, "会话2 登录"),
    ("92647062", "20260909192835", "F6AECA03B0FC98EFAC636DB521ADBB8F", MCUID, "会话2 ULoc"),
]


def _check(seq, ts, expected, hex_input, label):
    got = API.ZontesApiClient._control_hash(seq, ts, hex_input)
    assert got.upper() == expected, f"{label}: got {got.upper()} expected {expected}"


def test_all_frozen_hash_vectors():
    for seq, ts, expected, hex_input, label in VECTORS:
        _check(seq, ts, expected, hex_input, label)


def test_login_frame_is_qishi_parity():
    """登录帧: 不带结尾 '#', 哈希字段为大写 32 hex (骑仕/官方 App 逐字节对齐)."""
    frame = API.ZontesApiClient("x", "x", None)._control_login_frame(USER_CODE)
    assert isinstance(frame, bytes)
    assert not frame.endswith(b"#")
    fields = frame.split(b",")
    # 结构: *UL,userCode,guid,0.0,0.0,seq,hash,version
    assert fields[0] == b"*UL"
    assert len(fields) == 8, fields
    assert len(fields[6]) == 32 and fields[6].isalnum() and fields[6].isupper(), fields[6]


def test_command_frame_is_qishi_parity():
    """指令帧: 不带结尾 '#', 哈希字段为大写 32 hex."""
    client = API.ZontesApiClient("x", "x", None)
    for cmd in ("ULoc", "UClear"):
        frame = client._control_command_frame(cmd, USER_CODE, PKE_CODE, MCUID)
        assert isinstance(frame, bytes)
        assert not frame.endswith(b"#")
        fields = frame.split(b",")
        assert fields[0] == b"*" + cmd.encode()
        assert len(fields) == 5, fields
        assert len(fields[4]) == 32 and fields[4].isupper(), fields[4]


def test_login_and_cmd_use_different_rsa_keys():
    """定案断言 (2026-09-09): 登录钥 A 与指令钥 K1 必须不同. 用错钥加密指令帧
    会被服务端静默丢弃 (此前"登录OK但指令无回显"的根因, 详见手册).
    """
    assert API.CONTROL_RSA_PUBLIC_KEY
    assert API.CONTROL_CMD_RSA_PUBLIC_KEY
    assert API.CONTROL_CMD_RSA_PUBLIC_KEY != API.CONTROL_RSA_PUBLIC_KEY
    import base64

    from Crypto.Cipher import PKCS1_v1_5
    from Crypto.PublicKey import RSA

    for b64 in (API.CONTROL_RSA_PUBLIC_KEY, API.CONTROL_CMD_RSA_PUBLIC_KEY):
        der = base64.b64decode(b64)
        ct = PKCS1_v1_5.new(RSA.import_key(der)).encrypt(
            b"*UL,test,0.0,0.0,00000000,00000000000000000000000000000000,1.56"
        )
        assert len(ct) == 256


if __name__ == "__main__":
    import time

    t0 = time.time()
    ok = True
    for seq, ts, expected, hex_input, label in VECTORS:
        try:
            _check(seq, ts, expected, hex_input, label)
            print(f"PASS {label:12s} seq={seq} ts={ts}")
        except AssertionError as e:
            ok = False
            print(f"FAIL {label:12s} seq={seq} ts={ts}: {e}")
    for fn in (
        test_login_frame_is_qishi_parity,
        test_command_frame_is_qishi_parity,
        test_login_and_cmd_use_different_rsa_keys,
    ):
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except AssertionError as e:
            ok = False
            print(f"FAIL {fn.__name__}: {e}")
    print(f"{'ALL PASS' if ok else 'FAILURES'} in {(time.time()-t0)*1000:.0f}ms")
    sys.exit(0 if ok else 1)
