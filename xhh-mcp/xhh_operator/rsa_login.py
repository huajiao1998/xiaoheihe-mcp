"""纯标准库的 RSA 公钥加密（PKCS#1 v1.5），只用来加密登录手机号。

不引用 cryptography —— 只要公钥加密，一个模幂就够了。
"""
import base64
import os

# App 里 NDKTools.getrsakey 返回的公开值
LOGIN_PUBKEY_PEM = """-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQDZgjVwAiKTjZ55nG+mW6r3TSU4
ECvNYqDMIS/bhCj2QaH5GI/KZb2TBp+CBvUj9SLFnmJQ0kzHzHoGZCQ88VevCffF
7JePGF9cmKQqotlfTKbV4oxV5iLz7JSG6b/Vg7AXtrTolNtWsa8HiB0tI0YClYaQ
lOXm4UxLeSxQwSFETwIDAQAB
-----END PUBLIC KEY-----"""


def _der_read(data, pos):
    """读一个 DER 对象，返回 (tag, value, next_pos)。"""
    tag = data[pos]
    pos += 1
    length = data[pos]
    pos += 1
    if length & 0x80:
        n = length & 0x7F
        length = int.from_bytes(data[pos:pos + n], "big")
        pos += n
    return tag, data[pos:pos + length], pos + length


def parse_public_key(pem):
    """从 SubjectPublicKeyInfo 里取出 (n, e)。"""
    body = "".join(line for line in pem.splitlines() if not line.startswith("-----"))
    der = base64.b64decode(body)
    _, spki, _ = _der_read(der, 0)          # SEQUENCE
    _, _, pos = _der_read(spki, 0)          # AlgorithmIdentifier
    _, bitstring, _ = _der_read(spki, pos)  # BIT STRING
    _, rsa_pub, _ = _der_read(bitstring[1:], 0)  # 跳过 unused-bits 字节
    _, n_bytes, pos = _der_read(rsa_pub, 0)
    _, e_bytes, _ = _der_read(rsa_pub, pos)
    return int.from_bytes(n_bytes, "big"), int.from_bytes(e_bytes, "big")


def encrypt(plain_text, pem=LOGIN_PUBKEY_PEM):
    """加密成 base64（每 76 字符一行、末尾换行，跟 App 的 androidBase64 一致）。"""
    n, e = parse_public_key(pem)
    k = (n.bit_length() + 7) // 8
    msg = plain_text.encode()
    if len(msg) > k - 11:
        raise ValueError("明文太长")
    ps = bytearray()
    while len(ps) < k - 3 - len(msg):
        b = os.urandom(1)[0]
        if b:
            ps.append(b)
    block = b"\x00\x02" + bytes(ps) + b"\x00" + msg
    cipher = pow(int.from_bytes(block, "big"), e, n).to_bytes(k, "big")
    b64 = base64.b64encode(cipher).decode()
    return "\n".join(b64[i:i + 76] for i in range(0, len(b64), 76)) + "\n"
