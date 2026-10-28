# 小黑盒 MCP

小黑盒（Heybox，原小黑盒游戏社区）的 MCP 服务器与命令行工具。提供 30 个 MCP 工具，覆盖浏览、发帖、评论、互动、私信、签到与账号管理，可以直接接入任何支持 MCP 的客户端。

同时提供一个独立的 CLI（`xiaoheihe-cli`），不依赖 MCP 也能用。

## 特性

- **完整的社区操作**：首页推荐、搜索、帖子详情（含楼层与楼中楼）、发图文帖、发长文、发视频、评论、回复、点赞
- **私信收发**：通过 App 通道实现，需要独立的 App 登录
- **签到**：每日签到与状态查询
- **评论定位**：回复评论时可按正文定位，无需提前查 ID
- **MCP 与 CLI 双形态**：MCP 供 AI 客户端调用，CLI 供脚本和人工使用
- **纯 Python 依赖**：除设备指纹计算需要 Node.js 外，全部逻辑为 Python 实现

## 安装

```bash
bash install.sh
```

脚本会完成以下步骤：

1. 以 editable 模式将本包安装到 `uv tool` 环境
2. 验证 `xiaoheihe-cli` 命令可用
3. 断言模块加载路径指向本项目目录，防止误装成 PyPI 上的同名包
4. 检查关键工具是否已注册
5. 重启 MCP 进程

需要重跑 `install.sh` 的情况：更换机器、重装系统、`uv tool` 环境被删除或重建、`xiaoheihe-cli` 命令丢失。

登录态保存在 `~/.config/xiaoheihe-cli/`，与代码目录无关，重装不影响。

### 依赖

- Python 3.9+
- Node.js（仅用于 App 登录时计算设备指纹，调用官方 `fp.min.js`）

## 快速开始

### 登录

小黑盒有两套独立的凭据体系，用途不同，不能互相替代。

| 用途 | 凭据 | 登录方式 |
|---|---|---|
| 发帖、评论、关注、浏览 | 网页 cookie（`session.json`） | `xiaoheihe-cli login`（扫码） |
| 私信收发、签到 | App 凭据（`app-session.json`） | `xiaoheihe-cli app-login`（手机号 + 验证码） |

**网页登录**：

```bash
xiaoheihe-cli login          # 扫码登录
```

**App 登录**（私信与签到需要）：

```bash
xiaoheihe-cli app-login                        # 交互式，一次走完
xiaoheihe-cli app-login --phone 180xxxxxxxx    # 仅发验证码
xiaoheihe-cli app-login --code 1234            # 仅提交验证码
```

分两步执行时，发码阶段会把设备令牌与 `device_id` 写入 `app-login-pending.json`，提交阶段复用同一套。若每次调用都重新生成设备令牌，提交必然失败——这是 App 登录最容易踩的坑。

两套登录态相互独立，登录一边不会使另一边失效。

注意：App 登录会将该账号在手机 App 上挤下线。

### 为什么需要两套凭据

实测结论：

- App 凭据可以通过 `/bbs/app/` 下的**读**接口，但**写接口一律返回「请登录后使用该功能」**——发帖、评论、关注只认网页会话。
- 网页 cookie 无法访问 `/chat/`（私信）和 `/task/`（签到）两组接口。

因此两套凭据各司其职，无法统一。只想私信的话，额外执行一次 `app-login` 即可，其余操作不受影响。

## 工具清单

以下 30 个 MCP 工具按用途分组，括号内为工具名。

### 浏览

| 功能 | 工具 |
|---|---|
| 首页推荐流 | `get_home_content` |
| 搜索帖子 / 用户 / 话题 / 游戏 | `search_content` |
| 帖子详情（正文 + 楼层 + 楼中楼） | `get_post_detail` |
| 某用户的帖子列表 | `user_posts` |
| 自己的主页与动态 | `get_user_profile` |
| 下载帖子正文图片 | `get_post_detail(downloadImages=true)` |

搜索用户必须使用**数字 ID**，昵称无法搜到。平台明确说明「目前仅支持搜索发布投稿作者、用户数字ID」，搜索昵称会返回大量同名用户。要查看某人发过什么，直接用 `user_posts(userId=...)` 更准确。

搜索帖子按标题与正文关键词匹配，不包含作者名。索引存在不完整的情况：部分新帖搜不到，同一篇帖子用完整标题搜不到、换关键词反而能命中，属于平台侧限制，App 与网页表现一致。

### 发布

| 功能 | 工具 |
|---|---|
| 发图文帖 | `publish_content` |
| 发长文（支持 Markdown / HTML / 本地图片） | `publish_article` |
| **改已发布的图文帖**（标题/正文/配图/分区） | `edit_content` |
| 发视频与封面 | `publish_video` |
| 发评论 | `post_comment` |
| 回复评论 | `reply_comment` |
| 批量回复（单次最多 12 条，自动排队限流） | `reply_many` |
| 表情代码列表 | `list_emoticons` |

发帖类工具均有三种模式：`preview`（默认，仅预览）、`draft`（存草稿）、`publish`（正式发布）。`communities` 参数传分区名（如 `["AI"]`），不是话题标签。

正文中可直接书写 `[cube_狗头]` 这类中文别名，工具会自动转换为真实的表情代码。

### 互动

| 功能 | 工具 |
|---|---|
| 关注 | `follow_user` |
| 取消关注 | `unfollow_user` |
| 查看关注列表 | `list_following` |
| 查看粉丝列表 | `list_followers` |

写操作默认 `dry_run=true`，只做预检不实际执行，需显式传 `false` 才会发出。

小黑盒网页端没有「好友」接口，关注是唯一能建立关系的方式。

### 私信

| 功能 | 工具 |
|---|---|
| 读取与某人的私信 | `list_private_messages` |
| 发送私信 | `send_private_message` |

私信走 App 通道，依赖 App 凭据。

读取时，对方发来的消息在 `markdown` 字段，自己发出的在 `text` 字段。`is_user=1` 表示自己发送，但系统提示消息（如「我们已经是好友啦」）同样为 1，需根据文案判断。

### 通知

| 功能 | 工具 |
|---|---|
| 评论与回复（含被回复的原文） | `list_messages(kind="reply")` |
| 收到的赞 | `list_messages(kind="award")` |
| 官方通知 / 好友申请 / 私信提醒 | `list_messages(kind="official")` |
| 官方系统通知 | `list_notifications` |

### 账号

| 功能 | 工具 |
|---|---|
| 查询登录状态 | `check_login_status` |
| 扫码登录 | `interactive_login`、`get_login_qr_code` |
| 我的游戏记录 | `get_personal_games` |
| 我的评分与短评 | `list_personal_reviews`、`get_personal_review`、`create_personal_review`、`edit_personal_review`、`delete_personal_review` |
| 清理本地图片缓存 | `clean_cache` |

### 签到

```bash
xiaoheihe-cli sign --status   # 查询今日是否已签到
xiaoheihe-cli sign            # 执行签到
```

签到相关的接口属于 `/task/` 组，但该组内部并不统一：

| 操作 | 接口 | 签名与凭据 |
|---|---|---|
| 查询签到状态 | `/task/list_v2/` | 网页签名 + 网页 cookie |
| 执行签到 | `/task/sign_v3/sign` | native 签名 + App 凭据 |

查询状态不要用 `/task/sign_v4/get_sign_state`。该接口在当天已签到的情况下返回「非法请求」，容易误判为签名失效，实际只是重复签到被拒绝。查询状态应使用 `/task/list_v2/`，读取「每日任务」中标题为「签到」的项，判断 `state` 是否为 `finish`。

**签名路径与请求路径不同**：这是签到接口最容易踩的坑。

`/task/` 的 native 签名在使用路径时，会先做归一化——**去掉首尾多余斜杠，再补一个尾部斜杠**：

```
签名用：/task/sign_v3/sign/     ← 带尾斜杠
请求用：/task/sign_v3/sign      ← 不带
```

两者不一致时，服务端一律返回「非法请求」，看起来像签名算法错误，实际算法完全正确。签名的 `hkey` 与 `_rnd` 可用参考实现逐位比对验证，对得上就说明问题在这个斜杠上。

签名同时绑定 `osVersion` 与 `version`（`_eE3(debug_info + osVersion + version)`），修改请求里的这两项必须同步改签名参数，且 native 签名只支持 `1.3.398` / build `1142` / shader `14`。

## 设计说明

### 两套签名算法

小黑盒不同后端使用不同的签名方案，不可混用：

| 实现 | 用于 | 特征 |
|---|---|---|
| `app_sign.py` 的 `hkeyencode` | 私信 `/chat/` | 7 位；HMAC-SHA1 变体 + GF(2^8) 运算 |
| `app_sign_native.py` 的 `native_signature` | 登录、签到 `/account/` `/task/` | 8 位大写十六进制 + `_rnd`；HMAC-SHA512 + 自定义 CRC32 |

native 签名中的 SHA-512 为修改版，与标准实现有三处差异：K 表后半段被替换、`Ch` 函数改为 `(x & (y ^ z)) ^ y`、`_rnd` 使用 CRC32 多项式 `0xAD350290`。实现已与官方 JS 逐字节比对一致。

### 评论读取的容错

平台返回的 `child_num` 会把已删除、已折叠的回复计算在内，导致楼中楼数量校验失败。原始实现遇到不一致时直接抛出异常，整页读取随之失败，按正文定位评论也无法工作。

当前实现改为标记 `_incomplete` 后继续，同类情况共三处：

| 情况 | 处理方式 |
|---|---|
| 楼中楼数量与 `child_num` 不符 | 标记 `_incomplete = True`，继续 |
| 页面为空但标记 `has_more_floors` | 停止翻页，保留已读内容 |
| 达到 `max_pages` 仍未到底 | 返回已有结果 |

翻页时平台会重复返回已读楼层（`offset` 按楼层数推进，但置顶与删除会导致楼层号错位），遇到重复楼层直接跳过。

### 评论定位

`reply_comment` 的 `targetCommentContent` 支持两种方式：

- 精确指定 `replyId` 与 `rootId`
- 只给正文，工具按**先精确、再包含**的顺序匹配

包含匹配仅在唯一命中时采用，多处命中会报错，不会误回。因此消息中心返回的截断正文（末尾带「…」）可以直接用于定位，无需先查询完整原文。

### 私信实现要点

私信接口能用，依赖四个要素同时成立，缺一不可：

1. 请求参数采用 App 1.3.218 的格式（不是 1.3.398）
2. 使用 App 端凭据（`app-login` 取得）
3. 使用 App 的 native hkey 签名
4. User-Agent 为 `ApiMaxJia`（不是 `Max-Android`）

另外，参数名需照抄 C# 实现中的拼写：`divice_info`、`channal`——这两处确实是错误拼写，但服务端按此校验。

接口路径在 `/chat/` 前缀下，而非 `/bbs/app/`。这一点容易误判：按 `/bbs/app/` 试会全部返回 404，只有前缀正确后才会开始返回参数校验错误。

### 关注接口

- 关注：`POST /bbs/app/profile/follow/user`
- 取消关注：`POST /bbs/app/profile/follow/user/cancel`
- 表单只需提交 `following_id`（对方 uid），不要提交 `follower_id`
- 缺少必填参数时，服务端会明确返回「both following id and follower id is required」

### 错误信息透出

上游失败时，工具会透出平台返回的真实 `msg`（超过 300 字截断），而不是统一压缩成一句泛化的提示。这对调试至关重要——例如关注接口的必填参数就是通过错误信息发现的。

`http.request()` 的返回值已经是 `result` 字段内的内容，字段位于**顶层**，无需再取一层 `result`。

## 目录结构

```
.
├── install.sh           安装与重装脚本
├── pyproject.toml       包定义
├── LICENSE
├── README.md
├── xhh_operator/        主包
├── xiaoheihe/           上游模块
└── app_sign.py          签名实现
```

MCP 入口命令：`xiaoheihe-cli mcp`

## License

MIT License。原始版权归 Li Fangyu。

## 致谢

本项目在实现过程中参考了以下项目，做法是取长补短：参考各自的接口与算法思路，独立实现，并对实测结果逐项校验。

- **[AaronZ345/xiaoheihe-cli](https://github.com/AaronZ345/xiaoheihe-cli)**（MIT）：最早参考其网页端接口与命令行结构。当前版本已与其有较大差异——完整的工具集、App 手机号登录、两套签名算法、签到与私信均为后续独立实现。
- **[chr233/Xiaoheihe_CSharp](https://github.com/chr233/Xiaoheihe_CSharp)**：`/chat/` 私信使用的 hkey 签名（7 位）参考其 C# 实现移植。私信功能得以打通，这一份是关键。
- **[Cansiny0320/heybox-daily](https://github.com/Cansiny0320/heybox-daily)**：`/task/` 签到使用的 native 签名（8 位大写十六进制 + `_rnd`，含修改版 SHA-512）参考其实现。

三个项目覆盖了小黑盒三个不同后端：网页端、私信通道、签到通道。各自思路不同，合起来才构成完整图景。
