"""小黑盒 App 通道 hkey —— 纯 Python 实现。

从 vendor/encode.pyc（官方 App native 实现的 cp39 字节码）逐条反汇编重写，
120 组随机输入与原版对拍完全一致。不再依赖 Python 3.9。

原版是官方 App 的 native 实现，被 PyInstaller 打包成 cp39 字节码；
"""
import base64
import hmac


def _gf2_mult(a, b=2):
    """GF(2^8) 上的乘 2（AES xtime），保留原实现的两个分支。"""
    return ((a << 1) ^ 0x1B) & 0xFF if a & 0x80 else (a << 1) & 0xFF


def _sub_18d4(a):
    """原 sub_18d4：两次 xtime 再异或。"""
    v1 = ((a * 2) ^ 27) & 255
    if (a & 128) == 0:
        v1 = a * 2
    v2 = ((v1 * 2) ^ 27) & 255
    if (v1 & 128) == 0:
        v2 = v1 * 2
    return v2 ^ v1


def _sub_18f8(a):
    """原 sub_18f8：跟 sub_18d4 同样算一遍，但结果外面再套一层 sub_18d4。"""
    v1 = ((a * 2) ^ 27) & 255
    if (a & 128) == 0:
        v1 = a * 2
    v2 = ((v1 * 2) ^ 27) & 255
    if (v1 & 128) == 0:
        v2 = v1 * 2
    return _sub_18d4(v2 ^ v1)


def _sub_191e(a):
    """原 sub_191e：xtime(a) ^ a ^ sub_18f8(a)，再套一层 sub_18d4。"""
    v2 = (a & 128) != 0
    v3 = ((a * 2) ^ 27) & 255
    if not v2:
        v3 = a * 2
    v4 = v3 ^ a ^ _sub_18f8(a)
    return _sub_18d4(a) ^ v4


def _sub_194c(total):
    """原 sub_194c：把 4 个字符做一轮 GF(2^8) 混合，返回 4 项之和。"""
    c0, c1, c2, c3 = (ord(total[i]) for i in range(4))

    v5 = ((c1 * 2) ^ 27) & 255
    if (c1 & 128) == 0:
        v5 = c1 * 2
    v17 = v5 ^ c1

    v23 = _sub_191e(c0)
    v22 = _sub_18f8(c1)
    v21 = _sub_18d4(c2)
    v20 = _sub_191e(c1)
    v19 = _sub_18f8(c2)
    v18 = _sub_18d4(c3)

    v7 = v17 ^ _sub_18d4(c0)
    v8 = v7 ^ _sub_191e(c2)
    v9 = v8 ^ _sub_18f8(c3)
    total2 = v9

    v13 = ((c0 * 2) ^ 27) & 255
    if (c0 & 128) == 0:
        v13 = c0 * 2
    total1 = v13 ^ c0 ^ v20 ^ v19 ^ v18

    v14 = ((c3 * 2) ^ 27) & 255
    if (c3 & 128) == 0:
        v14 = c3 * 2
    total0 = v14 ^ v23 ^ v22 ^ v21 ^ c3

    v15 = ((c2 * 2) ^ 27) & 255
    if (c2 & 128) == 0:
        v15 = c2 * 2
    total3 = _sub_191e(c3) ^ v15 ^ c2 ^ _sub_18f8(c0) ^ _sub_18d4(c1)

    return total0 + total1 + total2 + total3


def _time_add(s):
    """原 time_add：数一数字符里有几个数字。"""
    return sum(1 for ch in s if ord(ch) - 48 < 10)


def hkeyencode(urlpath, timecasp, nonce):
    """算 App 通道的 hkey（7 位）。签名同原版 hkeyencode(urlpath, timecasp, nonce)。"""
    nonce_str = nonce.upper()
    sha1_key = base64.b64encode(urlpath.encode()).decode()
    time_addi = _time_add(nonce)
    sha1_str = ("00000000" + hex(int(timecasp) + time_addi)[2:]).upper()
    sha1 = hmac.new(sha1_key.encode(), bytes.fromhex(sha1_str), digestmod="sha1").hexdigest()

    sha1_index = int("0x" + sha1[-2:], 16) & 15
    hkey_index_befor = int(sha1[sha1_index * 2: sha1_index * 2 + 8], 16) & 2147483647
    hkey_str = "23456789BCDFGHJKMNPQRTVWXY" + nonce_str

    hkey_part5 = ""
    for _ in range(5):
        v20 = hkey_index_befor // 58
        hkey_index = hkey_index_befor - 58 * v20
        hkey_index_befor = v20
        hkey_part5 += hkey_str[hkey_index]

    hkey_part2 = _sub_194c(hkey_part5[1:])
    hkey_part = hkey_part2 - 100 * (hkey_part2 // 100)
    return hkey_part5 + str(hkey_part).zfill(2)
