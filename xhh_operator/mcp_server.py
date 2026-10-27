"""小黑盒 MCP：提供发布、查询、登录和个人评测工具，复用同一操作层。"""

import asyncio
import base64
import difflib
import logging
import re

from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, ToolAnnotations
from pydantic import StrictInt

from .auth import identity, login_browser, login_qr
from .errors import AuthError, OperatorError
from .http import HeyboxAPI
from .service import Operator, fmt_time, present_review
from .session import SessionStore, safe_result


# [本地补丁 2026-10-26] 小黑盒表情白名单：中文别名 → 真实代码。
# 实测统计自 10 篇热帖 220 个真实实例。写错一个字就原样显示成文本（比如 [cube_狗头] 不存在）。
EMOTICONS = {
    "鳄鱼": "cube_鳄鱼",
    "喜欢": "cube_喜欢",
    "摘墨镜": "cube_摘墨镜",
    "滑稽": "cube_滑稽",
    "哭泣": "cube_哭泣",
    "捂脸哭": "cube_捂脸哭",
    "惊讶": "cube_惊讶",
    "感动": "cube_感动",
    "摸摸头": "cube_摸摸头",
    "笑cry": "cube_笑cry",
    "笑死": "cube_笑cry",
    "笑哭": "cube_捂脸哭",
    "摸头": "cube_摸摸头",
    "狗头": "cube_doge",
    "doge": "cube_doge",
    "超人": "cube_超人",
    "开心": "cube_开心",
    "微笑": "cube_微笑",
    "比心": "cube_比心",
    "酷": "cube_酷",
    "害羞": "cube_害羞",
    "生气": "cube_生气",
    "委屈": "cube_委屈",
    "乖": "cube_乖",
    "僵尸": "cube_僵尸",
    "欧润吉": "cube_欧润吉",
    "握草": "cube_握草",
    "小鸡": "cube_小鸡",
    "并不简单": "cube_并不简单",
}


_EMO_PAT = re.compile(r"\[cube_([^\]]+)\]")


def _normalize_emoticons(content: str) -> str:
    """扫描正文里的 [cube_xxx]，能认出来的换成正确代码，认不出来的直接报错并给建议。

    这样写 [cube_狗头] 会被自动纠正成 [cube_doge]；写了个不存在的还会当场告诉我。
    白名单里没有的代码不会悄悄发出去（发出去只会显示成一串文本）。
    """

    def repl(match: re.Match[str]) -> str:
        key = match.group(1).strip()
        code = EMOTICONS.get(key)
        if code:
            return f"[{code}]"
        close = difflib.get_close_matches(key, sorted(set(EMOTICONS)), n=3, cutoff=0.4)
        hint = f"，最接近的：{'、'.join(close)}" if close else ""
        raise ValueError(f"没有这个表情：[cube_{key}]{hint}")

    return _EMO_PAT.sub(repl, content)


def create_server(*, port=8001):
    if not 1 <= port <= 65535:
        raise ValueError("MCP 端口无效。")
    server = FastMCP("xiaoheihe-cli", host="127.0.0.1", port=port, json_response=True)
    readonly = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True
    )
    writing = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True
    )
    qr_state = {"task": None, "error": None}

    async def call(method, *args, **kwargs):
        try:
            async with Operator() as operator:
                result = await getattr(operator, method)(*args, **kwargs)
            return safe_result(result)
        except OperatorError as error:
            if error.code == "auth":
                logging.getLogger(__name__).error("认证失败，操作已停止。")
            raise
        except (OSError, ValueError) as error:
            detail = str(error).strip()
            raise ValueError(detail or "操作参数或本地文件无效。") from None

    @server.tool(name="check_login_status", annotations=readonly)
    async def check_login_status() -> dict:
        """核实当前账号登录状态；不回传认证材料。"""
        session = SessionStore().load()
        if not session:
            return {
                "登录": False,
                "说明": "还没登录，先 get_login_qr_code 扫码",
                "扫码中": bool(qr_state["task"] and not qr_state["task"].done()),
                "错误": qr_state["error"],
            }
        async with HeyboxAPI(session["cookies"]) as api:
            response = await api.request("/account/restore_login")
            user = identity(response)
        if user != session["user_id"]:
            raise AuthError("当前账号与保存账号不一致，请重新登录。")
        result = response.get("result") or {}
        profile = result.get("profile") or result.get("account_detail") or {}
        name = profile.get("username") or profile.get("nickname") or "?"
        return {"登录": True, "账号": "%s（uid:%s）" % (name, user)}

    @server.tool(name="interactive_login", annotations=writing)
    async def interactive_login(
        browser: str = "auto",
        timeout: int = 300,
        executable_path: str | None = None,
        driver_path: str | None = None,
    ) -> dict:
        """打开独立的官方登录窗口，由用户完成登录和验证码。"""
        if not 1 <= timeout <= 600:
            raise ValueError("登录等待时间无效。")
        return await login_browser(
            SessionStore(),
            timeout=timeout,
            browser_name=browser,
            executable_path=executable_path,
            driver_path=driver_path,
        )

    @server.tool(name="get_login_qr_code", annotations=writing)
    async def get_login_qr_code(timeout: int = 180):
        """立即返回二维码图片，后台等待 App 确认；随后用 check_login_status 核实。"""
        if not 1 <= timeout <= 600:
            raise ValueError("登录等待时间无效。")
        old = qr_state["task"]
        if old and not old.done():
            old.cancel()
            await asyncio.gather(old, return_exceptions=True)
        ready = asyncio.Event()
        data = {}

        async def on_ready(path):
            data["png"] = base64.b64encode(path.read_bytes()).decode()
            ready.set()

        async def wait_for_login():
            try:
                await login_qr(SessionStore(), timeout=timeout, on_ready=on_ready)
            except OperatorError as error:
                qr_state["error"] = error.code
                if error.code == "auth":
                    logging.getLogger(__name__).error("认证失败，登录已停止。")
            finally:
                ready.set()

        qr_state["error"] = None
        task = asyncio.create_task(wait_for_login())
        qr_state["task"] = task
        await ready.wait()
        if "png" not in data:
            await task
            raise OperatorError("无法生成登录二维码。", qr_state["error"] or "upstream")
        return ImageContent(type="image", data=data["png"], mimeType="image/png")

    @server.tool(name="get_user_profile", annotations=readonly)
    async def get_user_profile(pageSize: int = 10) -> dict:
        """读取本人主页信息及动态，不把用户内容保存到仓库。"""
        if not 1 <= pageSize <= 100:
            raise ValueError("分页大小无效。")
        return await call("profile", limit=pageSize)

    @server.tool(name="get_home_content", annotations=readonly)
    async def get_home_content(limit: int = 20, offset: int = 0) -> dict:
        """读取首页推荐（平台一次就回 10 条左右，limit 基本没用），一行一条：标题｜分区｜数据｜时间｜id。"""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("分页参数无效。")
        return await call("feed", limit=limit, offset=offset)

    @server.tool(name="search_content", annotations=readonly)
    async def search_content(
        keyword: str, kind: str = "post", limit: int = 20, offset: int = 0
    ) -> dict:
        """搜索帖子、游戏、用户或话题（kind=post|user|topic|game），返回一行一条的中文列表。

        搜用户请用**数字 ID**，别用昵称：平台页面自己写着「目前仅支持搜索发布投稿作者、用户数字ID」，
        搜昵称会搜出一堆同名的陌生人。查某人发过什么帖子，直接用 user_posts(userId=...) 更准。
        搜帖子是按标题/正文关键词匹配，不含作者名；新帖可能还没进索引，搜不到是平台侧的事。
        """
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("分页参数无效。")
        return await call("search", keyword, kind=kind, limit=limit, offset=offset)

    @server.tool(name="get_post_detail", annotations=readonly)
    async def get_post_detail(
        postId: str,
        page: int = 1,
        limit: int = 10,
        full: bool = False,
        downloadImages: bool = False,
        sort: str = "hot",
    ) -> dict:
        """按 ID 读帖子：默认返回正文 + 该页 10 层评论（含楼中楼、@ 已格式化成「@名字（uid:数字）」）；翻页传 page；sort=latest 按最新评论排；full=true 回原始全量；downloadImages=true 会把正文图片下载到本地并返回 path，可直接打开看。"""
        return await call(
            "post_view",
            postId,
            page=page,
            limit=limit,
            full=full,
            download_images=downloadImages,
            sort=sort,
        )

    @server.tool(name="clean_cache", annotations=readonly)
    async def clean_cache(days: int = 7, dryRun: bool = True) -> dict:
        """清理本地图片缓存（/tmp/xhh-images）里超过 N 天的目录；默认只预览，dryRun=false 才真删。"""
        return await call("clean_cache", days=days, dry_run=dryRun)

    @server.tool(name="user_posts", annotations=readonly)
    async def user_posts(userId: str, offset: int = 0, limit: int = 10) -> dict:
        """按 UID 读任意用户主页的帖子列表（标题、浏览、点赞、link_id）；评论里的 userid 可以直接查。"""
        return await call("user_posts", userId, offset=offset, limit=limit)

    @server.tool(name="list_notifications", annotations=readonly)
    async def list_notifications(limit: int = 20) -> dict:
        """读官方系统通知（登录安全、官方消息）。要看谁回了你、谁赞了你，用 list_messages。"""
        if not 1 <= limit <= 100:
            raise ValueError("分页参数无效。")
        return await call("notifications", limit=limit)

    @server.tool(name="list_messages", annotations=readonly)
    async def list_messages(kind: str = "reply", limit: int = 20, offset: int = 0) -> dict:
        """读消息中心：kind=reply 评论与回复（谁回了、说了什么、回的是我哪句）、award 赞、official 通知（里面混着三样：官方系统通知、好友申请、私信）。

        official 里「私信」那几行就是别人发来的私信正文，末尾带对方 uid。**看得到，回不了**——小黑盒网页 API 没有私信发送接口，私聊只有 App 端有。想跟人对话走评论/回复。
        """
        if kind not in ("reply", "award", "official"):
            raise ValueError("kind 只能是 reply / award / official。")
        if not 1 <= limit <= 100:
            raise ValueError("分页参数无效。")
        if offset < 0:
            raise ValueError("分页参数无效。")
        return await call("messages", kind=kind, limit=limit, offset=offset)

    @server.tool(name="follow_user", annotations=writing)
    async def follow_user(userId: str, dry_run: bool = True) -> dict:
        """关注一个用户（peer 传对方 heybox uid）。小黑盒的「好友」网页端没有接口，关注是唯一真能加的。默认只预检。"""
        if not str(userId).strip().isdigit():
            raise ValueError("userId 必须是数字 uid。")
        return await call("follow_user", peer=userId, dry_run=dry_run)

    @server.tool(name="unfollow_user", annotations=writing)
    async def unfollow_user(userId: str, dry_run: bool = True) -> dict:
        """取消关注。默认只预检。"""
        if not str(userId).strip().isdigit():
            raise ValueError("userId 必须是数字 uid。")
        return await call("unfollow_user", peer=userId, dry_run=dry_run)

    @server.tool(name="list_following", annotations=readonly)
    async def list_following(userId: str | None = None, limit: int = 20, offset: int = 0) -> dict:
        """我关注了谁；传 userId 就查那个人关注了谁。"""
        if not 1 <= limit <= 100:
            raise ValueError("分页参数无效。")
        return await call("following", peer=userId, limit=limit, offset=offset)

    @server.tool(name="send_private_message")
    async def send_private_message(userId: str, text: str) -> dict:
        """给某人发小黑盒私信。

        走 App 通道，用的是 app-session.json 里的 App 凭据（不是网页 cookie）。
        没有凭据或过期了就跑 `xiaoheihe-cli app-login` 用手机号验证码登录一次。
        注意：发帖/评论/关注走的是另一套（网页 cookie），两者不能互换。
        """
        if not str(userId).strip().isdigit():
            raise ValueError("userId 必须是数字 uid。")
        if not str(text or "").strip():
            raise ValueError("text 不能为空。")
        return await call("send_private_message", peer=userId, text=text)

    @server.tool(name="list_private_messages", annotations=readonly)
    async def list_private_messages(userId: str, limit: int = 20) -> dict:
        """读和某人的小黑盒私信会话（对方 uid）。

        走 App 通道（需要 app-session.json 里的 App 凭据）。只读，发用 send_private_message。
        对方发来的消息在 markdown 字段，自己发的在 text 字段，工具已合并处理。
        """
        if not str(userId).strip().isdigit():
            raise ValueError("userId 必须是数字 uid。")
        if not 1 <= limit <= 100:
            raise ValueError("分页参数无效。")
        return await call("private_messages", peer=userId, limit=limit)

    @server.tool(name="list_followers", annotations=readonly)
    async def list_followers(userId: str | None = None, limit: int = 20, offset: int = 0) -> dict:
        """谁关注了我；传 userId 就查那个人的粉丝。"""
        if not 1 <= limit <= 100:
            raise ValueError("分页参数无效。")
        return await call("followers", peer=userId, limit=limit, offset=offset)

    @server.tool(name="publish_content", annotations=writing)
    async def publish_content(
        title: str,
        content: str,
        images: list[str] | None = None,
        communities: list[str] | None = None,
        tags: list[str] | None = None,
        mode: str = "preview",
    ) -> dict:
        """发图文：mode=preview 只预览（默认）/ draft 存草稿 / publish 正式发布。必须传 communities（分区名，如 ["AI"]），预览里会显示分区。"""
        return await call(
            "post_publish",
            title,
            content,
            images=images or [],
            communities=communities or [],
            tags=tags or [],
            mode=mode,
        )

    @server.tool(name="publish_article", annotations=writing)
    async def publish_article(
        title: str,
        content: str,
        communities: list[str] | None = None,
        tags: list[str] | None = None,
        images: list[str] | None = None,
        mode: str = "preview",
    ) -> dict:
        """发长文（支持 Markdown、HTML 图片及正文里的本地图片绝对路径）：mode=preview 只预览（默认）/ draft 存草稿 / publish 正式发布；必须传 communities（分区名，如 ["AI"]）。"""
        return await call(
            "post_publish",
            title,
            content,
            images=images or [],
            communities=communities or [],
            tags=tags or [],
            mode=mode,
            kind="article",
        )

    @server.tool(name="publish_video", annotations=writing)
    async def publish_video(
        videoPath: str,
        title: str,
        content: str = "",
        coverImagePath: str | None = None,
        communities: list[str] | None = None,
        tags: list[str] | None = None,
        mode: str = "preview",
    ) -> dict:
        """发视频：mode=preview 只预览（默认）/ publish 正式发布（平台不支持视频草稿）。必须传 communities（分区名，如 ["AI"]），预览里会显示分区。"""
        if mode not in ("preview", "publish"):
            raise ValueError("视频的 mode 只能是 preview 或 publish。")
        return await call(
            "write_video",
            title,
            videoPath,
            content=content,
            cover=coverImagePath,
            communities=communities or [],
            hashtags=tags or [],
            draft=False,
            dry_run=(mode == "preview"),
        )

    @server.tool(name="post_comment", annotations=writing)
    async def post_comment(
        postId: str,
        content: str,
        images: list[str] | None = None,
        dry_run: bool = True,
    ) -> dict:
        """发表文字或图片评论；dry_run 只预检。评论有限流：一条之后隔十来秒再发，一小时最多 30 条左右。
        正文里可以直接写 [cube_狗头]，工具会自动纠正成正确的表情代码（写错会报错并给建议）。"""
        return await call(
            "comment", postId, _normalize_emoticons(content), images=images or [], dry_run=dry_run
        )

    @server.tool(name="reply_comment", annotations=writing)
    async def reply_comment(
        postId: str,
        content: str,
        targetCommentContent: str | None = None,
        replyId: str | None = None,
        rootId: str | None = None,
        dry_run: bool = True,
    ) -> dict:
        """回复评论：要么传 targetCommentContent 按正文定位（正文重复会拒绝），要么同时传 replyId + rootId 精确指定；dry_run 只预检。
        正文里可以直接写 [cube_狗头]，工具会自动纠正成正确的表情代码（写错会报错并给建议）。"""
        if (replyId is None) != (rootId is None):
            raise ValueError("replyId 和 rootId 要一起给。")
        if replyId is not None and targetCommentContent is not None:
            raise ValueError("按正文定位和按 ID 回复只能选一种。")
        if replyId is None and not targetCommentContent:
            raise ValueError("请给 targetCommentContent，或 replyId + rootId。")
        content = _normalize_emoticons(content)
        kwargs = {"dry_run": dry_run}
        if replyId is not None:
            kwargs.update(reply_id=str(replyId), root_id=str(rootId))
        else:
            kwargs["target_text"] = targetCommentContent
        return await call("comment", postId, content, **kwargs)

    @server.tool(name="list_emoticons", annotations=readonly)
    async def list_emoticons() -> dict:
        """列出可用的表情（中文名 → 代码）。正文里写 [cube_中文名] 就行，工具会自动换成正确代码。"""
        return {"可传的中文名": sorted(set(EMOTICONS)), "代码": EMOTICONS}

    @server.tool(name="reply_many", annotations=writing)
    async def reply_many(
        postId: str, items: list[dict], gap: float = 12.0, dry_run: bool = True
    ) -> dict:
        """批量回复同一条帖子下的多条评论。items 每项 {"reply_id": "...", "root_id": "...", "text": "..."}，
        root_id 不填时等于 reply_id（顶层评论）。按 gap 秒排队自动提交（默认 12，最少 10），
        全部完成后返回逐条结果。一次最多 12 条（限流大约 10 秒一条、每小时 30 条）。"""
        if not items:
            raise ValueError("items 不能为空。")
        if len(items) > 12:
            raise ValueError("一次最多 12 条，超过会被限流，也会卡住工具超时。")
        gap = max(10.0, float(gap))
        sent: list[dict] = []
        failed: list[dict] = []
        for index, item in enumerate(items, 1):
            if not isinstance(item, dict):
                failed.append({"index": index, "error": "每一项要是对象"})
                continue
            reply_id = str(item.get("reply_id") or "").strip()
            text = str(item.get("text") or "").strip()
            if not reply_id or not text:
                failed.append({"index": index, "reply_id": reply_id, "error": "缺 reply_id 或 text"})
                continue
            root_id = str(item.get("root_id") or reply_id).strip()
            try:
                await call(
                    "comment", postId, text, reply_id=reply_id, root_id=root_id, dry_run=dry_run
                )
                sent.append({"index": index, "reply_id": reply_id, "root_id": root_id, "text": text[:30]})
            except Exception as error:  # noqa: BLE001
                failed.append({"index": index, "reply_id": reply_id, "error": str(error)})
            if index < len(items):
                await asyncio.sleep(gap)
        return {
            "postId": postId,
            "dry_run": dry_run,
            "sent": sent,
            "failed": failed,
            "summary": f"成功 {len(sent)} 条，失败 {len(failed)} 条（间隔 {gap:g} 秒）",
        }

    @server.tool(name="get_personal_games", annotations=readonly)
    async def get_personal_games(limit: int = 20, offset: int = 0, query: str = "") -> dict:
        """读取本人的游戏记录，可按名称查询。"""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("分页参数无效。")
        return await call("games", limit=limit, offset=offset, query=query)

    @server.tool(name="list_personal_reviews", annotations=readonly)
    async def list_personal_reviews(
        limit: int = 20, offset: int = 0, summary: bool = False
    ) -> dict:
        """读取本人评分和完整短评；summary 仅取摘要。"""
        if not 1 <= limit <= 100 or offset < 0:
            raise ValueError("分页参数无效。")
        return await call("reviews", limit=limit, offset=offset, summary=summary)

    @server.tool(name="get_personal_review", annotations=readonly)
    async def get_personal_review(appid: str) -> dict:
        """读取单款游戏的本人评分与完整短评。"""
        data = present_review(await call("review", appid))
        if not data.get("exists"):
            return {
                "游戏": "《%s》" % (data.get("name") or "?"),
                "appid": data.get("appid") or appid,
                "短评": "还没写过",
            }
        images = [
            b.get("url")
            for b in (data.get("blocks") or [])
            if isinstance(b, dict) and b.get("type") == "img" and b.get("url")
        ]
        view = {
            "游戏": "《%s》" % (data.get("name") or "?"),
            "appid": data.get("appid") or appid,
            "我的评分": data.get("personal_score"),
            "短评": data.get("text") or "",
            "短评 ID": data.get("review_id") or "",
            "写于": fmt_time(data.get("created_at")),
        }
        if data.get("modified_at"):
            view["改于"] = fmt_time(data.get("modified_at"))
        if images:
            view["图片"] = images
        return view

    @server.tool(name="create_personal_review", annotations=writing)
    async def create_personal_review(
        appid: str, score: StrictInt, text: str, dry_run: bool = True
    ) -> dict:
        """创建个人评分与短评（score 为 1-5 的整数）；已有记录时拒绝覆盖。"""
        return await call("write_review", appid, score=score, content=text, dry_run=dry_run)

    @server.tool(name="edit_personal_review", annotations=writing)
    async def edit_personal_review(
        appid: str, score: StrictInt | None = None, text: str | None = None, dry_run: bool = True
    ) -> dict:
        """修改个人评分（1-5 的整数）或短评，未提供的字段保留原值。"""
        if score is None and text is None:
            raise ValueError("请提供评分或正文。")
        return await call(
            "write_review", appid, score=score, content=text, edit=True, dry_run=dry_run
        )

    @server.tool(
        name="delete_personal_review",
        annotations=ToolAnnotations(
            readOnlyHint=False, destructiveHint=True, idempotentHint=False, openWorldHint=True
        ),
    )
    async def delete_personal_review(appid: str, dry_run: bool = True) -> dict:
        """删除本人短评，先核实作者和游戏身份。"""
        return await call("delete_review", appid, dry_run=dry_run)

    return server


def serve(*, transport="stdio", port=8001):
    logging.getLogger("httpx").setLevel(logging.CRITICAL)
    create_server(port=port).run(transport=transport)
