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


def query_state(_session=None):
    """查今天签没签。

    走 `/task/list_v2/`（**网页 cookie + 网页签名**）——这个接口才真正反映任务状态。
    踩过的坑：`/task/sign_v4/get_sign_state` 名字像查状态，实际今天已签过再调会回
    「非法请求」，看着像签名坏了，其实只是重复签到被拒。别再拿它当状态查询用。
    """
    import asyncio
    from .service import Operator

    async def _run():
        async with Operator() as op:
            return await op.api.request("/task/list_v2/")

    data = asyncio.run(_run())
    if str(data.get("status")) != "ok":
        raise RuntimeError(data.get("msg") or "查任务列表失败（网页登录态可能过期）")
    for group in (data.get("result") or {}).get("task_list") or []:
        if group.get("title") != "每日任务":
            continue
        for task in group.get("tasks") or []:
            if task.get("title") == "签到":
                return {"signed": str(task.get("state")) == "finish", "task": task}
    return {"signed": False, "task": None}


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
        state = query_state()
    except Exception as exc:                       # noqa: BLE001
        print("✗ 查签到状态失败：%s" % exc, file=sys.stderr)
        return 1

    signed = state["signed"]
    print("当前状态：%s" % ("今天已签到 ✅" if signed else "今天还没签"))

    if args.status:
        if state.get("task"):
            print("  任务：%s" % json.dumps(state["task"], ensure_ascii=False)[:200])
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
