# paperplane 多平台机器人

基于 **AstrBot** 的多平台聊天机器人:一个机器人同时接入 **QQ**、**飞书**、**Telegram(纸飞机)** 三个平台,收到转发消息自动回复"收到",并集成 **opencode**(AI 编程助手)提供智能回复。

## 功能

- 🎯 多平台接入:QQ(OneBot v11 / NapCat)、飞书、Telegram,一个 AstrBot 实例同时服务三个平台
- 📨 转发检测:收到转发消息自动回复"收到"(各平台支持情况见下方说明)
- 🤖 opencode 智能回复:指令 `/oc <问题>` 或 `/opencode <问题>` 调用 opencode 回答
- ⚙️ 可配置:收到转发消息时可选是否自动交给 opencode 处理(配置 `oc_on_forward`)

## 架构

```
QQ 个人号 ──NapCat(OneBot v11)──┐
飞书应用   ──事件订阅/长连接──────┼──► AstrBot ──► paperplane 插件 ──► opencode(子进程)
Telegram Bot ──Bot API(长轮询)──┘                    │                    │
                                                    └─► 回复"收到"        └─► 智能回复
```

- **QQ**:NapCat 作为 OneBot v11 协议端,通过反向 WebSocket 连接 AstrBot
- **飞书 / Telegram**:AstrBot 内置官方适配器,直接配置即可
- **opencode**:插件以子进程方式调用 `opencode run --format json --prompt <msg>`

## 目录结构

```
astrbot-multi-bot/
├── compose.yml                      # Docker Compose 编排(AstrBot + NapCat)
├── Dockerfile.astrbot-opencode     # 基于官方镜像安装 opencode
├── .env.example                    # 环境变量示例(复制为 .env)
├── README.md                       # 本文档
├── data/                           # AstrBot 数据目录(运行时生成)
│   ├── plugins/                    # 插件目录
│   └── opencode-auth/              # opencode 模型凭证(持久化)
├── napcat/                         # NapCat 配置与 QQ 登录态(运行时生成)
└── plugins/
    └── astrbot_plugin_paperplane/  # 本项目插件源码
        ├── metadata.yaml           # 插件元数据
        ├── _conf_schema.json       # 插件配置 schema
        ├── main.py                 # 插件主逻辑
        ├── opencode_client.py      # opencode 异步客户端
        └── requirements.txt
```

## 快速开始(Docker Compose)

```bash
# 1. 准备环境变量与目录
cp .env.example .env                 # 按需填写 OPENCODE_API_KEY
mkdir -p data/plugins napcat
cp -r plugins/astrbot_plugin_paperplane data/plugins/

# 2. 启动(建议先设置 UID/GID 再启动,避免权限问题)
export NAPCAT_UID=$(id -u) NAPCAT_GID=$(id -g)
docker compose up -d

# 3. 查看日志
docker compose logs -f astrbot
```

启动成功后浏览器访问 `http://<服务器IP>:6185` 打开 AstrBot 管理面板,默认账号密码均为 `astrbot`(首次登录强制修改密码)。

> 国内服务器拉镜像慢时,可将 compose.yml 中镜像地址加前缀 `m.daocloud.io/docker.io/`,例如 `m.daocloud.io/docker.io/soulter/astrbot:latest`。

## 接入消息平台

在 AstrBot WebUI 的 **「消息平台」** 页面添加适配器,三个平台可同时启用。

### 1. QQ(推荐 NapCat + OneBot v11)

本项目 compose 已包含 NapCat 服务,只需:

1. 浏览器打开 `http://<服务器IP>:6099`(NapCat WebUI),输入日志中打印的 token 登录
   ```bash
   docker compose logs napcat | grep -i "webui\|token" | head
   ```
2. 用**小号 QQ**扫码登录(⚠️ 新号/主号有风控封号风险,建议用养过的小号)
3. NapCat WebUI → **网络配置** → 新建 **WebSocket 客户端**:
   - URL:`ws://astrbot:6199/ws`(同 compose 网络用服务名;单独部署时填宿主机 IP)
   - 消息格式:`Array`
   - 心跳间隔:`5000`,重连间隔:`5000`
   - Token:自定一个强密码(下面 AstrBot 要填一样的)
4. AstrBot WebUI → **消息平台** → 新增适配器 → 选择「**接入 QQ 个人号(aiocqhttp)**」:
   - 反向 WebSocket 主机地址:`0.0.0.0`
   - 端口:`6199`
   - Token:与 NapCat 填的一致
5. 保存并重启 AstrBot(`docker compose restart astrbot`),日志出现 `aiocqhttp(OneBot v11) 适配器已连接` 即成功

验证:用另一个 QQ 私聊机器人发送 `/help`,有回复即全部打通。

### 2. 飞书

1. 打开 [飞书开放平台](https://open.feishu.cn/) → 创建**企业自建应用**
2. 应用 → **添加应用能力** → 添加「**机器人**」
3. 应用 → **权限管理** → 开通机器人相关权限(如 `im:message:send_as_bot`、`im:message:read` 等,按 AstrBot 页面提示勾选)
4. 应用 → **版本管理与发布** → 创建版本并发布(企业内可用「可用范围」限制)
5. AstrBot WebUI → **消息平台** → 新增适配器 → 选择「**飞书**」:
   - App ID / App Secret:应用凭证页获取
   - Verification Token / Encrypt Key:事件订阅页获取
   - 按页面提示选择事件订阅方式(WebSocket 长连接无需公网;Webhook 方式需公网可访问)
6. 详细说明见 [AstrBot 飞书接入文档](https://docs.astrbot.app/platform/lark.html)

> 飞书 Webhook 事件回调需要公网可达地址;内网环境可用 WebSocket 长连接方式或内网穿透。

### 3. Telegram(纸飞机)

1. 在 Telegram 中私聊 [@BotFather](https://t.me/BotFather),发送 `/newbot` 创建机器人,拿到 **Bot Token**
2. AstrBot WebUI → **消息平台** → 新增适配器 → 选择「**Telegram**」,填入 Token
3. 详细说明见 [AstrBot Telegram 接入文档](https://docs.astrbot.app/platform/telegram.html)

> 国内网络访问 Telegram API 需要代理。可在 AstrBot 配置中设置,或在 compose.yml 的 astrbot 服务环境变量中加入 `HTTP_PROXY` / `HTTPS_PROXY`。

## 接入 opencode

本项目镜像已内置 opencode(`Dockerfile.astrbot-opencode`)。两种接入方式:

### 方式 A:容器内直接运行(推荐)

1. 配置模型凭证(二选一):
   - 在 `.env` 中设置 `OPENCODE_API_KEY=你的Key`,然后 `docker compose up -d` 重建
   - 或进入容器交互式登录:`docker exec -it astrbot opencode auth login`
2. 确认插件配置:AstrBot WebUI → **插件** → paperplane 插件 → 配置
   - `oc_bin`:`opencode`(默认)
   - `oc_dir`:`/workspace`(默认)
   - `oc_model`:留空用默认,或填 `provider/model` 格式(如 `anthropic/claude-sonnet-4-5`)
   - `oc_timeout`:opencode 处理耗时较长,建议 ≥ 120 秒
3. 使用:
   - 群/私聊发送 `/oc 你的问题` 或 `/opencode 你的问题`
   - 在插件配置中打开 `oc_on_forward=true` 后,收到转发消息会自动交给 opencode 处理并回复结果

### 方式 B:附加到远程 opencode server

1. 在宿主机或另一台机器启动 opencode 服务:`opencode serve`(默认端口 4096)
2. 插件配置 `oc_attach_url` 填 `http://<主机IP>:4096`(远程需加 `--hostname 0.0.0.0` 与密码认证)
3. 插件将以 `opencode run --attach <url>` 方式调用,无需容器内安装 opencode

## 插件安装说明

插件源码在 `plugins/astrbot_plugin_paperplane/`,两种安装方式:

- **复制进数据目录**(推荐,项目内已自动完成):`cp -r plugins/astrbot_plugin_paperplane data/plugins/`,然后重启 AstrBot 或在 WebUI 插件页点「重载插件」
- **WebUI 在线安装**:将插件发布到 GitHub 后,在 WebUI 插件市场搜索安装

插件配置项(AstrBot WebUI → 插件 → paperplane → 配置):

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `reply_text` | 收到 | 收到转发消息时的回复文字 |
| `oc_enabled` | true | 是否启用 opencode 集成 |
| `oc_bin` | opencode | opencode 可执行文件路径 |
| `oc_dir` | /workspace | opencode 工作目录 |
| `oc_model` | (空) | 模型名,格式 `provider/model` |
| `oc_session` | (空) | 会话 ID,填写后可多轮延续同一会话 |
| `oc_attach_url` | (空) | opencode server 地址(方式 B) |
| `oc_timeout` | 180 | 调用超时(秒) |
| `oc_auto` | false | 自动批准 opencode 权限(危险) |
| `oc_on_forward` | false | 转发消息是否自动交给 opencode 处理 |

## 各平台转发检测说明

| 平台 | 检测能力 | 说明 |
| --- | --- | --- |
| QQ / OneBot v11 | ✅ 合并转发 | 合并转发(Forward/Nodes)可检测;单条消息转发以普通消息到达,平台不提供标记,无法检测 |
| Telegram | ✅ 完整支持 | 官方 API 提供 `forward_origin` 等字段,转发可完整检测 |
| 飞书 | ⚠️ 部分支持 | 普通转发无标记;仅能检测分享类消息(share_chat/share_user)与消息链中的转发段 |

## 常见问题(FAQ)

- **WebUI 打不开**:检查 6185 端口映射与云服务器安全组/防火墙是否放行;容器内不要用 `localhost` 访问,用服务器 IP
- **NapCat 连不上 AstrBot(6199)**:确认 WebSocket 客户端 URL 以 `/ws` 结尾(`ws://astrbot:6199/ws`)、消息格式为 Array、两端 Token 一致;确认 NapCat 与 AstrBot 在同一 compose 网络
- **QQ 收不到消息 / 登录被风控**:建议换养过一段时间的小号;新号扫码后先「热身」,不要立刻发大量消息
- **镜像拉取失败(国内)**:镜像地址加 `m.daocloud.io/docker.io/` 前缀
- **Telegram 无响应**:国内网络需代理,给 astrbot 容器配置 `HTTP_PROXY` / `HTTPS_PROXY` 环境变量
- **`/oc` 提示找不到 opencode**:确认镜像已重建(`docker compose build astrbot && docker compose up -d`),或 `docker exec -it astrbot which opencode` 检查;也可在插件配置中把 `oc_bin` 改为绝对路径

## 安全提醒

- 首次登录 AstrBot WebUI 后**立即修改默认密码**(astrbot/astrbot)
- 不要将 NapCat WebUI(6099)无保护暴露到公网
- 不要将 `.env`、API Key、登录凭证提交到代码仓库
- `oc_auto`(opencode 自动批准权限)默认关闭,请勿在不可信环境中开启
- 定期备份 `data/` 目录(配置、插件、opencode 凭证都在里面)