# 采集模块设计（acquisition）

> 状态：设计稿 v2（2026-08-03），已融入对能力层的实测结论，待评审后实施。
>
> 前置：MediaCrawler 已融合进本仓库（`mediacrawler/` 子树 + `agent-reach crawl` 命令 + 5 个新渠道体检）。本模块是**站在 agent-reach-playwright 之上的业务层**，不改动能力层本身。

## 0. v2 的实测依据（这些结论直接改变了设计）

| 假设（v1） | 实测结果 | 设计修正 |
| --- | --- | --- |
| Jina Reader 是通道一主力 | **本机完全不可用**（连 example.com 都返回空，疑似网络/限流） | Jina 降为可选后端；通道一主力改为 **RSS 优先、stealth 直连渲染兜底** |
| 财联社电报免登录可抓 | 页面是 Next.js SPA，HTTP 直连只拿到 JS 壳；nodeapi 也需前端签名 | 此类源走 **stealth 渲染**，`kind: stealth` |
| RSS 源需要验证 | 36氪 RSS 直连实测可用，自带结构化条目 | **有 RSS 的源永远首选 RSS**（L0 级，最稳） |
| MediaCrawler 产物可直接映射作者 | 教学版**匿名化**：`creator_hash`=sha256(user_id)[:16]，昵称中间脱敏，不存 user_id/头像/主页链接 | 作者归属**在源层恢复**（sources.yaml 里有 creator 名字），不 patch vendored 代码 |

## 1. 目标与非目标

**目标**

- 两类数据的统一采集：
  - **通道一（news）**：最新新闻/消息（财联社、RSS、公开网页）。只要"最新"，免登录。
  - **通道二（auth）**：需登录态的"关注的人/圈子"的观点与动态（微博/抖音/知乎创作者、X 时间线、小红书关注）。
- 用户侧操作只有两件：在源注册表里加一条源；登录态失效时按提示重新登录一次。
- 输出统一格式的条目流（JSONL + SQLite 索引），带增量去重，供下游（摘要、推送、Agent 阅读）消费。

**非目标**

- 多账户轮换池（等单账号撞上风控/限流再演进，接口预留）。
- 评论深度挖掘、词云、舆情分析（MediaCrawler 自带能力，按需透传，不进主链路）。
- 实时推送（定时轮询，分钟级延迟可接受）。
- **管道内不做精细内容结构化**（见 §5 的 L2 原则：精细理解交给下游 LLM）。

## 2. 分层架构

```
┌─────────────────────────────────────────────┐
│ acquisition（本模块，新增；无状态 CLI，        │
│  由上游 Agent 定时调用，自身不含调度）          │
│  源注册表 sources.yaml                        │
│  路由器 router → lane 策略 + 引擎优先级         │
│  适配器 adapters（每个引擎一个薄封装）           │
│  抽取 extractors（列表页 → 条目，分档）          │
│  存储 store（统一 schema + 增量去重 + 节流）     │
│  健康 health（登录态失效检测与告警）            │
└──────────────┬──────────────────────────────┘
               │ 只调用，不修改
┌──────────────▼──────────────────────────────┐
│ agent-reach-playwright（能力层，已存在）        │
│  RSS（feedparser）· StealthBackend.read       │
│  twitter-cli · agent-reach crawl（MediaCrawler）│
│  doctor（渠道/登录态体检）· Jina（可选）        │
└─────────────────────────────────────────────┘
```

能力层接口现状：

| 能力 | 调用方式 | 实测状态 |
| --- | --- | --- |
| RSS/Atom | agent_reach RSS 渠道（feedparser） | ✅ 可用（36氪验证） |
| 网页渲染 | `agent_reach.backends.stealth.StealthBackend.read(url)` | ✅ 已具备，本模块的主力兜底 |
| Jina Reader | `https://r.jina.ai/<url>` | ❌ 本机不可用，降为可选 |
| X 时间线/搜索 | `twitter` CLI（子进程，env 注入凭据，从 `~/.agent-reach/config.yaml` 读） | 依赖用户配置 |
| 国内平台登录态 | `agent-reach crawl <dy/ks/wb/tieba/zhihu> --type ...`（另支持 xhs/bili，主项目已有对应渠道），产物在 `mediacrawler/data/<platform>/` | ✅ 已就绪，首次使用需按引导完成平台登录 |
| 登录态体检 | `agent-reach doctor --json` | ✅ 已含 5 个新渠道 |

## 3. 源注册表（sources.yaml）

用户唯一需要维护的东西：

```yaml
- id: cailianshe-telegraph
  name: 财联社电报
  lane: news
  kind: stealth                    # JS 壳页面，必须渲染
  url: https://www.cls.cn/telegraph
  interval: 5m
  max_items: 30

- id: kr36-rss
  name: 36氪
  lane: news
  kind: rss                        # 有 RSS 永远首选
  url: https://36kr.com/feed
  interval: 15m

- id: x-timeline-trump
  name: Trump on X
  lane: auth
  kind: twitter-cli
  command: ["timeline", "--user", "realDonaldTrump", "-n", "20"]
  interval: 30m

- id: weibo-creator-xxx
  name: 某财经博主                # ← 作者归属在这里，不在产物里
  lane: auth
  kind: mediacrawler
  platform: wb
  crawler_type: creator
  creator_id: "1234567890"
  interval: 1h

- id: douyin-creator-yyy
  name: 某抖音创作者
  lane: auth
  kind: mediacrawler
  platform: dy
  crawler_type: creator
  creator_id: "<sec_uid>"
  interval: 2h
```

要点：

- `lane` 决定策略，不是平台决定——同平台可既有免登录源又有登录源。
- `name` 是给人看的归属名；对 mediacrawler 源它同时**充当作者字段**（产物里的昵称是脱敏的，见 §6）。
- `interval` 是**最小抓取间隔（节流）**，不是调度——模块无调度器，`run` 时距上次不足 `interval` 的源自动跳过（`--force` 可覆盖）。同平台登录态源在单轮内**串行 + 抖动**，news 源可并发。
- 可选覆盖：`enabled`、`max_items`、`extra_args`（透传给底层引擎）。

## 4. 通道策略

| | 通道一 news | 通道二 auth |
| --- | --- | --- |
| 引擎优先级 | RSS → stealth 直连 → Jina（可选） | twitter-cli；MediaCrawler |
| 并发 | 可并发（每源独立） | 同平台串行，跨平台最多 2 并发 |
| 重试 | 失败换下一档引擎再试，仍失败记 error | 不重试（避免触发风控），等下一轮 |
| 登录态 | 不需要 | 每轮前查 doctor 缓存；失效 → 源置 `blocked`，告警，跳过 |
| 默认量 | `max_items` 30 | 20，评论默认不抓 |

## 5. 列表页 → 条目：三档抽取策略

通道一的真难点不是"拿到页面"，是"从列表页切出条目"。分档，能用低档不用高档：

- **L0 结构化**：RSS/Atom 自带条目（title/link/pubDate/summary）。零解析成本，永远首选。
- **L1 规则切分**：stealth 渲染出可读文本后，按源配置的轻规则粗切（如电报的时间戳行首正则 `^\d{2}:\d{2}`、`max_items` 截断）。目标是"切得八九不离十"，不追求完美字段。
- **L2 整页透传（默认退路）**：切不动就不切——把渲染后的干净全文作为**一条"页面快照"条目**存下，精细结构化交给下游 LLM 消费时做。管道永不被解析器卡死。

原则：**管道的职责是"可靠地拿到内容 + 粗粒度增量"，精细理解是下游的事。** 每源一个脆弱解析器的路线不维护。

## 6. MediaCrawler 产物映射（实测字段）

以微博为例（`mediacrawler/store/weibo/__init__.py`），jsonl 每条字段：

| 产物字段 | 统一模型字段 | 说明 |
| --- | --- | --- |
| `note_id` | `item_id` | 去重键主成分 |
| `content` | `content` | 已去 HTML 标签 |
| `create_date_time` | `published_at` | 已转北京时间字符串 |
| `note_url` | `url` | |
| `liked_count/comments_count/shared_count` | `metrics`（可选保留） | |
| `creator_hash` | — | 教学版匿名化 sha256 截断，**不映射** |
| `nickname`（脱敏） | — | 首尾各 1 字中间星号，**不映射** |
| — | `author` | **从源注册表 `name` 恢复**（creator 模式下产物全部属于该 creator） |

其他平台（dy/ks/tieba/zhihu/xhs/bili）实施时按各自 `store/<platform>/__init__.py` 补映射表，模式相同。

合规立场：**不 patch vendored 的匿名化代码**。creator 模式下归属在源层，评论里第三方用户脱敏反而是对的（我们默认不抓评论）。

## 7. 统一数据模型与增量去重

```json
{
  "source_id": "weibo-creator-xxx",
  "lane": "auth",
  "platform": "weibo",
  "item_id": "平台内唯一 id（可空）",
  "author": "某财经博主",
  "title": "可空",
  "content": "正文文本",
  "url": "原文链接",
  "published_at": "字符串原样保留",
  "fetched_at": "ISO8601",
  "metrics": {"liked": "12", "comments": "3"},
  "snapshot": false
}
```

- **去重键**：`platform + item_id`；无 item_id（L1/L2 条目）时为 `sha1(归一化全文)`（连续空白折叠为单空格，截断 2000 字符）。纯 url 不可靠（列表页 url 不变）；只哈希前 200 字符也不可靠（列表页条目常共享日期头/栏目前缀，会把不同条目误杀——财联社电报实测 11→2）。
- **存储**：JSONL 追加（`~/.agent-reach/acquisition/data/<source_id>.jsonl`）+ SQLite seen 索引（`seen(item_key PRIMARY KEY, source_id, first_seen_at)`）。不引 ORM。
- **baseline 语义**：源首次运行抓到的条目只建 seen 不记为"新"（否则首跑就是一次洪水）；之后每轮 `new_items` = 命中未见过去重键的条目。
- **watermark**：每源额外记 `last_published_at` 仅用于展示和调参，**不参与去重判断**（时间戳不可靠，去重只认键）。

## 8. 登录态生命周期

1. **加源**：`lane: auth` 时 CLI 打印对应引导（X → Cookie-Editor + `agent-reach configure twitter-cookies`；国内平台 → `agent-reach crawl <p> --lt qrcode` 扫码一次）。
2. **每轮前**：读 `agent-reach doctor --json`（进程内缓存 5 分钟）；源所属渠道非 `ok` → 跳过 + 置 `blocked`。
3. **抓取中识别失效**：twitter-cli 返回 auth 错误 / MediaCrawler 产物为空且日志含登录跳转 → 置 `blocked`。
4. **告警与恢复**：`blocked` 源写入 `~/.agent-reach/acquisition/blocked.json`，每轮结束打印；用户重新登录后下一轮 doctor 转 `ok` 自动恢复。

## 9. 调用与消费（无内置调度）

**本模块不含调度器。** 定时触发由上游 Agent 负责（Agent 的定时任务周期性执行 `acquire run`）。模块本身是无状态 CLI：被调用一次，跑一轮，退出。

- `acquire run [--source <id>] [--force]`：跑一轮。`--force` 忽略最小间隔限制。
- **最小间隔节流（防风控，不是调度）**：`sources.yaml` 里的 `interval` 仅作为"同一源两次抓取的最小间隔"生效——`run` 时距上次抓取不足 `interval` 的源自动跳过。这样 Agent 可以用固定频率（如每 5 分钟）无脑调 `run`，而登录态源仍保持低频。每源的 `last_fetched_at` 记在 SQLite 里。
- `acquire latest [--source <id>] [-n 20] [--since 1h]`：查新条目，给 Agent/人直接消费。
- 每轮结束打印汇总：`source → new N / dup M / error / blocked / skipped`，同时写 `~/.agent-reach/acquisition/runs.log`。
- 推送（Server酱/webhook/邮件）留扩展点，不进核心。

## 10. 落地形态

新增顶层包 `acquisition/`（与 `agent_reach/` 平级，进 `pyproject.toml`）：

```
acquisition/
  __init__.py
  sources.py       # sources.yaml 加载与校验（dataclass，不引 pydantic）
  router.py        # lane 策略、引擎优先级、并发控制、interval 节流
  models.py        # Item + 去重键 + baseline
  store.py         # JSONL + SQLite seen 索引（含 last_fetched_at）
  health.py        # doctor 缓存 + blocked 管理
  extractors.py    # L0/L1/L2 三档抽取
  adapters/
    base.py        # Adapter 协议：fetch(source) -> list[Item]
    rss.py         # L0
    stealth.py     # L1/L2，封装 StealthBackend.read
    jina.py        # 可选后端
    twitter_cli.py
    mediacrawler.py
  cli.py           # acquire run / latest / sources
```

适配器协议只有一个 `fetch()`；新引擎 = 新增一个文件，不动核心。

## 11. 里程碑

- **M1**（✅ 已完成 2026-08-03）：`sources/models/store/extractors/router` + `rss.py`/`stealth.py` + `acquire run/latest` —— 通道一跑通；36氪 RSS 实测 baseline/节流/增量全链路验证（首跑 baseline 30 条、二跑节流 skipped、强制三跑捕获 1 条新增）。财联社 stealth 源待本机 playwright 浏览器就绪后实测。
- **M2**（✅ 已完成 2026-08-03）：`mediacrawler.py` + 7 平台字段映射（wb/dy/ks/bili/tieba/zhihu/xhs，均已对照 store 实现核验）+ `health.py` blocked 写入 + 渠道体检前置 + auth 串行抖动 + `acquire blocked/unblock`。**待用户首次登录后实测**（当前全部 mock/fixture 验证）。
- **M3**（✅ 已完成 2026-08-03）：`twitter_cli.py`（--json 强制结构化输出、凭据子进程注入不污染环境、认证失效识别）+ run 末尾 blocked 告警提示闭环。**待用户配置 twitter 凭据后实测**（当前全部 mock 验证）。
- **M4**（可选）：Jina 适配器（网络通的环境）、多账户池接口预留、推送扩展。

## 12. 风险与合规

- 通道二所有平台均违反各自 ToS，封号是常态：`blocked` 是一等状态，单账号、低频、不并发是基本姿态。
- `mediacrawler/` 子树为 NON-COMMERCIAL LEARNING LICENSE，本模块通过子进程调用，商用场景需另行评估。
- 登录态分散两处（`~/.agent-reach/config.yaml`、`mediacrawler/browser_data/`），本模块只读不写；写操作一律引导用户走已有官方命令。
- MediaCrawler 教学版的匿名化是特性不是障碍：creator 归属在源层恢复，第三方用户保持脱敏。
