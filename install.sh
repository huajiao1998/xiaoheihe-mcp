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
missing = [n for n in ("follow_user", "unfollow_user", "list_following", "list_followers")
           if f'"{n}"' not in src]
print("✓ 四个关注工具都在" if not missing else f"✗ 缺工具: {missing}")
PY

echo
echo "=== 重启 MCP（dsh 会自动拉起）==="
ps aux | grep 'xiaoheihe-cli mcp' | grep -v grep | awk '{print $2}' | xargs -r kill 2>/dev/null || true
echo "已发 kill，等 10 秒左右新进程就起来"
echo
echo "登录态在 ~/.config/xiaoheihe-cli/，与本次安装无关。"
