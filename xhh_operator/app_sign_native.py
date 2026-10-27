"""native 签名的纯 Python 实现（对应 heybox-daily 的 native-algorithm.ts）"""
import hmac, hashlib

_KEY = b'LFEJGJJOKCEHODNMNFIKKONFBKIHJFKB'
_DAILY_ALPHABET = 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'


def _reflected_crc32(data, poly):
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ (poly if crc & 1 else 0)
    return (crc ^ 0xFFFFFFFF) & 0xFFFFFFFF


def native_hkey(path, time_s, debug_info):
    """hkey = CRC32_自定义(HMAC-SHA512(path+time+debugInfo))，7 位大写十六进制。"""
    digest = hmac.new(_KEY, (path + time_s + debug_info).encode(), hashlib.sha512).digest()
    return format(_reflected_crc32(digest, 0x88A4B5A0), 'X')


def daily_nonce(time_s, pick=None):
    """nonce 是按 UTC 日期算的确定值（32 位）。"""
    import datetime
    dt = datetime.datetime.utcfromtimestamp(int(time_s))
    day = dt.year * 10000 + dt.month * 100 + dt.day
    alphabet = ''.join(_DAILY_ALPHABET[(day + 705 + i * 7) % 62] for i in range(32))
    if pick is None:
        import random
        pick = lambda mx: random.randrange(mx)
    return ''.join(alphabet[pick(32)] for _ in range(32))
