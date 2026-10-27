# 小黑盒 MCP 本地改进版（备份）

基于 [AaronZ345/xiaoheihe-cli](https://github.com/AaronZ345/xiaoheihe-cli) **v0.1.0** 的本地修改版。
原包 MIT 许可（见 `LICENSE`，版权归 Li Fangyu），这里只是把改过的地方留个底。

**改动全部在站点的 `site-packages` 里，重装或升级原包会被覆盖。** 这个仓库就是为了让它丢得回来。

---

## 为什么会有这个仓库

我用小黑盒（xiaoheihe）发帖、回评论、和评论区的人聊天。原版 CLI 有六个地方不通或用着别扭，
就在本地改了；另外补了四个「关注」相关的 MCP 工具。改在 site-packages 里，升级必丢，
所以整包备份一份，改了什么全写在下面。

---

## 一共改了六处

### 1. 评论读取：三处「读不完整就抛错」改成容忍

`xhh_operator/comments.py`

平台的 `child_num` 会把**已删除、已折叠**的回复也算进去，导致「楼中楼数量不足」的校验永远失败，
一失败就 `raise ProtocolError`，整页 `read_thread` 全挂——按正文定位评论也跟着废。

改成打标记 `_incomplete = True` 跳过，不拖垮整页。同一类毛病共修三处：

| 位置 | 原来 | 现在 |
|---|---|---|
| 楼中楼数量对不上 | `raise ProtocolError` | `group["_incomplete"] = True` |
| 空页却标 `has_more_floors` | `raise ProtocolError` | 停止翻页，保留已读到的 |
| 跑到 `max_pages` 还没到底 | `raise ProtocolError` | 返回已有结果 |

### 2. 评论读取：翻页重复楼层当死循环

`xhh_operator/comments.py`

平台翻页会**重复吐回已读楼层**（`offset` 按楼层数推进，但置顶/删除会让楼层号错位）。
原版把这当死循环直接抛错。改成重复就 `continue` 跳过。

> 这条是让我能按评论正文定位的前提，之前 `reply_comment` 用 `targetCommentContent` 一直在报错。

### 3. 表情白名单

`xhh_operator/mcp_server.py`

正文里写 `[cube_狗头]` 这种中文别名，工具自动换成真实代码。原版没有白名单，
写错一个字就原样显示成文本。

### 4. 报错文案：把「社区」和「话题标签」分清

`xhh_operator/selections.py`

原版这句把两个概念混成一句话，报错时看不懂到底是哪个参数错了。

### 5. 新增：关注功能（四个 MCP 工具）

`xhh_operator/service.py` + `xhh_operator/mcp_server.py`

小黑盒网页端**没有「好友」接口，私信也发不出去**（`/chat/send_message/` 存在但参数试了十几组全报错，
详见 `docs/小黑盒私信与好友-能做什么.md`）。关注是现有能力里唯一真能加人的。

| 工具 | 干什么 |
|---|---|
| `follow_user` | 关注某人（传对方 uid） |
| `unfollow_user` | 取消关注 |
| `list_following` | 我关注了谁 / 某人关注了谁 |
| `list_followers` | 谁关注了我 / 某人的粉丝 |

接口参数（踩过坑，记下来）：

- `POST /bbs/app/profile/follow/user`，取关 `POST /bbs/app/profile/follow/user/cancel`
- **表单只发 `following_id`**（对方 uid），不要发 `follower_id`。这是
  [heybox-lite 的官方 APK 核对文档](https://github.com/huanghao897/heybox-lite/blob/main/docs/OFFICIAL_API_AUDIT.md)写明的
- 报错会直接说缺什么：「both following id and follower id is required」

### 6. 批量回复工具

`tools/xhh-reply-batch.py`（在 `tools/` 下，不在包里）

一次回同一帖下多条评论，按 `gap` 秒排队提交，避开限流（约 10 秒一条、每小时 30 条）。

---

## 怎么装回去

`scripts/restore.sh` 只恢复 `service.py` 和 `mcp_server.py` 两个文件（关注功能）。
其余补丁在 `xhh_operator/` 里是完整源码，直接整个目录覆盖到 site-packages 即可。

```bash
# 找安装目录（python 版本号会变）
TARGET=$(ls -d /root/.local/share/uv/tools/xiaoheihe-cli/lib/python*/site-packages/xhh_operator)

# 备份现有的，再整个覆盖
cp -r "$TARGET" "$TARGET.bak-$(date +%Y%m%d-%H%M%S)"
cp -r xhh_operator/. "$TARGET/"

# 重启 MCP 才生效（dsh 会自动拉起）
ps aux | grep 'xiaoheihe-cli mcp' | grep -v grep | awk '{print $2}' | xargs -r kill
```

> 升级后一定要重启 MCP 进程，否则跑的还是旧代码。

---

## 目录

```
.
├── LICENSE              # 原包 MIT，版权归 Li Fangyu
├── README.md
├── xhh_operator/        # 改过的完整源码（20 个文件）
├── docs/                # 踩坑记录
│   ├── 小黑盒工具-本地补丁.md      # ← 最详细的一份，改了什么、为什么
│   ├── 小黑盒私信与好友-能做什么.md # 私信走不通的完整查证
│   ├── 小黑盒表情代码.md
│   ├── 小黑盒数据复盘.md
│   └── 小黑盒热帖观察.md
├── tools/               # 配图 / 批量回复脚本
│   ├── xhh-card.py      # 正文卡（蓝渐变 + 文字 + 贴纸，1080×608）
│   ├── xhh-sticker.py   # 1080×1080 方图
│   └── xhh-reply-batch.py
└── scripts/
    └── restore.sh       # 把关注功能装回去
```

---

## 已知走不通的

**私信**。`/chat/send_message/` 接口存在（不返回 404，返回「参数错误」），但参数试了 16 组全错，
公开代码里没有任何一份可参考。查到的事实：

- `/chat/` 是**独立后端**，返回格式带 `"version":"1.0"`，跟 `/bbs/app/` 不是一套
- 签名是对的——把 `hkey` 挪到请求头，它立刻说「hkey 不能为空」；放回查询参数就变「参数错误」
- App 端参数由 `libhbsecurity.so` 在 native 层生成，还套了虚拟机保护

结论：网页 cookie 这条路到头了。想看全记录读 `docs/小黑盒私信与好友-能做什么.md`。

---

MIT License · 原项目版权归 Li Fangyu · 改动部分随便用
