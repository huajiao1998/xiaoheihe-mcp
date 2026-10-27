# 小黑盒 MCP（我们的本体）

这是我们自己的小黑盒工具，**不是对别人包的补丁**。

代码就活在这个目录里，通过 editable 方式装进 uv tool 环境——site-packages 里只有
两个 `__editable__*.pth` 文件指向这里，没有代码副本。改这里的文件、重启 MCP 就生效。

底层来自 [AaronZ345/xiaoheihe-cli](https://github.com/AaronZ345/xiaoheihe-cli) v0.1.0（MIT），
在上面改了六处、补了四个工具。原包不再跟进升级——我们这版就是最新的。

---

## 跟「打补丁」的区别

| | 以前 | 现在 |
|---|---|---|
| 代码在哪 | uv tool 的 site-packages 里 | **本目录** |
| site-packages 里有什么 | 一份改过的代码副本 | 只有两个 `.pth` 指针 |
| 改完怎么生效 | 改 site-packages + 重启 | 改本目录 + 重启 |
| 重装工具会怎样 | **改动全丢**，得跑 restore.sh | 跑 `install.sh` 重装即可，代码一直在 |
| 升级原包 | 会覆盖 | 不升级了，这是我们自己的分支 |

---

## 装 / 重装

```bash
bash install.sh
```

什么时候需要跑：

- 换机器、重装系统
- 手滑 `uv tool uninstall xiaoheihe-cli` 或删了 uv tool 环境
- uv 大版本升级把 tool 环境重建了
- `xiaoheihe-cli` 命令不见了

脚本会：editable 安装 → 验证命令能跑 → **断言加载路径指向本目录**（防止静默装成别的）
→ 检查四个关注工具在不在 → 重启 MCP。

**登录态不受影响**：存在 `~/.config/xiaoheihe-cli/session.json`，跟代码目录无关。

---

## 改了六处

### 1. 评论读取：三处「读不完整就抛错」改成容忍

`xhh_operator/comments.py`

平台的 `child_num` 会把**已删除、已折叠**的回复也算进去，导致「楼中楼数量不足」的校验永远失败，
一失败就 `raise ProtocolError`，整页 `read_thread` 全挂——按正文定位评论也跟着废。

改成打标记 `_incomplete = True` 跳过。同一类毛病共修三处：

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

### 3. 报错透出上游真实原因

`xhh_operator/http.py`

原版把所有上游失败压成一句「小黑盒拒绝了操作，请检查账号权限、内容及接口兼容性」，
**看不到真正的原因**。改成把上游 `msg` 带出来（超过 300 字截断）。

> 这条价值很大：关注接口就是靠它吐出的「both following id and follower id is required」
> 才挖到参数名的。原版那句话会把这个线索吞掉。

### 4. 表情白名单

`xhh_operator/mcp_server.py`

正文里写 `[cube_狗头]` 这种中文别名，工具自动换成真实代码。

### 5. 报错文案：把「社区」和「话题标签」分清

`xhh_operator/selections.py`

原版这句把两个概念混成一句话，报错时看不懂到底是哪个参数错了。

### 6. 新增：关注功能（四个 MCP 工具）

`xhh_operator/service.py` + `xhh_operator/mcp_server.py`

小黑盒网页端**没有「好友」接口，私信也发不出去**。关注是现有能力里唯一真能加人的。

| 工具 | 干什么 | 默认行为 |
|---|---|---|
| `follow_user` | 关注某人（传对方 uid） | `dry_run=true` 只预检 |
| `unfollow_user` | 取消关注 | 同上 |
| `list_following` | 我关注了谁 / 某人关注了谁 | 只读 |
| `list_followers` | 谁关注了我 / 某人的粉丝 | 只读 |

接口参数（踩过坑）：

- `POST /bbs/app/profile/follow/user`，取关 `POST /bbs/app/profile/follow/user/cancel`
- **表单只发 `following_id`**（对方 uid），不要发 `follower_id`
- 报错会直接说缺什么：「both following id and follower id is required」

> 还有个坑：`http.request()` 返回的已经是 `result` 里的内容，字段在**顶层**。
> 我一开始多取了一层 `data.get("result")`，导致列表永远是空，白折腾一晚上。

---

## 目录

```
.
├── install.sh           ← 装 / 重装
├── pyproject.toml       包定义（版本 0.1.0+local.1，依赖内含 mcp）
├── LICENSE              原包 MIT，版权 Li Fangyu
├── README.md
├── xhh_operator/        主包（20 个 py，改动都在这）
├── xiaoheihe/
├── *.py                 上游那些模块（api_client / browser_manager / cli / ...）
└── .gitignore
```

MCP 入口：`xiaoheihe-cli mcp`（命令在 `/root/.local/bin/`）。

---

## 已知走不通的

**私信**。`/chat/send_message/` 接口存在（返回「参数错误」而非 404），但参数试了 16 组全错，
公开代码里没有任何一份可参考：

- `/chat/` 是**独立后端**，返回带 `"version":"1.0"`，跟 `/bbs/app/` 不是一套
- 签名是对的——`hkey` 挪到请求头它立刻说「hkey 不能为空」，放回查询参数就变「参数错误」
- App 端参数由 `libhbsecurity.so` 在 native 层生成，还套了虚拟机保护

结论：网页 cookie 这条路到头了。全记录在项目的 `docs/小黑盒私信与好友-能做什么.md`。

---

MIT License · 原项目版权归 Li Fangyu
