"""小黑盒 App native 签名（纯标准库）。

对应 heybox-daily 的 native-algorithm.ts。用在 `/account/`（登录）和 `/task/`（签到）——
跟 `/chat/`（私信）那套 7 位 hkey 不是一回事。

两套哈希都实现了：
  - hkey : 标准 HMAC-SHA512 + 自定义 CRC32 多项式 0x88A4B5A0
  - _rnd : 魔改 SHA-512（K 表后半段换过、Ch 函数换过）+ CRC32 0xAD350290
           登录时 `_rnd` 不带也行，签到必须带。

对拍过：sha("abc")、hmac("key","data")、完整签名三处跟原版 JS 逐字节一致。

注意坑：提取 K 表时前导零要 zfill（左边补），别 ljust——0x0fc19dc68b8cd5b5 这类值
补错边会得到完全不同的结果，而且不会报错，只会算出错的签名。
"""
"""native 签名的 _rnd 部分用到的魔改 SHA-512。

跟标准 SHA-512 的区别（源码注释原话：deliberately NOT advertised as standard）：
  - K 常量表后半段换过
  - Ch 用 (x & (y ^ z)) ^ y，标准是 (x & y) ^ (~x & z)
  - 折叠函数是 ror 21/31/39、ror 14/18/41、ror 1/8/7、ror 38/55/6
"""

_MASK = (1 << 64) - 1

_K = [
    0x428A2F98D728AE22,
    0x7137449123EF65CD,
    0xB5C0FBCFEC4D3B2F,
    0xE9B5DBA58189DBBC,
    0x3956C25BF348B538,
    0x59F111F1B605D019,
    0x923F82A4AF194F9B,
    0xAB1C5ED5DA6D8118,
    0xD807AA98A3030242,
    0x12835B0145706FBE,
    0x243185BE4EE4B28C,
    0x550C7DC3D5FFB4E2,
    0x72BE5D74F27B896F,
    0x80DEB1FE3B1696B1,
    0x9BDC06A725C71235,
    0xC19BF174CF692694,
    0xE49B69C19EF14AD2,
    0xEFBE4786384F25E3,
    0x0FC19DC68B8CD5B5,
    0x240CA1CC77AC9C65,
    0x2DE92C6F592B0275,
    0x4A7484AA6EA6E483,
    0x5CB0A9DCBD41FBD4,
    0x76F988DA831153B5,
    0x983E5152EE66DFAB,
    0xA831C66D2DB43210,
    0xB00327C898FB213F,
    0xBF597FC7BEEF0EE4,
    0xC6E00BF33DA88FC2,
    0xD5A79147930AA725,
    0x06CA6351E003826F,
    0x142929670A0E6E70,
    0x27B70A8546D22FFC,
    0x2E1B21385C26C926,
    0x4D2C6DFC5AC42AED,
    0x53380D139D95B3DF,
    0x650A73548BAF63DE,
    0x766A0ABB3C77B2A8,
    0x81C2C92E47EDAEE6,
    0x92722C851482353B,
    0xA2BFE8A14CF10364,
    0xA81A664BBC423001,
    0xC24B8B70D0F89791,
    0xC76C51A30654BE30,
    0xD192E819D6EF5218,
    0xD69906245565A910,
    0xF40E35855771202A,
    0x106AA07032BBD1B8,
    0x19A4C116B8D2D0C8,
    0x1E376C085141AB53,
    0x2748774CDF8EEB99,
    0x34B0BCB5E19B48A8,
    0x391C0CB3C5C95A63,
    0x4ED8AA4AE3418ACB,
    0x5B9CCA4F7763E373,
    0x682E6FF3D6B2B8A3,
    0x748F82EE5DEFB2FC,
    0x78A5636F43172F60,
    0x84C87814A1F0AB72,
    0x8CC702081A6439EC,
    0x7BA0EA2D98160007,
    0x7EABF2D0C21F964A,
    0x8DBE8D038B409545,
    0x90BB1721582E8285,
    0x99A2AD45936D4E61,
    0x9F86E289FE03E73A,
    0xA84C4472FAA9A82F,
    0xB3DF34FCE89E0532,
    0xB99BB8D7B173534F,
    0xBC76CBAB1AEA1F9C,
    0xC226A69A780F3CC3,
    0xD304F19AA233957D,
    0xDE1BE20A212129DD,
    0xE39BB43755141950,
    0xEE84927CEA48DDD2,
    0xF3EDD2773C523B67,
    0xFBFDFE53A8D32F2A,
    0x0BEE2C7AB77E9E25,
    0x0E90181CF1B09E56,
    0x25F57204C725BED8,
]

_IV = [
    0x6A09E667F3BCC908,
    0xBB67AE8584CAA73B,
    0x3C6EF372FE94F82B,
    0xA54FF53A5F1D36F1,
    0x510E527FADE682D1,
    0x9B05688C2B3E6C1F,
    0x1F83D9ABFB41BD6B,
    0x5BE0CD19137E2179,
]

import hmac
import hashlib


def _ror(x, bits):
    return ((x >> bits) | (x << (64 - bits))) & _MASK


def _S0(x):
    return _ror(x, 21) ^ _ror(x, 31) ^ _ror(x, 39)


def _S1(x):
    return _ror(x, 14) ^ _ror(x, 18) ^ _ror(x, 41)


def _s0(x):
    return _ror(x, 1) ^ _ror(x, 8) ^ (x >> 7)


def _s1(x):
    return _ror(x, 38) ^ _ror(x, 55) ^ (x >> 6)


def _Ch(x, y, z):
    """魔改点：标准 SHA-512 是 (x & y) ^ (~x & z)。"""
    return (x & (y ^ z)) ^ y


def _Maj(x, y, z):
    return (x & (y | z)) | (y & z)


def custom_sha512(data):
    size = ((len(data) + 1 + 16 + 127) // 128) * 128
    padded = bytearray(size)
    padded[:len(data)] = data
    padded[len(data)] = 0x80
    bits = len(data) * 8
    padded[size - 16:size - 8] = (bits >> 64).to_bytes(8, "big")
    padded[size - 8:size] = (bits & _MASK).to_bytes(8, "big")

    state = list(_IV)
    for offset in range(0, size, 128):
        W = [0] * 80
        for i in range(16):
            W[i] = int.from_bytes(padded[offset + i * 8: offset + i * 8 + 8], "big")
        for i in range(16, 80):
            W[i] = (_s1(W[i - 2]) + W[i - 7] + _s0(W[i - 15]) + W[i - 16]) & _MASK
        a, b, c, d, e, f, g, h = state
        for i in range(80):
            t1 = (h + _S1(e) + _Ch(e, f, g) + _K[i] + W[i]) & _MASK
            t2 = (_S0(a) + _Maj(a, b, c)) & _MASK
            a, b, c, d, e, f, g, h = (t1 + t2) & _MASK, a, b, c, (d + t1) & _MASK, e, f, g
        v = [a, b, c, d, e, f, g, h]
        for i in range(8):
            state[i] = (state[i] + v[i]) & _MASK
    return b"".join(x.to_bytes(8, "big") for x in state)


def _eE2(value):
    """native 回调：字节级混淆，超过 127 字节截断。"""
    raw = value.encode("utf-8")
    nul = raw.find(b"\x00")
    limit = min(127, len(raw) if nul < 0 else nul)
    out = bytearray(raw[:limit])
    for i in range(len(out)):
        c = out[i]
        x = (c & 0x7F) ^ 0x37
        out[i] = ((c & 7) << 4) ^ 0x37 if (x == 0 or (i + 1) % 3 == 0) else x
    return bytes(out)


def _eE3(value):
    out = bytearray(_eE2(value))
    for i in range(len(out)):
        c = out[i]
        x = (c & 0x7F) ^ 0x46
        out[i] = ((c & 31) << 2) ^ 0x46 if (x == 0 or (i + 1) % 5 == 0) else x
    return bytes(out)


def custom_hmac_sha512(key, data):
    if len(key) > 128:
        key = custom_sha512(key)
    ipad = bytearray([0x52] * 128)
    opad = bytearray([0x6C] * 128)
    for i, byte in enumerate(key):
        ipad[i] ^= byte
        opad[i] ^= byte
    return custom_sha512(bytes(opad) + custom_sha512(bytes(ipad) + data))


def _reflected_crc32(data, poly):
    crc = 0xFFFFFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


_DAILY_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def native_signature(path, time_s, debug_info, os_version="17", version="1.3.398", nonce=None):
    """完整 native 签名，含 _rnd（签到需要它）。"""
    import datetime
    import random

    time_s = str(time_s)
    key = b"LFEJGJJOKCEHODNMNFIKKONFBKIHJFKB"
    digest = hmac.new(key, (path + time_s + debug_info).encode(), hashlib.sha512).digest()
    mixed = custom_hmac_sha512(_eE2(path + time_s),
                               _eE3(debug_info + os_version + version)).hex()

    if nonce is None:
        dt = datetime.datetime.utcfromtimestamp(int(time_s))
        day = dt.year * 10000 + dt.month * 100 + dt.day
        alphabet = "".join(_DAILY_ALPHABET[(day + 705 + i * 7) % 62] for i in range(32))
        nonce = "".join(alphabet[random.randrange(32)] for _ in range(32))

    return {
        "_time": time_s,
        "nonce": nonce,
        "hkey": format(_reflected_crc32(digest, 0x88A4B5A0), "X"),
        "_rnd": "14:" + format(_reflected_crc32(mixed.encode("ascii"), 0xAD350290), "X"),
    }


def native_hkey(path, time_s, debug_info):
    """只要 hkey 的轻量接口（登录用，不需要 _rnd）。"""
    return native_signature(path, time_s, debug_info)["hkey"]


def sign_params(path, time_s, debug_info, os_version="17", version="1.3.398"):
    """返回可以直接塞进 query 的签名字段（_time / nonce / hkey / _rnd）。"""
    return native_signature(path, time_s, debug_info, os_version, version)
