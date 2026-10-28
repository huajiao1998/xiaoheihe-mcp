"""根评论与楼中楼的有界完整读取，供 CLI、SDK 和正文定位共用。"""

import html
from html.parser import HTMLParser

from .errors import OperatorError, ProtocolError


async def read_thread(api, link_id, *, page_size=20, max_pages=100):
    if not 1 <= page_size <= 100 or not 1 <= max_pages <= 100:
        raise ValueError("评论分页范围无效。")
    groups, seen, offset = [], set(), 0
    first = None
    for page in range(max_pages):
        data = await api.request(
            "/bbs/app/link/tree",
            params={
                "link_id": link_id,
                "page": page + 1,
                "is_first": 1 if page == 0 else 0,
                "index": 1,
                "owner_only": 0,
                "offset": offset,
                "limit": page_size,
                "sort_filter": "oldest",
            },
        )
        if first is None:
            first = data
        result = data.get("result") or {}
        page_groups = result.get("comments") or []
        if not page_groups:
            # [本地补丁 2026-10-26] 平台偶尔返回空页却仍标 has_more_floors=True。
            # 原来这里直接抛 ProtocolError，会让「按正文定位回复」整条路失败。
            # 降级：当作到底了，把已经拿到的返回出去。
            if result.get("has_more_floors"):
                break
            break
        for group in page_groups:
            items = group.get("comment") or []
            if not items:
                continue
            root = str(items[0].get("commentid", ""))
            if not root:
                continue
            if root in seen:
                # [本地补丁 2026-10-27] 平台翻页会重复吐回已读楼层（offset 按楼层数推进，
                # 但接口分页并不严格对齐），原来这里直接抛 ProtocolError，让「按正文定位
                # 回复」整条路失败。降级：跳过重复楼层，继续翻页。
                continue
            seen.add(root)
            if int(items[0].get("child_num") or 0) > len(items) - 1:
                cursor = str(items[-1].get("commentid") or "")
                cursors = {cursor} if cursor else set()
                child_ids = {str(item.get("commentid")) for item in items[1:]}
                for reply_page in range(max_pages):
                    replies = (
                        await api.request(
                            "/bbs/app/comment/sub/comments",
                            params={
                                "link_id": link_id,
                                "root_comment_id": root,
                                "limit": page_size,
                                **({"lastval": cursor} if cursor else {}),
                            },
                        )
                    ).get("result") or {}
                    for child in replies.get("comments") or []:
                        child_id = str(child.get("commentid"))
                        if child_id not in child_ids:
                            items.append(child)
                            child_ids.add(child_id)
                    if not replies.get("has_more"):
                        break
                    returned_comments = replies.get("comments") or []
                    cursor = str(
                        replies.get("lastval")
                        or (returned_comments[-1].get("commentid") if returned_comments else "")
                        or ""
                    )
                    if not cursor or cursor in cursors:
                        raise ProtocolError("楼中楼游标未推进，停止读取；结果不完整。")
                    cursors.add(cursor)
                else:
                    raise ProtocolError("楼中楼超过分页上限，结果不完整。")
                if len(items) - 1 < int(items[0].get("child_num") or 0):
                    # [本地补丁] 平台返回的 child_num 会把已删除/已折叠的回复也算进去，
                    # 实测有 child_num=2 但接口只回 1 条、has_more=False 的情况。
                    # 原来这里直接抛 ProtocolError，会让整页读取失败（连累按正文定位回复）。
                    # 降级：标记不完整，把拿到的返回出去。
                    group["_incomplete"] = True
            groups.append(group)
        offset += len(page_groups)
        if not result.get("has_more_floors"):
            first["result"]["comments"] = groups
            first["result"]["full_comments"] = True
            first["result"]["has_more_floors"] = False
            return first
    # [本地补丁 2026-10-26] 跑满分页上限也别抛错——帖子评论多的时候这条会把
    # 「按正文定位回复」整条路堵死。降级：返回已经拿到的，标不完整。
    first["result"]["comments"] = groups
    first["result"]["full_comments"] = False
    first["result"]["has_more_floors"] = True
    return first


class _Text(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)

    def handle_starttag(self, tag, attrs):
        if tag == "img":
            self.parts.append(dict(attrs).get("alt", ""))
        elif tag in ("br", "p", "div"):
            self.parts.append(" ")


def plain_text(value):
    parser = _Text()
    parser.feed(value)
    return " ".join(html.unescape("".join(parser.parts)).split())


def locate_comment(thread, target):
    """按正文找一条评论。

    先精确匹配；不行再退到包含匹配——消息中心（list_messages）给的正文是**截断过的**
    （末尾带「…」），拿它直接来定位必然对不上，以前只能让调用方自己先查一遍帖子详情。
    包含匹配只在**唯一命中**时才采用，多命中照样报错，不会乱回错人。
    """
    target = plain_text(target)
    if not target:
        raise ValueError("目标评论正文不能为空。")
    # 消息中心截断时会在末尾留省略号，比之前先去掉，免得整串永远匹配不上。
    cores = {target}
    stripped = target.rstrip("…...").strip()
    if stripped:
        cores.add(stripped)

    exact, fuzzy = [], []
    for group in (thread.get("result") or {}).get("comments") or []:
        items = group.get("comment") or []
        for item in items:
            text = plain_text(str(item.get("text") or ""))
            # [本地补丁 2026-10-28] 空正文的评论（纯图片、被折叠、已删除）不能让。
            # 下面那句 `text in core`，当 text 是空串时恒为 True——一条空评论就等于
            # 「任何目标都命中一次」，于是 matches 永远 ≥2，按正文定位整条路废掉。
            # 表现是「目标评论不存在或正文重复」，其实目标好好在那儿。
            if not text:
                continue
            entry = {
                "reply_id": str(item["commentid"]),
                "root_id": str(items[0]["commentid"]),
            }
            if text in cores:
                exact.append(entry)
            elif any(core and (core in text or text in core) for core in cores):
                fuzzy.append(entry)

    matches = exact or fuzzy
    if len(matches) != 1:
        raise OperatorError(
            "目标评论不存在或正文重复；请使用明确的 reply-id 和 root-id。"
            f"（精确命中 {len(exact)} 条、模糊命中 {len(fuzzy)} 条）",
            "target",
        )
    return matches[0]
