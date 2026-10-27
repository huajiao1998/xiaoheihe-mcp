"""小黑盒每日签到（App 通道）。

走 `/task/` 后端，要 App 凭据 + 完整 native 签名（含 _rnd）——这是跟私信最大的区别：
私信那套 7 位 hkey 在这儿不认，签到缺 `_rnd` 会回「非法请求」。

用法：
    xiaoheihe-cli sign            # 签到
    xiaoheihe-cli sign --status   # 只看状态，不签
"""
import json
import os
import random
import string
import sys
import time
import urllib.parse
import urllib.request

from .app_sign_native import sign_params

API = "https://api.xiaoheihe.cn"
APP_VERSION = "1.3.398"
APP_BUILD = "1142"
SESSION_PATH = os.path.expanduser("~/.config/xiaoheihe-cli/app-session.json")


def load_session(path=SESSION_PATH):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise RuntimeError("没有 App 凭据。先跑：xiaoheihe-cli app-login --phone 手机号")
    for key in ("heybox_id", "pkey", "x_xhh_tokenid", "device_id"):
        if not data.get(key):
            raise RuntimeError("App 凭据不完整（缺 %s），重跑 app-login" % key)
    return data


def _call(path, session, timeout=25):
    ts = int(time.time())
    nonce = "".join(random.choices(string.ascii_letters + string.digits, k=32))
    params = {
        "heybox_id": session["heybox_id"], "imei": session["device_id"],
        "device_info": "PKJ110", "os_type": "Android", "x_os_type": "Android",
        "x_client_type": "mobile", "os_version": "17",
        "version": APP_VERSION, "build": APP_BUILD, "dw": "360",
        "channel": "heybox", "x_app": "heybox", "time_zone": "Asia/Shanghai",
    }
    params.update(sign_params(path, ts, session["device_id"] + session["heybox_id"]))
    url = API + path + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, method="GET")
    req.add_header("User-Agent", "Max-Android")
    req.add_header("Accept", "application/json")
    req.add_header("Cookie", "pkey=%s; x_xhh_tokenid=%s"
                   % (session["pkey"], session["x_xhh_tokenid"]))
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def query_state(session=None):
    session = session or load_session()
    return _call("/task/sign_v4/get_sign_state", session)


def do_sign(session=None):
    session = session or load_session()
    return _call("/task/sign_v3/sign", session)


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="小黑盒每日签到（要 App 凭据）")
    ap.add_argument("--status", action="store_true", help="只看今天签没签，不执行签到")
    args = ap.parse_args(argv)

    try:
        session = load_session()
    except RuntimeError as exc:
        print("✗ %s" % exc, file=sys.stderr)
        return 2

    print("账号 %s" % session["heybox_id"])
    try:
        state = query_state(session)
    except Exception as exc:                       # noqa: BLE001
        print("✗ 查签到状态失败：%s" % exc, file=sys.stderr)
        return 1

    res = state.get("result") or {}
    signed = res.get("state") == 1 or res.get("signed")
    print("当前状态：%s" % ("今天已签到" if signed else "今天还没签"))

    if args.status:
        if res:
            print("  明细：%s" % json.dumps(res, ensure_ascii=False)[:200])
        return 0

    if signed:
        print("不用重复签。")
        return 0

    print("签到中…")
    try:
        out = do_sign(session)
    except Exception as exc:                       # noqa: BLE001
        print("✗ 签到失败：%s" % exc, file=sys.stderr)
        return 1

    print(out.get("msg") or json.dumps(out, ensure_ascii=False)[:200])
    return 0 if str(out.get("status")) == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
