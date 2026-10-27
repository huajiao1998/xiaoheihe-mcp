"""社区与话题按准确名称解析，不静默选择搜索结果中的第一项。"""

from .errors import OperatorError, ProtocolError


def validate_selections(communities=(), hashtags=()):
    if isinstance(communities, str) or isinstance(hashtags, str):
        raise ValueError("社区和话题应为名称列表。")
    communities, hashtags = list(communities or ()), list(hashtags or ())
    if len(communities) > 2 or len(hashtags) > 5:
        raise ValueError("每篇内容最多选择 2 个社区、5 个话题标签。")
    for values in (communities, hashtags):
        if any(not isinstance(v, str) or not v.strip() for v in values) or len(set(values)) != len(
            values
        ):
            raise ValueError("社区和话题名称不能为空或重复。")
    return communities, hashtags


async def resolve_selections(api, communities=(), hashtags=()):
    communities, hashtags = validate_selections(communities, hashtags)
    topics, tags = [], []
    for kind, names, target in (("topic", communities, topics), ("hashtag", hashtags, tags)):
        for name in names:
            response = await api.request(
                "/bbs/app/api/post_editor/topic_selection/search",
                params={"q": name, "only_hashtag": "0"},
            )
            items = (response.get("result") or {}).get("search_result")
            if not isinstance(items, list):
                raise ProtocolError("社区和话题搜索响应格式已变化。")
            matches = [
                item
                for item in items
                if item.get("search_type") == kind
                and (item.get("name") or item.get("title")) == name
            ]
            if len(matches) != 1:
                # [本地补丁 2026-10-27] 原文案把「社区」和「话题标签」混成一句，
                # 而 post_publish 会把 tags 当 hashtags 传进来，于是 tags 搜不到时
                # 误报成「社区不存在」——实测 tags=["大肥鱼"] 就报「社区或话题不存在」。
                label = "社区" if kind == "topic" else "话题标签"
                raise OperatorError(
                    f"{label}搜不到或重名：{name}（精确匹配 {len(matches)} 个）", "selection"
                )
            item = matches[0]
            if kind == "topic" and not item.get("topic_id"):
                raise ProtocolError("社区搜索结果缺少 ID。")
            if (
                kind == "hashtag"
                and item.get("is_act_hashtag")
                and any(t.get("is_act_hashtag") for t in tags)
            ):
                raise ValueError("每篇内容最多选择一个活动话题。")
            target.append(item)
    return {
        "topic_ids": ",".join(str(item["topic_id"]) for item in topics),
        "hashtags": [item["name"] for item in tags],
    }
