"""小黑盒 App 端登录（手机号 + 验证码）。

除了取设备令牌要跑官方 fp.min.js（node + jsdom），其余全是纯 Python：
手机号 RSA 加密、native 签名、HTTP 请求都不依赖第三方库。

用法：
    python -m xhh_operator.app_login                # 交互式
    python -m xhh_operator.app_login --phone 180..  # 只发验证码
    python -m xhh_operator.app_login --phone 180.. --code 1234   # 提交
"""
import argparse
import json
import os
import random
import string
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid

from .app_sign_native import native_hkey
from .rsa_login import encrypt as rsa_encrypt

API = "https://api.xiaoheihe.cn"
APP_VERSION = "1.3.398"
APP_BUILD = "1142"
JS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "js")
SESSION_PATH = os.path.expanduser("~/.config/xiaoheihe-cli/app-session.json")
# 发码和提交必须用同一套设备令牌 + device_id，跨进程靠这个文件接上
PENDING_PATH = os.path.expanduser("~/.config/xiaoheihe-cli/app-login-pending.json")
PENDING_TTL = 300  # 验证码有效期，秒


def normalize_phone(raw):
    phone = str(raw).strip().replace(" ", "").replace("-", "")
    if len(phone) == 11 and phone.isdigit() and phone[0] == "1":
        return "+86" + phone
    if phone.startswith("+") and phone[1:].isdigit() and 7 <= len(phone[1:]) <= 15:
        return phone
    raise ValueError("手机号格式不对（国内直接填 11 位）")


def device_token():
    """跑官方 fp.min.js 换设备令牌。"""
    script = os.path.join(JS_DIR, "get-device-token.mjs")
    if not os.path.exists(script):
        raise RuntimeError("没有 js/get-device-token.mjs，先跑 npm install")
    out = subprocess.run(["node", script], capture_output=True, text=True,
                         timeout=120, cwd=JS_DIR)
    if out.returncode != 0:
        raise RuntimeError("取设备令牌失败：%s" % (out.stderr or "")[:300])
    return json.loads(out.stdout)["token"]


def _request(path, params, form=None, cookie=""):
    url = API + path + "?" + urllib.parse.urlencode(params)
    data = urllib.parse.urlencode(form).encode() if form else None
    req = urllib.request.Request(url, data=data, method="POST" if form else "GET")
    req.add_header("User-Agent", "Max-Android")
    req.add_header("Accept", "application/json")
    if cookie:
        req.add_header("Cookie", cookie)
    if data:
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _params(token, device_id, extra):
    p = {
        "heybox_id": "-1", "imei": device_id, "device_info": "PKJ110",
        "os_type": "Android", "x_os_type": "Android", "x_client_type": "mobile",
        "os_version": "17", "version": APP_VERSION, "build": APP_BUILD, "dw": "360",
        "channel": "heybox", "x_app": "heybox", "time_zone": "Asia/Shanghai",
    }
    p.update(extra)
    return p


def _signed(path, token, device_id, extra=None):
    ts = int(time.time())
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    params = _params(token, device_id, extra or {})
    params.update({"_time": str(ts), "nonce": nonce,
                   "hkey": native_hkey(path, str(ts), device_id + "-1")})
    return params


def send_code(phone, token=None, device_id=None):
    token = token or device_token()
    device_id = device_id or str(uuid.uuid4())
    path = "/account/get_login_code/"
    r = _request(path, _signed(path, token, device_id),
                 {"phone_num": rsa_encrypt(normalize_phone(phone))},
                 "pkey=; x_xhh_tokenid=%s" % token)
    if str(r.get("status")) != "ok":
        raise RuntimeError("发验证码失败：%s" % (r.get("msg") or r))
    return {"token": token, "device_id": device_id, "remain": (r.get("result") or {}).get("remain_time")}


def verify_code(phone, code, token, device_id):
    path = "/account/login_code/"
    r = _request(path, _signed(path, token, device_id, {"code": str(code), "is_new_device": "1"}),
                 {"phone_num": rsa_encrypt(normalize_phone(phone))},
                 "pkey=; x_xhh_tokenid=%s" % token)
    if str(r.get("status")) != "ok":
        raise RuntimeError("登录失败：%s" % (r.get("msg") or r))
    res = r.get("result") or {}
    profile = res.get("profile") or {}
    detail = res.get("account_detail") or {}
    heybox_id = str(profile.get("heybox_id") or "")
    pkey = res.get("pkey") or ""
    if not heybox_id.isdigit() or heybox_id != str(detail.get("userid")) or not pkey:
        raise RuntimeError("返回的账号或凭据不完整，原配置保留")
    return {
        "heybox_id": heybox_id, "pkey": pkey, "x_xhh_tokenid": token,
        "device_id": device_id,
        "device": {"model": "PKJ110", "osVersion": "17"},
        "app": {"version": APP_VERSION, "build": APP_BUILD, "shaderVersion": "14"},
        "note": "小黑盒 App 端登录态，与网页 session.json 是两套",
    }


def save_pending(phone, token, device_id):
    os.makedirs(os.path.dirname(PENDING_PATH), exist_ok=True)
    with open(PENDING_PATH, "w", encoding="utf-8") as fh:
        json.dump({"phone": phone, "token": token, "device_id": device_id,
                   "ts": int(time.time())}, fh)
    os.chmod(PENDING_PATH, 0o600)


def load_pending():
    try:
        with open(PENDING_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except (FileNotFoundError, ValueError):
        return None
    age = int(time.time()) - int(data.get("ts") or 0)
    if age > PENDING_TTL:
        return None
    data["age"] = age
    return data


def clear_pending():
    try:
        os.unlink(PENDING_PATH)
    except FileNotFoundError:
        pass


def save_session(session):
    os.makedirs(os.path.dirname(SESSION_PATH), exist_ok=True)
    with open(SESSION_PATH, "w", encoding="utf-8") as fh:
        json.dump(session, fh, ensure_ascii=False, indent=2)
    os.chmod(SESSION_PATH, 0o600)


def main(argv=None):
    ap = argparse.ArgumentParser(description="小黑盒 App 登录（手机号 + 验证码）")
    ap.add_argument("--phone", help="手机号，国内直接 11 位")
    ap.add_argument("--code", help="验证码；不给就交互输入")
    ap.add_argument("--send-only", action="store_true", help="只发验证码，不登录")
    args = ap.parse_args(argv)

    # 带 --code 且没指定手机号：说明是「上一次 --send-only 的续集」，复用当时的令牌
    pending = load_pending()
    if args.code and not args.phone:
        if not pending:
            print("没有可用的发码记录（可能超过 %d 秒或没发过）。先跑：\n"
                  "  xiaoheihe-cli app-login --phone 手机号\n再跑：\n"
                  "  xiaoheihe-cli app-login --code 验证码" % PENDING_TTL, file=sys.stderr)
            return 2
        phone = pending["phone"]
        tok, dev = pending["token"], pending["device_id"]
        print("接着上次发码（%d 秒前，尾号 %s）" % (pending["age"], phone[-4:]))
        return _finish(phone, args.code, tok, dev)

    phone = args.phone or input("手机号（国内 11 位）：").strip()
    norm = normalize_phone(phone)
    print("手机号尾号 %s" % norm[-4:])

    # 发码前先清掉旧记录，避免拿过期令牌去提交
    clear_pending()
    print("取设备令牌…")
    t0 = time.time()
    tok = device_token()
    dev = str(uuid.uuid4())
    print("  ok（%d 字符，%.1fs）" % (len(tok), time.time() - t0))

    print("发验证码…")
    info = send_code(phone, tok, dev)
    save_pending(phone, tok, dev)
    print("  已发送" + ("（%s 秒后可重发）" % info["remain"] if info.get("remain") else ""))
    print("  状态已存，%d 秒内可用 --code 提交" % PENDING_TTL)

    if args.send_only:
        print("\n下一步：xiaoheihe-cli app-login --code <验证码>")
        return 0

    code = args.code or input("验证码：").strip()
    return _finish(phone, code, tok, dev)


def _finish(phone, code, tok, dev, interactive=None):
    """提交验证码；终端里可重试（验证码敲错不用重新发码），管道里只试一次。"""
    if interactive is None:
        interactive = sys.stdin.isatty()
    for attempt in range(1, 4):
        try:
            print("提交验证码…" if attempt == 1 else "重新提交…")
            session = verify_code(phone, code, tok, dev)
            save_session(session)
            clear_pending()
            print("登录成功：%s（%s）" % (session["heybox_id"], SESSION_PATH))
            return 0
        except Exception as exc:                     # noqa: BLE001
            last_err = exc
            msg = str(exc)
            print("  %s" % msg, file=sys.stderr)
            if attempt == 3 or not interactive:
                break
            if input("再试一次？直接回车重输，输入 q 退出：").strip().lower() != "":
                break
            code = input("验证码：").strip()
    print("登录未完成，原凭据没动。", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
