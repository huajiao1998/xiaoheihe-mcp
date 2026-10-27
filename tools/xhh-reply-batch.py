#!/usr/bin/env python3
"""xhh-reply-batch.py — 一口气回一批小黑盒评论（自动排队 + 限速）

注意：MCP 里已经有 `reply_many` 工具了（一次最多 12 条，返回汇总），日常优先用它。
这个命令行版留着处理特别长的单子，或者 MCP 不可用的时候。

小黑盒没有批量接口，服务端一次只收一条。但没必要一条条手调：
把要回的 id 和话填进一个 JSON，这个脚本替你排队发。

用法:

    cd /tmp && PYTHONPATH=/root/.local/share/uv/tools/xiaoheihe-cli/lib/python3.13/site-packages \
        python3 /root/工作目录/AIlife/tools/xhh-reply-batch.py replies.json

replies.json:

    {
      "post_id": "195012420",
      "gap": 12,
      "items": [
        {"reply_id": "984140740", "root_id": "984140740", "text": "那就别查。"},
        {"reply_id": "984122558", "root_id": "984115428", "text": "什么算伟大，谁定？"}
      ]
    }

- 顶层评论 reply_id == root_id；回复楼中楼时 root_id 填它所在那一楼的根 id。
- 默认每条间隔 12 秒（小黑盒大约 10 秒一条、每小时 30 条上下）。
- 已经回过的不重复：脚本只按你给的单子发，去重得自己看。
"""

import asyncio
import json
import sys

from xhh_operator.service import Operator


async def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    cfg = json.load(open(sys.argv[1], encoding="utf-8"))
    post_id = str(cfg["post_id"])
    gap = float(cfg.get("gap", 12))
    items = cfg.get("items") or []
    if not items:
        sys.exit("没有要回的条目")

    ok = fail = 0
    async with Operator() as op:
        for i, it in enumerate(items, 1):
            text = str(it.get("text") or "").strip()
            if not text:
                print(f"[{i}/{len(items)}] 跳过空内容")
                continue
            try:
                await op.comment(
                    post_id,
                    text,
                    reply_id=str(it["reply_id"]),
                    root_id=str(it.get("root_id") or it["reply_id"]),
                )
                ok += 1
                print(f"[{i}/{len(items)}] 已发 → {it['reply_id']}  {text[:24]}")
            except Exception as e:  # noqa: BLE001
                fail += 1
                print(f"[{i}/{len(items)}] 失败 → {it['reply_id']}  {e}")
            if i < len(items):
                await asyncio.sleep(gap)

    print(f"完成：成功 {ok} 条，失败 {fail} 条")
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
