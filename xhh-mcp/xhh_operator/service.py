import asyncio
import json
import os
import random
import re
import string
import time
from decimal import Decimal, InvalidOperation

from .auth import verify
from .errors import AuthError, OperatorError, ProtocolError
from .http import API_BASE, HeyboxAPI
from .session import SessionStore
from .signing import workshop_params

import html as _html

_AT_RE = re.compile(r'<a[^>]*data-user-id="(\d+)"[^>]*>([^<]*)</a>')
_TAG_RE = re.compile(r'<[^>]+>')


def clean_rich_text(value):
    """把带 @ 的 HTML 正文清成阅读友好的纯文本：@名字（uid:123）。"""
    if not isinstance(value, str) or "<" not in value:
        return value

    def _at(m):
        name = _html.unescape(m.group(2)).strip().lstrip("@").strip()
        return "@%s（uid:%s）" % (name, m.group(1))

    value = _AT_RE.sub(_at, value)
    value = _TAG_RE.sub("", value)
    return _html.unescape(value).strip()


def clean_texts_deep(obj):
    """递归清洗所有名为 text 的字符串字段。"""
    if isinstance(obj, dict):
        return {
            k: clean_rich_text(v) if k == "text" else clean_texts_deep(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [clean_texts_deep(v) for v in obj]
    return obj



def fmt_time(ts):
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(int(ts)))
    except (TypeError, ValueError):
        return ""


def who_name(obj):
    """把 user 对象或评论对象格式化成「名字（uid:123）」。"""
    if not isinstance(obj, dict):
        return ""
    user = obj.get("user") if isinstance(obj.get("user"), dict) else obj
    name = clean_rich_text(user.get("username") or user.get("name") or "")
    uid = user.get("userid") or obj.get("userid") or ""
    if uid:
        return "%s（uid:%s）" % (name or "?", uid)
    return name or ""


def fmt_stats(view=None, like=None, comment=None):
    parts = []
    if view is not None:
        parts.append("%s 浏览" % view)
    if like is not None:
        parts.append("%s 赞" % like)
    if comment is not None:
        parts.append("%s 评论" % comment)
    return " · ".join(parts)


def slim_user(user):
    """用户只留 uid/名字/等级。"""
    if not isinstance(user, dict):
        return None
    return {
        "uid": user.get("userid"),
        "name": user.get("username") or user.get("name") or "",
        "level": (user.get("level_info") or {}).get("level"),
    }


def link_blocks(raw):
    """帖子正文（JSON 字符串）解析成块列表，文本里的 @ 转成「名字（uid:）」。"""
    if not raw:
        return []
    try:
        blocks = json.loads(raw) if isinstance(raw, str) else raw
    except (ValueError, TypeError):
        return [{"type": "text", "text": clean_rich_text(str(raw))}]
    out = []
    for b in blocks if isinstance(blocks, list) else []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            txt = clean_rich_text(b.get("text") or "")
            if txt:
                out.append({"type": "text", "text": txt})
        elif b.get("type") == "img":
            out.append({"type": "image", "url": b.get("url") or b.get("text") or ""})
    return out


def slim_post(link, *, summary_len=90):
    """帖子列表项：只留标题/摘要/作者/数据/分区。"""
    if not isinstance(link, dict):
        return None
    desc = clean_rich_text(link.get("description") or "") or ""
    return {
        "id": str(link.get("linkid") or link.get("link_id") or ""),
        "title": clean_rich_text(link.get("title") or ""),
        "summary": desc[:summary_len],
        "author": slim_user(link.get("user")),
        "stats": {
            "view": link.get("click"),
            "like": link.get("link_award_num"),
            "comment": link.get("comment_num"),
            "favorite": link.get("favour_count"),
        },
        "topics": [t.get("name") for t in (link.get("topics") or []) if isinstance(t, dict)],
        "created": link.get("create_at"),
        "has_video": bool(link.get("has_video")),
    }


def post_line(link):
    """帖子一览的一行：标题｜分区｜数据｜时间｜id。"""
    if not isinstance(link, dict):
        return ""
    title = (clean_rich_text(link.get("title") or "") or "").replace("\n", " ").strip()
    if not title:
        title = (clean_rich_text(link.get("description") or "") or "")[:36] or "（无标题）"
    zone = "/".join(
        t.get("name") for t in (link.get("topics") or []) if isinstance(t, dict) and t.get("name")
    )
    lid = link.get("linkid") or link.get("link_id")
    return "｜".join(
        part
        for part in (
            title,
            zone,
            fmt_stats(link.get("click"), link.get("link_award_num"), link.get("comment_num")),
            fmt_time(link.get("create_at")),
            "id %s" % lid if lid else "",
        )
        if part
    )


def identifier(value: str) -> str:
    value = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", value):
        raise ValueError("ID 只能包含字母、数字、下划线或短横线。")
    return value


def link_id(record) -> str | None:
    if not isinstance(record, dict):
        return None
    for key in ("link_id", "linkid"):
        if record.get(key):
            return str(record[key])
    for key in ("link", "comment", "data", "result"):
        if found := link_id(record.get(key)):
            return found
    return None


def review_record(value):
    if value in (None, {}, []):
        return None
    if not isinstance(value, dict):
        raise ProtocolError("本人评测的响应格式已变化。")
    if value.get("exists") is False:
        return None
    if link_id(value):
        return value
    raise ProtocolError("本人评测返回了非空数据，但缺少评测 ID。")


def find_field(record, names):
    if not isinstance(record, dict):
        return None
    for name in names:
        if record.get(name) is not None:
            return record[name]
    for key in ("link", "comment", "data"):
        if (value := find_field(record.get(key), names)) is not None:
            return value
    return None


def encode_text(content: str) -> str:
    if not content.strip():
        raise ValueError("正文不能为空。")
    return json.dumps(
        [{"type": "text", "text": content}], ensure_ascii=False, separators=(",", ":")
    )


def integer_score(value):
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("请指定 1 到 5 的整数评分。") from None
    if not number.is_finite() or number != number.to_integral_value() or not 1 <= number <= 5:
        raise ValueError("请指定 1 到 5 的整数评分。")
    return int(number)


def present_review(data):
    result = data.get("result") or {}
    record = review_record(result)
    if not record:
        return {"exists": False, "appid": result.get("appid"), "name": result.get("name")}
    serialized = find_field(record, ("text",))
    try:
        blocks = json.loads(serialized)
    except (ValueError, TypeError):
        raise ProtocolError("短评原始正文格式已变化。") from None
    if not isinstance(blocks, list):
        raise ProtocolError("短评原始正文不是文本块数组。")
    return {
        "exists": True,
        "appid": result.get("appid") or find_field(record, ("appid",)),
        "name": result.get("name"),
        "review_id": link_id(record),
        "personal_score": find_field(record, ("impression_score", "score", "user_score")),
        "text": "\n".join(
            block.get("text", "")
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        ),
        "blocks": blocks,
        "created_at": find_field(record, ("create_at",)),
        "modified_at": find_field(record, ("modify_at",)),
    }


class Operator:
    def __init__(
        self, store: SessionStore | None = None, *, api: HeyboxAPI | None = None, uploader=None
    ):
        self.store = store or SessionStore()
        self.session = self.store.load()
        self.api = api or HeyboxAPI(_preferred_cookies(self.session))
        self._verified = False
        self._uploader = uploader

    async def __aenter__(self):
        await self.api.__aenter__()
        return self

    async def __aexit__(self, *args):
        await self.api.__aexit__(*args)

    async def require_account(self):
        if not self.session:
            raise AuthError("尚未登录，请先执行 xiaoheihe login。")
        if not self._verified:
            await verify(self.api, self.session["user_id"])
            self._verified = True
        return self.session["user_id"]

    async def notifications(self, *, limit=20):
        """官方系统通知；评论和回复用 messages(kind="reply")，赞用 kind="award"。"""
        user = await self.require_account()
        data = await self.api.request(
            "/bbs/app/message/list", params={"userid": user, "offset": 0, "limit": limit}
        )
        result = data.get("result") or {}
        items = [n for n in (result.get("notifications") or []) if isinstance(n, dict)]
        lines = []
        for item in items:
            stamp = fmt_time(item.get("create_at"))
            body = clean_rich_text(item.get("text") or "").replace("\n", " ")
            lines.append(
                "｜".join(
                    p
                    for p in (
                        stamp,
                        clean_rich_text(item.get("title") or ""),
                        body[:120] + ("…" if len(body) > 120 else ""),
                    )
                    if p
                )
            )
        return {
            "条数": len(lines),
            "通知": lines,
            "说明": "这里只有官方系统通知；评论、回复、赞用 messages(kind=reply|award) 查。",
        }

    async def messages(self, *, kind="reply", limit=20, offset=0):
        """消息中心：kind=reply 评论与回复、award 赞、official 官方通知。一行一条。"""
        kinds = {"reply": 0, "award": 1, "official": 2}
        if kind not in kinds:
            raise ValueError("kind 只能是 reply / award / official。")
        user = await self.require_account()
        data = await self.api.request(
            "/bbs/app/user/message",
            params={
                "userid": user,
                "offset": offset,
                "limit": limit,
                "list_type": kinds[kind],
            },
        )
        result = data.get("result") or {}
        unread = result.get("message_unread_num") or {}
        items = [m for m in (result.get("messages") or []) if isinstance(m, dict)]
        lines = []
        for item in items:
            stamp = fmt_time(item.get("timestamp"))
            title = clean_rich_text(item.get("link_title") or "")
            linkid = item.get("linkid")
            if kind == "reply":
                who = (item.get("user_a") or {}).get("username") or "?"
                said = clean_rich_text(item.get("comment_a_text") or "").replace("\n", " ")
                mine = clean_rich_text(item.get("comment_b_text") or "").replace("\n", " ")
                # message_type=1 是回复我的某条评论；2 是直接评论我的帖子。
                action = "回复我的评论" if item.get("message_type") == 1 else "评论我的帖子"
                parts = [stamp, "%s %s：%s" % (who, action, said[:70] + ("…" if len(said) > 70 else ""))]
                if mine:
                    parts.append("我原话「%s」" % (mine[:28] + ("…" if len(mine) > 28 else "")))
                if title:
                    parts.append("《%s》%s" % (title, linkid or ""))
                lines.append("｜".join(p for p in parts if p))
            elif kind == "award":
                parts = [stamp]
                if title:
                    parts.append("《%s》%s" % (title, linkid or ""))
                parts.append("%s 赞" % (item.get("link_award_num") or 0))
                lines.append("｜".join(p for p in parts if p))
            else:
                # list_type=2 里混着几种东西，靠 entry / sub_entry 区分：
                #   entry=message     → 真正的私信，message_id 就是对方 uid
                #   sub_entry=heychat → 好友申请（App 里的 chat_request）
                #   其余              → 官方系统通知
                text = clean_rich_text(item.get("text") or "").replace("\n", " ")
                entry = item.get("entry") or ""
                sub = item.get("sub_entry") or ""
                sender = (item.get("user_a") or {}).get("username") or ""
                if entry == "message":
                    peer = item.get("message_id")
                    parts = [stamp, "私信", "%s：%s" % (sender or "?", text[:80] + ("…" if len(text) > 80 else ""))]
                    if peer:
                        parts.append("对方 uid %s" % peer)
                    lines.append("｜".join(p for p in parts if p))
                elif sub == "heychat":
                    who = text.split(" ")[0] if " " in text else ""
                    label = "好友申请" if "好友" in text else "好友互动"
                    parts = [stamp, label, text[:60]]
                    if who:
                        parts.append("来自 %s" % who)
                    lines.append("｜".join(p for p in parts if p))
                else:
                    lines.append(
                        "｜".join(
                            p
                            for p in (
                                stamp,
                                clean_rich_text(item.get("title") or ""),
                                text[:100] + ("…" if len(text) > 100 else ""),
                            )
                            if p
                        )
                    )
        return {
            "类型": {"reply": "评论与回复", "award": "赞与奖励", "official": "通知·好友·私信"}[kind],
            "未读": unread,
            "条数": len(lines),
            "消息": lines,
            "说明": "official 里 entry=message 的是私信（能看到内容，回不了——网页 API 没有发送接口）；sub_entry=heychat 的是好友申请。",
        }

    async def games(self, *, offset=0, limit=20, query=""):
        """本人游戏库：一行一款，只留时长和成就。"""
        user = await self.require_account()
        data = await self.api.request(
            "/account/game_list/",
            params={
                "userid": user,
                "offset": offset,
                "limit": limit,
                "sort": "time",
                **({"q": query} if query else {}),
            },
        )
        result = data.get("result") or {}
        items = [g for g in (result.get("game_list") or []) if isinstance(g, dict)]
        lines = []
        for game in items:
            parts = [game.get("name") or "?"]
            if game.get("show_playtime_forever"):
                parts.append("总时长 %s" % game["show_playtime_forever"])
            if game.get("playtime_2weeks"):
                parts.append("两周 %s" % (game.get("show_playtime_2weeks") or ""))
            if game.get("has_achievement"):
                parts.append(
                    "成就 %s/%s" % (game.get("achieved_count") or 0, game.get("achivement_count") or 0)
                )
            if game.get("appid"):
                parts.append("appid %s" % game["appid"])
            lines.append("｜".join(parts))
        total = result.get("total")
        view = {
            "账号": "uid %s" % user,
            "共": "%s 款" % (total if total is not None else len(lines)),
            "游戏": lines,
        }
        if not lines:
            view["说明"] = "这个号没有绑定 Steam 游戏，或库里还没有记录。"
        return view

    async def reviews(self, *, platform="steam", offset=0, limit=20, summary=False):
        user = await self.require_account()
        data = await self.api.request(
            "/account/my_comment_list",
            params={"userid": user, "platform": platform, "offset": offset, "limit": limit},
        )
        result = data.get("result") or {}
        games = result.get("game_list")
        if not isinstance(games, list):
            raise ProtocolError("个人评测列表缺少 game_list。")
        items = []
        for game in games:
            appid = str(game.get("appid") or game.get("steam_appid") or "")
            if not appid:
                raise ProtocolError("个人评测列表中的游戏缺少 ID。")
            if summary:
                # 'score' here is the community aggregate, not the account's rating.
                items.append(
                    {
                        "appid": appid,
                        "name": game.get("name"),
                        "personal_score": game.get("comment_score"),
                        "comment_created_at": game.get("comment_create_at"),
                    }
                )
            else:
                items.append(present_review(await self.review(appid)))
        if summary:
            lines = []
            for item in items:
                lines.append(
                    "｜".join(
                        p
                        for p in (
                            "《%s》" % (item.get("name") or "?"),
                            "我的评分 %s" % item["personal_score"]
                            if item.get("personal_score") is not None
                            else "",
                            fmt_time(item.get("comment_created_at")),
                            "appid %s" % item["appid"],
                        )
                        if p
                    )
                )
        else:
            lines = []
            for item in items:
                text = (item.get("text") or "").strip()
                head = "｜".join(
                    p
                    for p in (
                        "《%s》" % (item.get("name") or "?"),
                        "我的评分 %s" % item["personal_score"]
                        if item.get("personal_score") is not None
                        else "",
                        fmt_time(item.get("created_at")),
                        "appid %s" % item["appid"],
                    )
                    if p
                )
                lines.append(head + ("\n   " + text.replace("\n", "\n   ") if text else ""))
        view = {
            "平台": platform,
            "条数": len(lines),
            "评测": lines,
        }
        if result.get("has_more") or (games and len(games) >= limit):
            view["下一页"] = offset + len(games)
        return view

    async def review(self, appid):
        appid = identifier(appid)
        user = await self.require_account()
        data = await self.api.request("/bbs/app/link/game/comment/mine", params={"appid": appid})
        record = review_record(data.get("result"))
        if record:
            body = record.get("link") or record
            if not isinstance(body, dict):
                raise ProtocolError("本人评测数据格式已变化。")
            owner = body.get("userid") or (body.get("user") or {}).get("userid")
            for candidate in (record.get("appid"), body.get("appid")):
                if candidate is not None and str(candidate) != appid:
                    raise ProtocolError("本人评测的游戏 ID 与请求不一致。")
            if owner is not None and str(owner) != user:
                raise ProtocolError("本人评测作者与已登录账号不一致。")
            if owner is None or find_field(record, ("text",)) is None:
                review_id = link_id(record)
                full = ((await self.post(review_id, limit=1)).get("result") or {}).get("link") or {}
                if str(full.get("userid")) != user or link_id(full) != review_id:
                    raise ProtocolError("完整短评的作者或 ID 与本人记录不一致。")
                if full.get("appid") is not None and str(full["appid"]) != appid:
                    raise ProtocolError("完整短评的游戏 ID 与请求不一致。")
                if not isinstance(full.get("text"), str):
                    raise ProtocolError("完整短评缺少原始正文。")
                record["link"] = {**(record.get("link") or {}), **full}
        return data

    async def write_review(self, appid, *, score=None, content=None, edit=False, dry_run=False):
        appid = identifier(appid)
        if score is not None:
            score = integer_score(score)
        await self.require_account()
        old = review_record((await self.review(appid)).get("result"))
        old_id = link_id(old)
        if edit and not old_id:
            raise OperatorError("这款游戏没有本人的短评，请使用 review create。", "not_found")
        if not edit and old_id:
            raise OperatorError("这款游戏已经有本人的短评，请使用 review edit。", "conflict")
        if score is None:
            score = find_field(old, ("impression_score", "score", "user_score"))
        if content is None:
            text = find_field(old, ("text",))
            if not isinstance(text, str):
                raise ProtocolError("无法保留原正文，请同时指定 --text 或 --file。")
        else:
            text = encode_text(content)
        score = integer_score(score)
        form = {
            "appid": appid,
            "score": str(score),
            "text": text,
            "game_impression_post_type": "2",
            "link_tag": str(find_field(old, ("link_tag",)) or 3),
        }
        if edit:
            form.update({"edit": "1", "link_id": old_id})
        if dry_run:
            return {
                "模式": "预检（不会提交）",
                "动作": "修改短评" if edit else "新建短评",
                "游戏": "appid %s" % appid,
                "评分": score,
                "正文": "%d 字" % len(content or ""),
            }
        receipt = await self.api.request("/bbs/app/api/link/post", form=form, extra=workshop_params)
        result = {
            "accepted": True,
            "operation": "review.edit" if edit else "review.create",
            "link_id": link_id(receipt) or old_id,
        }
        try:
            record = review_record((await self.review(appid)).get("result"))
            score_read = find_field(record, ("impression_score", "score", "user_score"))
            text_read = find_field(record, ("text",))
            id_matches = bool(link_id(record)) and (
                not result["link_id"] or link_id(record) == result["link_id"]
            )
            try:
                same_text = json.loads(text_read) == json.loads(text)
            except (ValueError, TypeError):
                same_text = False
            try:
                same_score = integer_score(score_read) == score
            except ValueError:
                same_score = False
            result["verified"] = bool(id_matches and same_score and same_text)
            result["readback"] = record
        except OperatorError as exc:
            result.update({"verified": False, "readback_error": exc.code})
        view = {
            "动作": "修改短评" if edit else "新建短评",
            "结果": "已提交",
            "短评 ID": str(result["link_id"] or ""),
            "回读核对": "通过（评分和正文都对得上）"
            if result.get("verified")
            else "没对上，可能还在审核",
        }
        if result.get("readback_error"):
            view["核对失败"] = result["readback_error"]
        return view

    async def delete_review(self, appid, *, dry_run=False):
        record = review_record((await self.review(appid)).get("result"))
        current_id = link_id(record)
        if not current_id:
            raise OperatorError("这款游戏没有本人的短评。", "not_found")
        if dry_run:
            return {
                "模式": "预检（不会删）",
                "动作": "删除短评",
                "游戏": "appid %s" % appid,
                "短评 ID": current_id,
            }
        await self.api.request(
            "/bbs/app/link/delete", form={"link_id": current_id}, extra=workshop_params
        )
        result = {"accepted": True, "operation": "review.delete", "link_id": current_id}
        try:
            result["verified"] = review_record((await self.review(appid)).get("result")) is None
        except OperatorError as exc:
            result.update({"verified": False, "readback_error": exc.code})
        view = {
            "动作": "删除短评",
            "结果": "已删除",
            "短评 ID": current_id,
            "回读核对": "通过（已经查不到了）" if result.get("verified") else "还能查到，可能还没生效",
        }
        if result.get("readback_error"):
            view["核对失败"] = result["readback_error"]
        return view

    async def search(self, keyword, *, kind="post", offset=0, limit=20):
        if not keyword.strip():
            raise ValueError("搜索词不能为空。")
        if kind not in ("post", "user", "topic", "game"):
            raise ValueError("搜索类型无效。")
        if kind == "game":
            path, params = "/game/search/", {"q": keyword}
        elif kind == "topic":
            path, params = "/bbs/app/api/search/topic", {"q": keyword}
        else:
            path, params = (
                "/bbs/app/api/general/search/v1",
                {"q": keyword, "search_type": "link" if kind == "post" else "user"},
            )
        data = await self.api.request(path, params={**params, "offset": offset, "limit": limit})
        result = data.get("result") or {}
        if kind in ("post", "user"):
            want = "link" if kind == "post" else "user"
            items = [
                i.get("info")
                for i in (result.get("items") or [])
                if isinstance(i, dict)
                and i.get("type") == want
                and isinstance(i.get("info"), dict)
            ]
        elif kind == "topic":
            items = [i for i in (result.get("topics") or []) if isinstance(i, dict)]
        else:
            items = [i for i in (result.get("games") or []) if isinstance(i, dict)]
        lines = []
        for item in items:
            if kind == "post":
                lines.append(post_line(item))
            elif kind == "user":
                level = (item.get("level_info") or {}).get("level")
                lines.append(
                    "｜".join(
                        p
                        for p in (
                            who_name(item),
                            "Lv%s" % level if level else "",
                            clean_rich_text(item.get("rec_tag") or ""),
                        )
                        if p
                    )
                )
            elif kind == "topic":
                lines.append(
                    "｜".join(
                        p
                        for p in (
                            item.get("name") or "",
                            clean_rich_text(item.get("show_desc") or ""),
                            "topic_id %s" % item.get("topic_id") if item.get("topic_id") else "",
                        )
                        if p
                    )
                )
            else:
                lines.append(
                    "｜".join(
                        p
                        for p in (
                            item.get("name") or "",
                            "评分 %s" % item.get("score") if item.get("score") else "",
                            "%s 关注" % item.get("follow_num") if item.get("follow_num") else "",
                            item.get("complete_tags_str") or "",
                            "appid %s" % item.get("steam_appid") if item.get("steam_appid") else "",
                        )
                        if p
                    )
                )
        view = {
            "搜索": "%s（%s）" % (
                keyword,
                {"post": "帖子", "user": "用户", "topic": "话题", "game": "游戏"}[kind],
            ),
            "条数": len(lines),
            "结果": lines,
        }
        if kind == "user":
            view["提示"] = "看某人发了什么：user_posts(userId)"
        return view

    async def post(self, postid, *, offset=0, limit=20, sort="hot"):
        data = await self.api.request(
            "/bbs/app/link/tree",
            params={
                "link_id": identifier(postid),
                "page": offset // limit + 1,
                "is_first": 1 if offset == 0 else 0,
                "index": 1,
                "owner_only": 0,
                "offset": offset,
                "limit": limit,
                "sort_filter": sort,
            },
        )
        return clean_texts_deep(data)

    async def posts(self, *, offset=0, limit=20):
        user = await self.require_account()
        return await self.api.request(
            "/bbs/app/profile/user/link/list_v2",
            params={"userid": user, "offset": offset, "limit": limit},
        )

    async def user_posts(self, userid, *, offset=0, limit=10):
        """读任意用户主页：一行一条帖子的阅读列表。"""
        await self.require_account()
        data = await self.api.request(
            "/bbs/app/profile/user/link/list_v2",
            params={"userid": identifier(userid), "offset": offset, "limit": limit},
        )
        result = data.get("result") or {}
        links = result.get("post_links") or []
        user = result.get("user") or {}
        lines = []
        for l in links:
            if not isinstance(l, dict):
                continue
            head = clean_rich_text(l.get("title") or "") or (
                clean_rich_text(l.get("description") or "")[:36] or "（无标题）"
            )
            lines.append("%s｜%s｜%s｜id %s" % (
                head,
                fmt_stats(l.get("click"), l.get("link_award_num"), l.get("comment_num")),
                fmt_time(l.get("create_at")),
                l.get("linkid") or l.get("link_id") or "",
            ))
        return {
            "用户": "%s · %s 粉 · %s 赞 · %s 帖" % (
                who_name(user),
                user.get("fan_num") or 0,
                user.get("awarded_num") or 0,
                user.get("post_link_num") or len(links),
            ),
            "帖子": lines,
            "总页数": result.get("total_page"),
        }

    async def fetch_images(self, urls, tag, *, keep_days=7):
        """把图片下载到本地 /tmp/xhh-images/<tag>/，顺手清掉 keep_days 天前的旧图。"""
        import os
        import shutil

        root = os.path.join("/tmp", "xhh-images")
        os.makedirs(root, exist_ok=True)
        cutoff = time.time() - max(1, keep_days) * 86400
        for name in os.listdir(root):
            path = os.path.join(root, name)
            try:
                if os.path.isdir(path) and os.path.getmtime(path) < cutoff:
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

        safe = re.sub(r"[^\w-]", "", str(tag)) or "post"
        base = os.path.join(root, safe)
        os.makedirs(base, exist_ok=True)
        out = []
        for i, url in enumerate(urls[:12]):
            if not url:
                continue
            try:
                resp = await self.api.client.get(url)
                resp.raise_for_status()
                ctype = (resp.headers.get("content-type") or "").lower()
                ext = ".webp" if "webp" in ctype else ".png" if "png" in ctype else ".jpg"
                path = os.path.join(base, "img%02d%s" % (i + 1, ext))
                with open(path, "wb") as f:
                    f.write(resp.content)
                out.append({"url": url, "path": path})
            except Exception:  # noqa: BLE001 - 单张失败不影响整体
                out.append({"url": url, "path": None})
        return out

    async def clean_cache(self, *, days=7, dry_run=True):
        """清理本地图片缓存 /tmp/xhh-images 里超过 N 天的目录。"""
        import os
        import shutil

        root = os.path.join("/tmp", "xhh-images")
        if not os.path.isdir(root):
            return {"缓存目录": root, "删除": [], "说明": "目录不存在"}
        cutoff = time.time() - max(0, days) * 86400
        removed = []
        kept = 0
        for name in os.listdir(root):
            path = os.path.join(root, name)
            if not os.path.isdir(path):
                continue
            size = sum(
                os.path.getsize(os.path.join(dp, f)) for dp, _, fs in os.walk(path) for f in fs
            )
            if os.path.getmtime(path) < cutoff:
                if not dry_run:
                    shutil.rmtree(path, ignore_errors=True)
                removed.append("%s（%d KB）" % (name, size // 1024))
            else:
                kept += 1
        return {
            "缓存目录": root,
            "阈值天数": days,
            "试运行": bool(dry_run),
            "删除": removed,
            "保留": kept,
        }

    async def post_view(
        self, postid, *, page=1, limit=10, full=False, download_images=False, sort="hot"
    ):
        """帖子：一行一行的阅读视图（中文键、评论带缩进和回复对象）。"""
        if page < 1 or not 1 <= limit <= 50:
            raise ValueError("分页参数无效。")
        if sort not in ("hot", "latest"):
            raise ValueError("排序只能是 hot 或 latest。")
        data = await self.api.request(
            "/bbs/app/link/tree",
            params={
                "link_id": identifier(postid),
                "page": page,
                "is_first": 1 if page == 1 else 0,
                "index": 1,
                "owner_only": 0,
                "offset": (page - 1) * limit,
                "limit": limit,
                "sort_filter": sort,
            },
        )
        if full:
            return clean_texts_deep(data)
        result = data.get("result") or {}
        link = result.get("link") or {}
        floors = result.get("comments") or []
        index = {}
        for group in floors:
            for c in group.get("comment") or []:
                index[str(c.get("commentid"))] = c

        comments = []
        for group in floors:
            items = group.get("comment") or []
            if not items:
                continue
            root = items[0]
            comments.append("%s%s：%s" % (
                "[置顶] " if root.get("is_top") else "",
                who_name(root),
                clean_rich_text(root.get("text") or ""),
            ))
            replies = items[1:]
            for c in replies[:5]:
                target = index.get(str(c.get("replyid") or ""))
                body = clean_rich_text(c.get("text") or "")
                arrow = " → %s" % who_name(target) if target else ""
                comments.append("  └ %s%s：%s" % (who_name(c), arrow, body))
            if len(replies) > 5:
                comments.append("  └ …还有 %d 条回复" % (len(replies) - 5))

        blocks = link_blocks(link.get("text"))
        texts = [b.get("text") or "" for b in blocks if b.get("type") == "text"]
        images = [b.get("url") or "" for b in blocks if b.get("type") == "image"]

        view = {}
        if page == 1:
            view.update({
                "标题": clean_rich_text(link.get("title") or ""),
                "作者": who_name(link),
                "数据": fmt_stats(link.get("click"), link.get("link_award_num"), link.get("comment_num")),
                "分区": "、".join(
                    t.get("name") or "" for t in (link.get("topics") or []) if isinstance(t, dict)
                ),
                "时间": fmt_time(link.get("create_at")),
                "正文": "\n\n".join(x for x in texts if x),
                "图片": [
                    u for u in images if u
                ],
            })
            if download_images and view.get("图片"):
                view["图片"] = await self.fetch_images(
                    view["图片"], str(link.get("linkid") or postid)
                )
        view["评论"] = comments
        view["下一页"] = page + 1 if len(floors) >= limit else None
        return view

    async def edit_info(self, postid):
        await self.require_account()
        return await self.api.request(
            "/bbs/app/link/edit/info", params={"link_id": identifier(postid)}
        )

    def uploader(self):
        from .media import MediaUploader

        if self._uploader is None:
            self._uploader = MediaUploader(self.api)
        return self._uploader

    async def upload_media(self, path, *, kind="image", dry_run=False):
        from .media import inspect_image, inspect_video

        if kind not in ("image", "video"):
            raise ValueError("媒体类型无效。")
        media = inspect_image(path) if kind == "image" else await inspect_video(path)
        await self.require_account()
        if dry_run:
            return {"dry_run": True, "operation": "media.upload", "media": media.preview()}
        return await self.uploader().upload(media)

    async def feed(self, *, offset=0, limit=20):
        """首页推荐：一行一条，不再吐原始 JSON。"""
        data = await self.api.request(
            "/bbs/app/feeds", params={"offset": offset, "limit": limit, "pull": "0", "dw": 604}
        )
        result = data.get("result") or {}
        links = [l for l in (result.get("links") or []) if isinstance(l, dict)]
        return {
            "首页": "%d 条（platform 实际返回，offset 起点 %d）" % (len(links), offset),
            "帖子": [post_line(l) for l in links],
            "下一页": offset + len(links),
        }

    async def profile(self, *, limit=10):
        """本人主页：一行一条帖子。"""
        user = await self.require_account()
        result = (await self.posts(limit=limit)).get("result") or {}
        profile = result.get("user")
        if not isinstance(profile, dict) or str(profile.get("userid")) != user:
            raise ProtocolError("本人主页响应缺少匹配的身份。")
        level = (profile.get("level_info") or {}).get("level")
        posts = [l for l in (result.get("post_links") or []) if isinstance(l, dict)]
        head = "%s" % who_name(profile)
        if level:
            head += " · Lv%s" % level
        head += " · %s 粉 · %s 赞 · %s 帖" % (
            profile.get("fan_num") or 0,
            profile.get("awarded_num") or 0,
            profile.get("post_link_num") or len(posts),
        )
        return {"用户": head, "帖子": [post_line(l) for l in posts], "总页数": result.get("total_page")}

    async def write_post(
        self,
        title,
        content,
        *,
        tag=11,
        draft=True,
        postid=None,
        dry_run=False,
        readback_attempts=4,
        images=(),
        communities=(),
        hashtags=(),
        post_kind="article",
        base_dir=None,
    ):
        from .article import prepare_article
        from .media import inspect_image
        from .selections import resolve_selections, validate_selections

        if not isinstance(readback_attempts, int) or not 1 <= readback_attempts <= 4:
            raise ValueError("读回次数需要为 1 到 4。")
        if (
            post_kind not in ("article", "image_text")
            or not title.strip()
            or not (content.strip() or images)
        ):
            raise ValueError("发布类型、标题或正文无效。")
        validate_selections(communities, hashtags)
        template = (
            prepare_article(content, images, base_dir=base_dir) if post_kind == "article" else None
        )
        media = template.images if template else [inspect_image(path) for path in images]
        await self.require_account()
        if postid:
            postid = identifier(postid)
            await self.require_own_post(postid)
        selection = await resolve_selections(self.api, communities, hashtags)
        if post_kind == "image_text" and not selection["topic_ids"]:
            selection["topic_ids"] = "58144"
        urls = [f"pending://image/{i}" for i in range(len(media))]
        if not dry_run:
            urls = [(await self.uploader().upload(item))["url"] for item in media]
        if template:
            blocks = [{"type": "html", "text": template.render(urls)}] + [
                {"type": "img", "url": url, "width": item.width, "height": item.height}
                for item, url in zip(media, urls)
            ]
        else:
            blocks = ([{"type": "text", "text": content}] if content else []) + [
                {"type": "img", "url": url, "width": item.width, "height": item.height}
                for item, url in zip(media, urls)
            ]
        form = {
            "title": title,
            "text": json.dumps(blocks, ensure_ascii=False),
            "post_type": "1",
            "link_tag": str(
                (21 if selection["topic_ids"] == "58144" else tag)
                if post_kind == "article"
                else (28 if selection["topic_ids"] == "58144" else 27)
            ),
            "draft": "1" if draft else "0",
            "view_limit": "1",
            "original": "1",
            "words_count": str(len(content)),
        }
        if selection["topic_ids"]:
            form["topic_ids"] = selection["topic_ids"]
        if selection["hashtags"]:
            form["hashtags"] = json.dumps(selection["hashtags"], ensure_ascii=False)
        if postid:
            form.update({"edit": "1", "link_id": postid})
        if dry_run:
            result = {
                "dry_run": True,
                "operation": "post.edit" if postid else "post.create",
                "form": form,
                "uploads": [item.preview() for item in media],
            }
            if not selection["topic_ids"]:
                result["warning"] = (
                    "未指定分区：发帖必须传 communities（社区名，如 [\"AI\"]），"
                    "否则小黑盒会以「请添加分区」拒绝。"
                )
            return result
        return await self._submit_post(
            form,
            blocks,
            draft=draft,
            postid=postid,
            readback_attempts=readback_attempts,
            verify_topics=bool(communities),
        )

    async def post_publish(
        self, title, content, *, images=(), communities=(), tags=(), mode="preview", kind="image_text"
    ):
        """发图文或长文的简化入口：mode=preview/draft/publish，返回人话结果。"""
        if mode not in ("preview", "draft", "publish"):
            raise ValueError("mode 只能是 preview / draft / publish。")
        dry_run = mode == "preview"
        draft = mode != "publish"
        result = await self.write_post(
            title,
            content,
            post_kind=kind,
            images=list(images or ()),
            communities=list(communities or ()),
            hashtags=list(tags or ()),
            draft=draft,
            dry_run=dry_run,
        )
        zone = "、".join(communities or ()) or ""
        base = {
            "分区": zone or "（未指定）",
            "标题": title,
            "正文": "%d 字" % len(content or ""),
            "图片": "%d 张" % len(list(images or ())),
        }
        if dry_run:
            base["模式"] = "预览（不会真发）"
            base["下一步"] = "确认后传 mode=draft 存草稿，或 mode=publish 正式发布"
            if not zone:
                base["警告"] = "没传分区（communities），正式发布会小黑盒被拒：请传例如 [\"AI\"]。"
            return base
        link_id = result.get("link_id") if isinstance(result, dict) else None
        link = (((result.get("readback") or {}).get("result") or {}).get("link") or {})
        base["结果"] = "已存草稿" if result.get("is_draft") else "已发布"
        base["id"] = str(link_id or link.get("linkid") or "")
        base["链接"] = link.get("share_url") or ""
        return base

    async def write_video(
        self,
        title,
        path,
        *,
        content="",
        cover=None,
        draft=False,
        communities=(),
        hashtags=(),
        dry_run=False,
    ):
        from .media import inspect_image, inspect_video
        from .selections import resolve_selections, validate_selections

        if not title.strip():
            raise ValueError("视频标题不能为空。")
        if draft and not dry_run:
            raise ValueError("小黑盒不支持视频草稿；请先预览，再明确正式发布。")
        validate_selections(communities, hashtags)
        video = await inspect_video(path)
        image = inspect_image(cover) if cover else None
        await self.require_account()
        selection = await resolve_selections(self.api, communities, hashtags)
        media = (
            {"url": "pending://video", "thumb": None}
            if dry_run
            else await self.uploader().upload(video)
        )
        thumbnail = "pending://cover" if image else media.get("thumb")
        if image and not dry_run:
            thumbnail = (await self.uploader().upload(image))["url"]
        if not thumbnail and not dry_run:
            raise ProtocolError("视频上传没有返回封面，请显式提供封面图片。")
        seconds = max(1, round(video.duration))
        duration = f"{seconds // 60}:{seconds % 60:02d}"
        blocks = [{"type": "text", "text": content}]
        form = {
            "title": title,
            "text": content,
            "desc": content,
            "link_tag": "1",
            "post_type": "4",
            "original": "1",
            "view_limit": "1",
            "draft": "1" if draft else "0",
            "video_url": media["url"],
            "video_thumb": thumbnail or "pending://automatic-cover",
            "video_duration": duration,
            "video_duration_desc": duration,
            "video_cos_provider": "tencent",
        }
        if selection["topic_ids"]:
            form["topic_ids"] = selection["topic_ids"]
        if selection["hashtags"]:
            form["hashtags"] = json.dumps(selection["hashtags"], ensure_ascii=False)
        if dry_run:
            result = {
                "分区": "、".join(communities or ()) or "（未指定）",
                "标题": title,
                "正文": "%d 字" % len(content or ""),
                "视频": "%s 秒" % seconds,
                "封面": "有" if image else "自动截帧",
                "模式": "预览（不会真发，也不会传文件）",
                "下一步": "确认后传 mode=publish 正式发布（视频不支持草稿）",
            }
            if not selection["topic_ids"]:
                result["警告"] = "没传分区（communities），正式发布会小黑盒被拒：请传例如 [\"AI\"]。"
            return result
        return await self._submit_post(
            form, blocks, draft=draft, endpoint="/bbs/app/api/video-link/post"
        )

    async def _submit_post(
        self,
        form,
        blocks,
        *,
        draft,
        postid=None,
        readback_attempts=4,
        endpoint="/bbs/app/api/link/post",
        verify_topics=True,
    ):
        from .article import semantic_html
        from .media import same_asset

        title = form["title"]
        receipt = await self.api.request(endpoint, form=form, extra=workshop_params)
        result = {
            "accepted": True,
            "is_draft": draft,
            "link_id": link_id(receipt) or postid,
            "verified": False,
        }
        if result["link_id"]:
            try:
                for attempt in range(readback_attempts):
                    if attempt:
                        await asyncio.sleep(2**attempt)
                    record = (
                        await self.edit_info(result["link_id"])
                        if draft
                        else await self.post(result["link_id"])
                    )
                    returned = record.get("result") or {}
                    returned_link = returned.get("link") or {}
                    try:
                        returned_blocks = json.loads(returned_link.get("text", ""))
                        expected_body = [
                            dict(block) for block in blocks if block.get("type") != "img"
                        ]
                        actual_body = [
                            dict(block) for block in returned_blocks if block.get("type") != "img"
                        ]
                        expected_images = [
                            block["url"] for block in blocks if block.get("type") == "img"
                        ]
                        actual_images = [
                            block.get("url")
                            for block in returned_blocks
                            if block.get("type") == "img"
                        ]
                        actual_images.extend(
                            image.get("url")
                            for image in (returned.get("images") or [])
                            if isinstance(image, dict)
                        )
                        for values in (actual_body, expected_body):
                            for block in values:
                                if block.get("type") == "html":
                                    block["text"] = semantic_html(block["text"], expected_images)
                        same_blocks = actual_body == expected_body and all(
                            any(same_asset(expected, actual) for actual in actual_images)
                            for expected in expected_images
                        )
                        if form.get("video_url"):
                            same_blocks = bool(
                                actual_body and actual_body[0].get("text") == form["text"]
                            )
                    except (ValueError, TypeError):
                        same_blocks = bool(
                            form.get("video_url") and returned_link.get("text") == form["text"]
                        )
                    result["verified"] = bool(
                        same_blocks
                        and returned_link.get("title") == title
                        and link_id(returned_link) == str(result["link_id"])
                        and str(returned_link.get("userid")) == self.session["user_id"]
                    )
                    if result["verified"] and form.get("topic_ids") and verify_topics:
                        implicit_dynamic = form["topic_ids"] == "58144" and str(
                            returned_link.get("link_tag")
                        ) in ("28", "21")
                        result["verified"] = implicit_dynamic or set(
                            form["topic_ids"].split(",")
                        ).issubset(
                            {
                                str(t.get("topic_id"))
                                for t in (returned_link.get("topics") or [])
                                if isinstance(t, dict)
                            }
                        )
                    if result["verified"] and form.get("hashtags"):
                        result["verified"] = set(json.loads(form["hashtags"])).issubset(
                            {
                                t.get("name") if isinstance(t, dict) else t
                                for t in (returned_link.get("hashtags") or [])
                            }
                        )
                    if result["verified"] and form.get("video_url"):
                        info = returned_link.get("video_info") or {}
                        result["verified"] = (
                            returned_link.get("video_url") or info.get("url")
                        ) == form["video_url"] and (
                            returned_link.get("video_thumb") or info.get("thumb")
                        ) == form["video_thumb"]
                    result["readback"] = record
                    if result["verified"]:
                        break
            except OperatorError as exc:
                result["readback_error"] = exc.code
        return result

    async def require_own_post(self, postid):
        user = await self.require_account()
        result = (await self.edit_info(postid)).get("result") or {}
        record = result.get("link") or result
        author = record.get("user") or record.get("author") or {}
        owner = author.get("userid") or author.get("heybox_id") or record.get("userid")
        if str(owner) != user:
            raise OperatorError(
                "只能修改或删除当前账号自己的帖子；无法确认作者时停止操作。", "ownership"
            )

    async def delete_post(self, postid, *, dry_run=False, draft=False):
        postid = identifier(postid)
        await self.require_own_post(postid)
        if dry_run:
            return {"dry_run": True, "operation": "post.delete", "link_id": postid}
        await self.api.request(
            "/bbs/app/link/delete",
            params={"draft": "1"} if draft else None,
            form={"link_id": postid},
            extra=workshop_params,
        )
        return {"accepted": True, "operation": "post.delete", "link_id": postid, "verified": False}

    async def comment(
        self,
        postid,
        content,
        *,
        reply_id="-1",
        root_id="-1",
        target_text=None,
        images=(),
        dry_run=False,
        max_pages=100,
    ):
        from .media import inspect_image, same_asset

        media = [inspect_image(path) for path in images]
        await self.require_account()
        if not content.strip() and not media:
            raise ValueError("评论不能为空。")
        if target_text is not None:
            if reply_id != "-1" or root_id != "-1":
                raise ValueError("按正文定位和显式评论 ID 不能同时使用。")
            from .comments import locate_comment, read_thread

            target = locate_comment(
                await read_thread(self.api, identifier(postid), max_pages=max_pages), target_text
            )
            reply_id, root_id = target["reply_id"], target["root_id"]
        elif (reply_id == "-1") != (root_id == "-1"):
            raise ValueError("回复需要同时提供 reply-id 和 root-id。")
        form = {
            "link_id": identifier(postid),
            "text": content,
            "root_id": identifier(root_id),
            "reply_id": identifier(reply_id),
            "is_cy": "0",
        }
        if media:
            urls = (
                [f"pending://image/{i}" for i in range(len(media))]
                if dry_run
                else [(await self.uploader().upload(item))["url"] for item in media]
            )
            form["imgs"] = ";".join(urls)
        if dry_run:
            view = {
                "模式": "预检（不会发出）",
                "动作": "回复评论" if reply_id != "-1" else "发表评论",
                "帖子": str(postid),
                "内容": "%d 字" % len(content or ""),
            }
            if target_text is not None:
                view["定位评论"] = target_text[:40]
            if reply_id != "-1":
                view["回复位置"] = "楼 %s 下的 reply_id %s" % (root_id, reply_id)
            if media:
                view["图片"] = "%d 张" % len(media)
                view["上传预览"] = [item.preview() for item in media]
            return view
        receipt = await self.api.request(
            "/bbs/app/comment/create", form=form, extra=workshop_params
        )
        result = {
            "accepted": True,
            "comment_id": receipt.get("commentid")
            or (receipt.get("result") or {}).get("commentid"),
            "result": receipt.get("result"),
            "verified": False,
        }
        if result["comment_id"]:
            try:
                if reply_id == "-1":
                    readback = await self.post(postid, sort="latest")
                else:
                    readback = await self.api.request(
                        "/bbs/app/comment/sub/comments",
                        params={"link_id": postid, "root_comment_id": root_id, "limit": 20},
                    )

                def matches(value):
                    if isinstance(value, dict):
                        if str(value.get("commentid")) == str(result["comment_id"]):
                            images = value.get("imgs") or value.get("images") or []
                            if isinstance(images, str):
                                returned_urls = set(images.split(";"))
                            else:
                                returned_urls = {
                                    item.get("url") if isinstance(item, dict) else item
                                    for item in images
                                }
                            return (
                                value.get("text") == content
                                and str(value.get("userid")) == self.session["user_id"]
                                and (
                                    not form.get("imgs")
                                    or all(
                                        any(
                                            same_asset(expected, actual) for actual in returned_urls
                                        )
                                        for expected in form["imgs"].split(";")
                                    )
                                )
                            )
                        return any(matches(v) for v in value.values())
                    if isinstance(value, list):
                        return any(matches(v) for v in value)
                    return False

                result["verified"] = matches(readback)
            except OperatorError as exc:
                result["readback_error"] = exc.code
        view = {
            "动作": "回复评论" if reply_id != "-1" else "发表评论",
            "结果": "已发出" if result["comment_id"] else "已提交，但平台没返回评论 ID",
            "评论 ID": str(result["comment_id"] or ""),
            "回读核对": (
                "通过（帖子里能看到这条）"
                if result["verified"]
                else "没在页面上找到，可能还在审核"
            ),
        }
        if reply_id != "-1":
            view["回复位置"] = "楼 %s 下的 reply_id %s" % (root_id, reply_id)
        if result.get("readback_error"):
            view["核对失败"] = result["readback_error"]
        return view

    # ------------------------------------------------------------------
    # 关注（2026-10-27 补）
    # 小黑盒的「好友」在网页端没有接口，私信也发不出去，关注是唯一真能加的。
    # ------------------------------------------------------------------

    async def follow_user(self, peer, *, dry_run=False):
        """关注一个用户。peer 是对方的 heybox uid。"""
        peer = identifier(peer)
        user = await self.require_account()
        if str(peer) == str(user):
            raise ValueError("不能关注自己。")
        if dry_run:
            return {"dry_run": True, "operation": "user.follow", "peer": peer}
        await self.api.request(
            "/bbs/app/profile/follow/user",
            form={"following_id": peer},
        )
        return {"accepted": True, "operation": "user.follow", "peer": peer, "verified": False}

    async def unfollow_user(self, peer, *, dry_run=False):
        """取消关注。"""
        peer = identifier(peer)
        user = await self.require_account()
        if dry_run:
            return {"dry_run": True, "operation": "user.unfollow", "peer": peer}
        await self.api.request(
            "/bbs/app/profile/follow/user/cancel",
            form={"following_id": peer},
        )
        return {"accepted": True, "operation": "user.unfollow", "peer": peer, "verified": False}

    async def following(self, *, offset=0, limit=20, peer=None):
        """我关注的人；给了 peer 就查那个人关注了谁。"""
        user = await self.require_account()
        target = identifier(peer) if peer else user
        data = await self.api.request(
            "/bbs/app/profile/following/list",
            params={"userid": target, "offset": offset, "limit": limit, "owner": user},
        )
        return self._follow_lines(data)

    async def followers(self, *, offset=0, limit=20, peer=None):
        """关注我的人。"""
        user = await self.require_account()
        target = identifier(peer) if peer else user
        data = await self.api.request(
            "/bbs/app/profile/follower/list",
            params={"userid": target, "offset": offset, "limit": limit, "owner": user},
        )
        return self._follow_lines(data)

    @staticmethod
    def _follow_lines(data):
        # 注意：http 层已经把 result 解包了，字段在顶层；兼容两种形态。
        result = data if ("follow_list" in data or "follow_cnt" in data) else (data.get("result") or {})
        users = result.get("follow_list") or result.get("users") or result.get("list") or []
        lines = []
        for u in users:
            if not isinstance(u, dict):
                continue
            uid = u.get("userid") or u.get("heybox_id") or ""
            name = u.get("username") or u.get("nickname") or "?"
            tail = "｜uid %s" % uid if uid else ""
            lines.append("%s%s" % (name, tail))
        cnt = result.get("follow_cnt") or {}
        out = {"条数": len(lines), "用户": lines}
        if isinstance(cnt, dict) and cnt:
            out["关系"] = "关注 %s｜粉丝 %s｜互关 %s" % (
                cnt.get("follow_num", "?"), cnt.get("fan_num", "?"), cnt.get("inter_follow_num", "?"))
        if not lines and not cnt:
            out["说明"] = "接口返回空——可能没关注过人，也可能被限流了，过一会儿再查"
        return out

    # ------------------------------------------------------------------
    # [本地补丁 2026-10-27] 私信读取
    # 读的接口网页 cookie 就能用；发的接口要 App 端登录态，暂时做不到。
    # 接口与参数来自 chr233/Xiaoheihe_CSharp 的 ChatAPI.cs（C# 实现）。
    # ------------------------------------------------------------------

    async def private_messages(self, peer, *, limit=20):
        """读和某个人的私信会话（只读，网页 cookie 可用）。"""
        peer = identifier(peer)
        data = await self.api.request("/chat/get_message_list/", params={"userid": peer})
        # /chat/ 这组接口回的是完整响应（status/result），跟 /bbs/app/ 不一样
        result = data.get("result") if isinstance(data.get("result"), dict) else data

        who = result.get("recipient") or {}
        name = who.get("username") or "?"
        uid = who.get("userid") or peer
        msgs = result.get("list") or []

        lines = []
        for m in msgs:
            ts = m.get("create_time") or 0
            try:
                stamp = __import__("datetime").datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d %H:%M")
            except Exception:
                stamp = "?"
            body = str(m.get("markdown") or m.get("text") or "").replace("\n", " ").strip()
            # is_user: 1 = 我发的，0 = 对方发的。
            # 平台把「加好友 / 互关」这类提示也算成 1，靠文案认出来标成系统。
            if any(h in body for h in ("已经是好友", "已互相关注", "开始聊天吧", "以上是打招呼")):
                side = "系统"
            else:
                side = "我" if str(m.get("is_user")) == "1" else name
            if len(body) > 100:
                body = body[:100] + "…"
            lines.append("%s｜%s｜%s" % (stamp, side, body))

        if limit and len(lines) > limit:
            lines = lines[-limit:]

        return {
            "对象": "%s（uid %s）" % (name, uid),
            "条数": len(lines),
            "对话": lines,
            "说明": "走 App 凭据（app-session.json）。发新的用 send_private_message。",
        }

    # ------------------------------------------------------------------
    # [本地补丁 2026-10-27] 私信发送（App 通道）
    # 关键：必须是 App 1.3.218 那套参数 + App 凭据 + encode.pyc 的 hkey。
    # 用 1.3.398 / native 签名 / Max-Android UA 都会失败。
    # 参数拼写照抄 C# 源码：divice_info、channal（不是 device_info/channel）。
    # ------------------------------------------------------------------

    async def send_private_message(self, peer, text):
        """给某人发私信（走 App 通道）。"""
        peer = identifier(peer)
        text = str(text or "").strip()
        if not text:
            raise OperatorError("私信内容不能为空。", "invalid_input", 2)

        app = _load_app_session()
        path = "/chat/send_message/"
        ts = int(time.time())
        nonce = _rand_alnum(32)
        hkey = _app_hkey(path, ts, nonce)

        params = {
            "heybox_id": str(app["heybox_id"]),
            "imei": str(app["device_id"]),
            "divice_info": "P40",
            "os_type": "Android",
            "x_os_type": "Android",
            "x_client_type": "mobile",
            "os_version": "10",
            "version": "1.3.218",
            "x_app": "heybox",
            "channal": "heybox_xiaomi",
            "userid": peer,
            "_time": str(ts),
            "nonce": nonce,
            "hkey": hkey,
        }
        headers = {
            "User-Agent": ("Mozilla/5.0 AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/41.0.2272.118 Safari/537.36 ApiMaxJia/1.0"),
            "Referer": "http://api.maxjia.com/",
            "Cookie": "pkey=%s; x_xhh_tokenid=%s" % (app["pkey"], app["x_xhh_tokenid"]),
        }
        response = await self.api.client.post(
            API_BASE + path, params=params,
            data={"text": text, "img": ""}, headers=headers)
        try:
            payload = response.json()
        except Exception:
            raise OperatorError("私信接口返回的不是 JSON：%s" % response.text[:150],
                                "send_failed", 4)
        # /chat/ 回的是完整响应
        result = payload.get("result") if isinstance(payload.get("result"), dict) else payload
        if str(payload.get("status", "")) != "ok":
            raise OperatorError("私信没发出去：%s" % (payload.get("msg") or "未知原因"),
                                "send_failed", 4)
        return {
            "结果": "已发送",
            "对象": peer,
            "内容": text[:80] + ("…" if len(text) > 80 else ""),
            "msg_id": result.get("msg_id") or payload.get("msg_id"),
        }


# ----------------------------------------------------------------------
# [本地补丁 2026-10-27] App 通道辅助
# ----------------------------------------------------------------------

_APP_SESSION_PATH = os.path.expanduser("~/.config/xiaoheihe-cli/app-session.json")


def _preferred_cookies(session):
    """凭据优先级：App 登录态 > 网页 cookie。

    App 凭据一套就能跑 /bbs/app/（网页签名）、/chat/（encode 签名）、
    /account/ 与 /task/（native 签名），登录一次不用再回网页拿密钥。
    设 XHH_FORCE_WEB_COOKIE=1 可以强制用网页 cookie 回退排查。
    """
    # 实测结论：App 凭据能过 /bbs/app/ 的**读**接口，但写接口（发评论/发帖/关注）
    # 会回「请登录后使用该功能」，所以默认仍用网页 cookie。
    # 想试 App 凭据读接口可以设 XHH_USE_APP_COOKIE=1。
    if os.environ.get("XHH_USE_APP_COOKIE") != "1":
        return (session or {}).get("cookies", [])
    try:
        app = _load_app_session()
    except OperatorError:
        return (session or {}).get("cookies", [])
    return [
        {"name": "pkey", "value": app["pkey"],
         "domain": ".xiaoheihe.cn", "path": "/", "secure": True},
        {"name": "x_xhh_tokenid", "value": app["x_xhh_tokenid"],
         "domain": ".xiaoheihe.cn", "path": "/", "secure": True},
    ]


def _load_app_session():
    """读 App 端登录态（手机号验证码登录后保存的那份）。"""
    try:
        with open(_APP_SESSION_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
    except FileNotFoundError:
        raise OperatorError("没有 App 登录态，发不了私信。", "no_app_session", 3)
    for key in ("heybox_id", "pkey", "x_xhh_tokenid", "device_id"):
        if not data.get(key):
            raise OperatorError("App 登录态缺 %s，需要重新登录。" % key, "bad_app_session", 3)
    return data


def _rand_alnum(n):
    return "".join(random.choices(string.ascii_letters + string.digits, k=n))


def _app_hkey(path, ts, nonce):
    """算 App 通道的 hkey（纯 Python，见 app_sign.py）。"""
    from .app_sign import hkeyencode
    return hkeyencode(path, ts, nonce)
