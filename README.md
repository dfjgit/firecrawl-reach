# agent-reach-firecrawl

AI Agent 上网能力 + 反检测浏览器渲染 + 自托管 Firecrawl 站点级抓取的统一项目。本仓库是
[Agent-Reach](https://github.com/Panniantong/agent-reach) 的本地研究副本，在保留原版全部能力的基础上，
集成了自托管 [Firecrawl](https://github.com/firecrawl/firecrawl) 栈，补齐"站点级广度"能力：整站爬取、
URL 发现、批量抓取、搜索。

架构上分三大模块：

```
Agent-Reach（路由层：平台渠道 / 配置 / 体检 / CLI）
   │  失败回退 / 强制指定
   ▼
渠道后端（web 单页链路：Jina Reader → firecrawl → stealth）
   │  站点级：map / crawl-site / batch / search
   ▼
stealth_kit（反检测层：指纹档案 / 注入 / CDP / 行为 / 会话池 / uTLS 代理）
   │  只用公开 API，不改 Playwright 源码
   ▼
playwright（pip 包，驱动层）┐
Firecrawl 自托管栈（docker）┴→ 浏览器（Chrome / Edge / Chromium）
```

## 目录

| 目录 | 角色 |
| --- | --- |
| `Agent-Reach-main/` | **主项目（运行时）**。含 `agent_reach/`（路由包 + firecrawl 客户端与后端）、`stealth_kit/`（反检测包，顶层独立包）、`mediacrawler/`（移植的 MediaCrawler 子树：抖音/快手/微博/贴吧/知乎登录态抓取）、`acquisition/`（情报采集，含 firecrawl 适配器）、`sidecar/`（Go uTLS 代理：JA3 改写 + proxy chaining）、`deploy/firecrawl/`（自托管 Firecrawl 栈部署）、`detect/`（自检与指纹工具） |
| `playwright-main/` | **Playwright 源码快照（参考用）**。设计阶段的源码勘察依据（`packages/playwright-core`）；含 `stealth-kit/`——stealth 的 TypeScript 冻结参考实现。**运行时不依赖本目录**，浏览器驱动来自 pip 的 `playwright` 包 |
| `docs/` | 集成设计文档（`superpowers/specs|plans`），Firecrawl 整合的原始设计与实现计划 |

## 快速开始

```bash
cd Agent-Reach-main
pip install -e .[browser]        # playwright 在 browser extra 中
python detect/self_check.py      # stealth 链路自检（19 项）
agent-reach doctor               # 渠道体检（含 stealth 兜底 + firecrawl 栈状态）
```

网页读取默认走 Jina/HTTP 直连，渲染/反爬页面自动回退 stealth 浏览器；
`web_backend=stealth` 可强制。详见 `Agent-Reach-main/README.md` 的
"Stealth 浏览器渲染能力"与"Firecrawl 集成能力"章节。

## Firecrawl 集成能力（本仓库相对原版的新增）

| 能力 | 说明 |
| --- | --- |
| 单页中间层 | web 链路变为 Jina → **firecrawl** → stealth；firecrawl 命中反爬特征自动 fall through 到 stealth 兜底 |
| `map` | 枚举站点全部 URL（`agent-reach map <url>`） |
| `crawl-site` | 整站爬取为 markdown（`agent-reach crawl-site <url> [--limit N]`） |
| `batch` | 批量抓取 URL 清单（`agent-reach batch <file>`） |
| `search` | 搜索并抓取结果（`agent-reach search <query>`，需自建 SearXNG） |
| 部署 | `deploy/firecrawl/docker-compose.yaml`：预构建镜像 + 免认证 + Postgres 队列，一键 `docker compose up -d` |
| 配置隔离 | `AGENT_REACH_HOME` 环境变量，副本/多实例独立 config 与数据目录 |

Firecrawl 是**中间层而非替代品**：未启用（`firecrawl_enabled` 默认关）时零开销跳过，栈不可达只把
`doctor` 状态降级为 warn 不阻断主链路。详细用法见 `Agent-Reach-main/deploy/firecrawl/README.md`。

## 副本策略

- 原项目 `E:\Projects\Research\agent-reach-playwright-main` 冻结不动；本仓库是其完整副本 + Firecrawl 集成。
- 两个项目的源码差异仅为 Firecrawl 集成相关文件（客户端、后端、适配器、部署、测试）。

## 备注

- `playwright-main/` 为复制迁入的 Playwright 源码快照（参考用），原始目录确认无误后可手动删除。
- sidecar 二进制构建：Go ≥ 1.23，`cd Agent-Reach-main/sidecar && go build -o bin/tlsproxy ./cmd/tlsproxy`（Windows 下输出 `bin/tlsproxy.exe`）。
- Firecrawl 镜像 tag 用 latest；固定版本请修改 `deploy/firecrawl/docker-compose.yaml` 中三个 ghcr.io 镜像。
