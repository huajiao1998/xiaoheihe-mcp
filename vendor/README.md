# vendor/encode.pyc

**来源**：`chr233/Xiaoheihe_CSharp` 的 `doc/hkey server.7z` → PyInstaller 解包 → `PYZ-00.pyz_extracted/encode.pyc`
（MIT 项目，仅作算法出处留档；原始版权归原作者）

**用途**：小黑盒 **App 端**的 `hkey` 签名算法。网页端签名是另一套（`xhh_operator/signing.py`）。

## 现在不依赖它了

算法已按字节码逐条反汇编，重写成纯 Python：**`xhh_operator/app_sign.py`**。
跟原版对拍 500 组随机输入，结果完全一致。运行时**不需要 Python 3.9**，也不再读这个 `.pyc`。

这个文件留着只为两件事：

1. 万一以后要重新校对算法，原版还在这儿；
2. 记清楚算法是从哪来的。

## 如果哪天要重新跑原版

它是 cp39 字节码，只有 Python 3.9 能加载（用法示例，平时用不到）：

```bash
cd /tmp && uv run --no-project --python 3.9 python -c "
import marshal
g = {}
exec(marshal.loads(open('/path/to/encode.pyc','rb').read()[16:]), g)
print(g['hkeyencode']('/chat/send_message/', 1793106261, 'A'*32))"
```

`hkeyencode(urlpath, timestamp, nonce)`，nonce 必须 32 位 `[a-zA-Z0-9]`，返回 7 位字符串。

## 算法要点（给要读 app_sign.py 的人）

- 本质是 HMAC-SHA1 的变体，key 是 `base64(urlpath)`，message 是 `hex(timestamp + 数字字符个数)`
- 后面用了一串 GF(2^8) 上的运算（AES 的 xtime）把 4 个字符混合成两位尾数
- 代码里 `_sub_18d4` / `_sub_18f8` / `_sub_191e` 保留了原函数名，方便对着字节码核对
