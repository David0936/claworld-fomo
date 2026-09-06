# Fomo 本机监控台

[![GitHub](https://img.shields.io/badge/GitHub-claworld--fomo-606AF7)](https://github.com/David0936/claworld-fomo)

打开 http://127.0.0.1:8765。Python 标准库实现，无需安装第三方依赖。

双击项目根目录「打开Fomo监控台.command」打开页面。已配置 macOS 登录启动服务时，关闭网页不影响推送；退出系统、电脑睡眠或关机后不能继续执行。Codex 与行情浏览器需保持开启和登录。

## 飞书与 Telegram 接入

连接方式对齐旧工具 [Claworld Monitor](https://github.com/David0936/claworld-x-monitor)：飞书使用自定义机器人 Webhook + 可选签名密钥；Telegram 使用 Bot Token + Chat ID。两个通道可以同时启用。

飞书群推送：飞书群 → 设置 → 群机器人 → 添加自定义机器人 → 将 Webhook 填入页面。如果开启签名校验，同时填写签名密钥；如果开启关键词校验，应允许“Fomo”。点击保存，再点击“测试飞书推送”。这种方式无需 App ID、App Secret 或账号 OAuth 登录。

Telegram：找 @BotFather 创建机器人并取得 Bot Token，填入并保存。私聊接收可点击“Telegram 登录 / 绑定本人”，打开生成的机器人链接，在 Telegram 中点击 Start，再回到网页点击“完成验证”。系统只接受该次随机验证码对应的近期私聊消息，并自动绑定发送人的 Chat ID。此功能是通过机器人验证 Telegram 账号并登录本机工作台，不是 Telegram 网站 OAuth；无需公开域名或网页回调地址。验证链接10分钟有效，请勿分享。

如果已有旧工具的 Chat ID，可以直接填写。群聊填写负数 Chat ID；频道可填 @频道用户名，并确保机器人有发送权限。仅填 Bot Token 不能自动向你发送私信，需要主动开始会话或填写可用接收会话。机器人已配置 Webhook、被其他程序消费更新或积压太多消息时，验证登录可能无法取到本次消息，请手动填 Chat ID；本程序不会清空机器人更新或删除现有 Webhook。

“测试飞书推送”和“测试 Telegram 推送”分别测试对应通道。消息记录显示每个通道独立的成功/失败；仅重试失败通道，已成功的通道不会因另一通道失败而重发。发送目标在该事件第一次进入投递流程时确定；后来新增通道不会自动重放已发送事件。暂停某通道会保留它已有的待发送记录，恢复后继续发送。

高级设置保留飞书账号登录与个人推送：在 https://open.feishu.cn/app 创建企业自建应用，启用机器人，申请 `im:message:send_as_bot`，配置回调地址 `http://127.0.0.1:8765/auth/callback`，发布并将本人加入应用可用范围。在高级设置填写 App ID / App Secret，保存并登录。已配置群 Webhook 时优先发群。

此页面只在本机监听，操作系统登录用户可管理本地连接；不是面向公网的多用户网站。不要将8765端口转发到公网。

## 执行方式与可用性

- 原 Codex 自动化负责 GMGN 多链热搜和 Windvane 实际核验，5分钟请求运行一次；受任务执行时长、Codex调度及平台配额影响，并非精确5分钟一次。沿用 Codex 的账户额度。
- 网页每5秒读取已生成结果，不会单凭页面刷新声称查询了行情。
- 按钮将即时核验请求加入原 Codex 任务队列，以扫描状态及实际观测时间判断是否完成。
- 新命中经过后端阈值二次检查，立刻写入 SQLite 队列，后台每2秒检查待发队列，网络请求耗时可能延长实际间隔。既有生命周期及模拟策略仍每2小时采样。
- 同一事件ID只入队一次；网络失败指数退避，最长5分钟后重试。平台已收但响应丢失时可能重发，属于至少一次投递，事件编号可用于识别重复。
- 未配置任何消息通道时事件保留；配置后会补发待发送事件，保留原观测时间。测试消息明确标记为测试。
- 历史 Markdown 档案只展示，不自动转换成“新命中”。
- 页面超过20分钟没有扫描状态更新会标为数据过期。运行失败/登录过期/查询额度不足由监测任务报告。

## 维护

运行文件和数据位于 `~/Library/Application Support/FomoMonitor/`，避免后台服务受 Documents 目录访问限制。配置在 `~/Library/Application Support/FomoMonitor/data/config.json`，权限600，不要公开。队列为 `~/Library/Application Support/FomoMonitor/data/monitor.sqlite3`。不要将 data 目录提交到版本库。

测试：在 monitor 目录运行 `python3 -m unittest discover -v`。所有发送测试均使用 mock，真实飞书与 Telegram 收件需在配置凭证后通过页面测试按钮验证。

服务：`~/Library/LaunchAgents/club.local.fomo-monitor.plist`。暂停服务可执行 `launchctl bootout gui/$(id -u) ~/Library/LaunchAgents/club.local.fomo-monitor.plist`；重新加载用 `launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/club.local.fomo-monitor.plist`。服务退出自动重启，登录自动加载。只防止系统空闲睡眠，不改变关机、合盖或用户主动睡眠行为。

源码更新后执行 `python3 monitor/install.py` 将代码同步到常驻服务并重启。监测任务每次写入扫描状态时同步历史档案到服务的数据目录。

工作台每6小时读取 GitHub 仓库的 `VERSION` 检查更新，也可以在“消息连接 → 项目与更新”中手动检查。检查只比较语义化版本并提示，不会自动下载或覆盖本机代码、数据库与连接凭证。安装新版本前阅读 [CHANGELOG.md](CHANGELOG.md)，拉取源码后重新执行安装脚本。

原自动化对接说明见 INTEGRATION.md。

Telegram 长文会按4096字符分段。中途某段失败后再次尝试，已成功的前段可能重复；事件编号和原观测时间会保留。

## 品牌界面与本地入口

新版采用用户上传 Logo 与 Fomo 官方配色，提供信号总览、监测档案、消息连接三个视图。信号列表可按强信号/观察/待发送筛选，支持名称或 CA 搜索、复制完整 CA、展开原始核验详情。数字为首次观测值，不是实时行情。

双击项目根目录 `Fomo 本地监控台.html`，再点击“打开本地监控台”。该文件内嵌Logo和样式，无外部字体或图片依赖；实际功能由现有本机后台提供。三个阅读密度选项会在浏览器中记住。旧版界面备份位于 `monitor/design-v1/`。


## 100U 模拟观察表

参见 [PORTFOLIO.md](PORTFOLIO.md)。每个标的从首次核验合格的观测价独立投入100U，回放三套策略（模型3含100U/110U两分支），逐笔扣买卖成本。按各标的所在链和每笔交易金额计算官方平台费；额外费用未核验时不冒充实测总成本。已付费用与剩余仓位预估卖出费分开展示。无后续报价时显示待采样，不计入当前收益汇总。历史50U日志保留，页面为独立100U回放账本。


## 当前费用规则（取代此前统一6%假设）

按每个标的所在链采用当前官方平台手续费规则（2026-09-06核查：https://help.fomo.family/en/articles/14436214-trading-fees-on-fomo）：BSC/BNB、Robinhood、Base、Monad、Ethereum每笔0.5%，网络费另计；Solana单笔<5U固定0.10U、5–47.5U为2%、47.5–190U固定0.95U、≥190U为0.5%，Solana网络费由平台承担。每次分批卖出按实际模拟卖出金额重新计费，回本按平台费后净收金额反解。未核验的网络费、代币买卖税、池费、滑点及价格冲击不能宣称已计入，也不能标为实测0。未验证邀请码或特殊代币优惠时不应用优惠。展示为平台费后估算收益，非全部费用后的真实盈亏。
采样时逐个检查该关注对象可见的费用信息；将官方规则、网络费/买卖税是否可核验及来源URL写入sample.note，未知明确写未知，严禁使用买入/卖出按钮执行交易来核验。页面默认由引擎按链和逐笔金额计算平台费；其他费用没有可靠证据时单列未计入。

## 开发者

**David小鱼**

- 微信：dragon-yu-171728
- 公众号：自家的鱼鱼 / Claworld
- X：[Shark1996_](https://x.com/shark1996_)
- YouTube：[@Singularity2026](https://www.youtube.com/@Singularity2026)
- 小红书：[David小鱼](https://xhslink.com/m/6WBQosGc8F6)
