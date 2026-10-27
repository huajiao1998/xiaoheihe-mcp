#!/usr/bin/env bash
# 把这个包以 editable 方式装进 uv tool 环境。
#
# 什么时候用：
#   - 换机器 / 重装系统之后
#   - 手滑把 uv tool 环境删了（uv tool uninstall / rm -rf）
#   - uv 版本大升级把 tool 环境重建了
#   - `xiaoheihe-cli` 命令不见了
#
# 为什么不用「打补丁再恢复」那套：
#   这个目录里的代码就是本体。editable 装进去后，site-packages 里只有两个
#   __editable__*.pth 文件指向这里，没有代码副本。改这里的文件重启 MCP 就生效，
#   不需要重新安装，也不存在「覆盖」。
#
# 登录态在 ~/.config/xiaoheihe-cli/，跟这里无关，重装不会丢。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UV="${UV:-/root/.local/bin/uv}"

if [ ! -x "$UV" ]; then
    UV="$(command -v uv || true)"
fi
if [ -z "$UV" ] || [ ! -x "$UV" ]; then
    echo "✗ 找不到 uv（试过 /root/.local/bin/uv 和 PATH）" >&2
    exit 1
fi

echo "包目录：$HERE"
echo "uv：$UV"
echo

cd "$HERE"
"$UV" tool install --editable "$HERE" --force

# ---------------------------------------------------------------------------
# 设备令牌依赖：私信和 App 登录要跑官方 fp.min.js，那玩意儿要 DOM，
# 所以借 node + jsdom。没有 node 也不影响发帖/评论/关注。
# ---------------------------------------------------------------------------
echo
echo "=== js 依赖（设备令牌，只有私信/登录用得到）==="
if command -v node >/dev/null 2>&1; then
    echo "node：$(node --version)"
    if [ -d "$HERE/js/node_modules/jsdom" ]; then
        echo "✓ jsdom 已就位"
    else
        echo "装 jsdom（约 25M，走国内镜像）…"
        if (cd "$HERE/js" && npm install --no-audit --no-fund \
                --registry https://registry.npmmirror.com); then
            echo "✓ jsdom 装好了"
        else
            echo "⚠ npm install 失败，私信/App 登录暂不可用（发帖评论关注不受影响）" >&2
        fi
    fi
else
    echo "⚠ 没找到 node —— 私信和 App 登录用不了，其余功能正常"
    echo "  要用的话装一下 node（>=18），再重跑本脚本"
fi

echo
echo "=== 验证 ==="
/root/.local/bin/xiaoheihe-cli --version

echo
echo "=== 检查代码位置（应该指向本目录）==="
/root/.local/share/uv/tools/xiaoheihe-cli/bin/python - <<'PY'
import xhh_operator, xhh_operator.mcp_server as m
print("加载路径:", m.__file__)
assert "xhh-mcp" in m.__file__ or "AIlife" in m.__file__, "⚠ 装的不是本地目录，editable 没生效"
print("✓ editable 生效")
src = open(m.__file__, encoding="utf-8").read()
want = ("follow_user", "unfollow_user", "list_following", "list_followers",
        "list_private_messages", "send_private_message")
missing = [n for n in want if f'"{n}"' not in src]
if missing:
    raise SystemExit(f"✗ 缺工具: {missing}")
print("✓ 工具齐全（关注 4 个 + 私信 2 个）")

# 两套签名都得在
import importlib
for mod, fn in (("xhh_operator.app_sign", "hkeyencode"),
                ("xhh_operator.app_sign_native", "native_hkey"),
                ("xhh_operator.rsa_login", "encrypt")):
    m2 = importlib.import_module(mod)
    assert hasattr(m2, fn), f"{mod} 缺 {fn}"
print("✓ 签名/RSA 模块都在")
PY

echo
echo "=== 重启 MCP（dsh 会自动拉起）==="
ps aux | grep 'xiaoheihe-cli mcp' | grep -v grep | awk '{print $2}' | xargs -r kill 2>/dev/null || true
echo "已发 kill，等 10 秒左右新进程就起来"
echo
echo "=== 登录态 ==="
CONF="$HOME/.config/xiaoheihe-cli"
[ -f "$CONF/session.json" ]     && echo "✓ 网页凭据：发帖/评论/关注可用" \
                                || echo "✗ 没有网页凭据 —— 跑 xiaoheihe-cli login 扫码登录"
[ -f "$CONF/app-session.json" ] && echo "✓ App 凭据：私信/签到可用" \
                                || echo "✗ 没有 App 凭据 —— 跑 xiaoheihe-cli app-login 手机号登录"
echo
echo "凭据分工：发帖/评论/关注走网页 cookie，私信/签到走 App 凭据，两套独立。"
