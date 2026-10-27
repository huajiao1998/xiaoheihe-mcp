#!/usr/bin/env bash
# 把「关注」相关的补丁装回 xiaoheihe-cli。
#
# 为什么需要这个文件：
#   xhh_operator 是 pip/uv 装的包，装在 site-packages 里。
#   重装或升级 xiaoheihe-cli 会把 service.py / mcp_server.py 覆盖回原版，
#   我加的 follow_user / unfollow_user / list_following / list_followers 四个工具就没了。
#   跑一遍这个脚本就能装回去。
#
# 用法：bash tools/xhh-patches/restore.sh
# 装完记得重启 MCP 才会生效（脚本最后会问）。

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 自动找 xhh_operator 的安装目录（python 版本号会变，别写死）
TARGET="$(python3 - <<'PY'
import glob, os
hits = glob.glob("/root/.local/share/uv/tools/xiaoheihe-cli/lib/python*/site-packages/xhh_operator")
print(hits[0] if hits else "")
PY
)"

if [ -z "$TARGET" ] || [ ! -d "$TARGET" ]; then
    echo "✗ 没找到 xhh_operator 安装目录，xiaoheihe-cli 是不是没装？" >&2
    exit 1
fi

echo "目标目录：$TARGET"

STAMP="$(date +%Y%m%d-%H%M%S)"
for f in service.py mcp_server.py; do
    if [ ! -f "$HERE/$f.modified" ]; then
        echo "✗ 缺补丁文件 $HERE/$f.modified" >&2
        exit 1
    fi
    # 已有文件先备份，别把人家的改动直接盖掉
    [ -f "$TARGET/$f" ] && cp "$TARGET/$f" "$TARGET/$f.bak-$STAMP"
    cp "$HERE/$f.modified" "$TARGET/$f"
    echo "✓ $f 已装回"
done

# 语法自检
if python3 -c "
import ast, pathlib
for f in ('service.py', 'mcp_server.py'):
    ast.parse(pathlib.Path('$TARGET') / f and (pathlib.Path('$TARGET') / f).read_text())
" 2>/dev/null; then
    echo "✓ 语法检查通过"
else
    echo "✗ 语法检查失败，快看上面报错" >&2
    exit 1
fi

echo
echo "四个工具：follow_user / unfollow_user / list_following / list_followers"
echo
echo "要让 MCP 生效，需要重启它："
echo "  ps aux | grep 'xiaoheihe-cli mcp' | grep -v grep | awk '{print \$2}' | xargs -r kill"
echo "（dsh 会自动拉起新的，等 8 秒左右）"
