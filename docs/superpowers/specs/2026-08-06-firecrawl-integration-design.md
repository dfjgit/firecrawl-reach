# Firecrawl × Agent-Reach 整合设计

- 日期：2026-08-06
- 状态：已获用户批准（方案 A）
- 工作目录：`E:\Projects\Research\agent-reach-firecrawl-main`（原项目的完整副本）

## 背景与目标

Agent-Reach 现状强于"平台级深度"（21 个平台渠道路由、stealth_kit 反检测、uTLS JA3 改写），但缺乏"站点级广度"能力：整站爬取、URL 发现、批量抓取、搜索。自托管 Firecrawl（docker compose：api + playwright-service + Redis + RabbitMQ + Postgres）恰好补齐这四项，且免费、数据不出网。

整合方案采用**方案 A：Firecrawl 作为新后端接入 Agent-Reach 路由层**，使其成为路由体系的一等公民。反爬能力仍完全由 stealth_kit 兜底（自托管 Firecrawl 无 Fire-engine，反检测弱于 stealth_kit）。

明确不做（YAGNI）：LLM 结构化提取（/extract 类端点，需额外模型 key）；修改 Firecrawl 内部代码（方案 C，已否决）。

## 1. 副本策略（保护原版）

- 原项目 `E:\Projects\Research\agent-reach-playwright-main` 冻结不动；所有改动只发生在副本 `E:\Projects\Research\agent-reach-firecrawl-main`。
- 副本由 robocopy 整树复制（排除 `.venv`、`__pycache__`、`.pytest_cache`、`.ruff_cache`、`node_modules`），复制后按 SHA256 清单逐文件验证（16938 个文件，0 失败）。
- 整合验收时重跑原目录校验和并与快照比对，证明原版零改动。
- 副本使用独立 venv（副本内重建），不影响原版已安装的 pip 包。
- 用户级状态隔离：`agent_reach/config.py:101` 当前硬编码 `CONFIG_DIR = Path.home() / ".agent-reach"`。需新增环境变量 `AGENT_REACH_HOME` 支持：设置后配置目录与数据目录指向 `$AGENT_REACH_HOME` 而非 `~/.agent-reach`，避免副本与原版共用 config.yaml 和 acquisition state.db。
- 副本非 git 仓库（原项目即无 .git），版本管理不在本期范围。

## 2. 架构总览

```
Agent (Claude Code 等)
  │ SKILL.md（更新：新增站点级能力说明）
  ▼
Agent-Reach 路由层（副本）
  ├─ 单页链路: Jina Reader → Firecrawl /scrape → stealth_kit（反爬兜底不变）
  ├─ 站点级: map / crawl-site / batch / search ──┐
  └─ acquisition: 新增 firecrawl 适配器 ─────────┤
                                                 ▼
                              Firecrawl 自托管栈（docker compose）
                              默认 http://localhost:3002
```

Firecrawl 栈对 Agent-Reach 只是一个 HTTP 服务，通过配置项 `firecrawl_url` + `firecrawl_api_key` 寻址。默认指向自托管；未来切换云端仅改配置，代码不动。

## 3. 新增/改动组件（全部在副本内）

| 组件 | 路径 | 动作 | 说明 |
|---|---|---|---|
| REST 客户端 | `Agent-Reach-main/agent_reach/firecrawl_client.py` | 新增 | 薄客户端，封装 v2 端点 `/scrape`、`/map`、`/crawl`（含任务轮询）、`/batch/scrape`、`/search`。仅用 `requests`（已有依赖），不引入官方 SDK |
| 单页后端 | `Agent-Reach-main/agent_reach/backends/firecrawl.py` | 新增 | 接口与 `backends/stealth.py` 对齐；返回正文 + 元数据；识别反爬拦截类结果并抛可回退异常 |
| web 渠道 | `Agent-Reach-main/agent_reach/channels/web.py` | 修改 | 后端列表变为 `["Jina Reader", "firecrawl", "stealth"]`，顺序可配置 |
| CLI | `Agent-Reach-main/agent_reach/cli.py` | 修改 | 新增子命令 `map <url>`、`crawl-site <url> [--limit N]`、`batch <file>`、`search <query>` |
| acquisition 适配器 | `Agent-Reach-main/acquisition/adapters/firecrawl_site.py` | 新增 | sources.yaml 支持 `type: firecrawl_crawl` 源（入口 URL + 深度/限制）；定期 re-crawl，changeTracking 做增量；落盘沿用 JSONL + SQLite state.db；失败源进现有 blocked 机制 |
| 体检 | `Agent-Reach-main/agent_reach/doctor.py` | 修改 | 新增 Firecrawl 体检项：栈可达性 + `/scrape` 冒烟 |
| 配置 | `Agent-Reach-main/agent_reach/config.py` | 修改 | 新增 `firecrawl_url`（默认 `http://localhost:3002`）、`firecrawl_api_key`（可空）、`firecrawl_enabled`；新增 `AGENT_REACH_HOME` 环境变量支持 |
| Firecrawl 部署 | `Agent-Reach-main/deploy/firecrawl/docker-compose.yaml` | 新增 | 基于上游 compose 精简，固定 ghcr.io/firecrawl/* 镜像版本 |
| Agent 指南 | `Agent-Reach-main/agent_reach/skill/SKILL.md` | 修改 | 补充站点级命令的使用时机与路由规则 |

## 4. 数据流

### 单页链路

`WebChannel.read(url)` → Jina Reader 失败 → firecrawl `/scrape`（返回 markdown，可控 actions）→ 若结果含反爬挑战标记 → 回退 stealth（指纹注入 + JA3 改写，现有能力不变）。

### 站点级链路

`agent-reach crawl-site https://example.com --limit 500` → firecrawl `/crawl` 提交任务 → 客户端轮询至完成 → 逐页 markdown 落盘 acquisition 目录（JSONL + state.db 记录已抓 URL，重跑自动增量）。

### acquisition 定期源

`acquire run` → router 识别 `type: firecrawl_crawl` → 适配器调用 crawl/map → changeTracking 判定增量 → 落盘 + 更新 state.db。

## 5. 错误处理

- Firecrawl 栈不可达/超时：单页链路跳过 firecrawl 直落 stealth 并记日志；站点级命令明确报错并提示启动 docker compose。
- Firecrawl 返回内容含 captcha/cf-challenge 等反爬标记：与 stealth 后端现有检测（`backends/stealth.py:43-50`）同策略，抛可回退异常。
- crawl 任务中途失败：已抓部分保留落盘，源标记进 blocked 清单（复用现有机制）。
- `firecrawl_enabled=false` 或 `firecrawl_url` 未配置时：路由层完全跳过 firecrawl 后端，行为与原版一致。

## 6. 测试与验证

- 单元测试（mock HTTP，不起 docker）：
  - `firecrawl_client` 各端点请求构造与响应解析、轮询逻辑；
  - 后端回退链：Jina 失败 → firecrawl 失败 → stealth 被调用；
  - 反爬标记识别触发回退；
  - acquisition 适配器增量逻辑（changeTracking 判定、state.db 去重）；
  - `AGENT_REACH_HOME` 隔离生效。
- 集成冒烟脚本（仿 `detect/` 风格，`detect/firecrawl_smoke.py`）：检测栈可达后实跑 map + scrape + crawl 各一发；栈不可达则 skip。
- 验收标准：
  1. 副本内 `agent-reach doctor` 全绿；
  2. 单元测试全过；
  3. 原目录重跑校验和与快照一致（零改动证明）。

## 7. 前置依赖

- 本机 Docker（docker compose 可用）——实施时先确认，缺失则提示用户安装 Docker Desktop。
- Firecrawl 镜像从 ghcr.io/firecrawl/* 拉取（首次约需下载数 GB）。

## 8. 实施修订记录（2026-08-06）

实施阶段与本文档的有意偏差，均已通过审查：

- Firecrawl API 用 v1 端点（`_API_PREFIX = "/v1"`，集中一处可切 v2）；本文档写 v2，因上游自托管 e2e 测试覆盖的是 v1。
- doctor 体检未改 `doctor.py`：firecrawl 状态经 `WebChannel.check()` 汇总呈现（doctor 遍历渠道 check 自动生效），功能等价。
- acquisition 适配器文件名为 `acquisition/adapters/firecrawl.py`（非 `firecrawl_site.py`）；源 kind 为 `firecrawl_crawl`。
- 增量去重复用 router/state.db 现有判重（按 URL/标题），未引入 Firecrawl changeTracking——等价简化。
- "crawl 中途失败保留部分结果"未实现：`_wait_job` 遇 failed 直接抛错丢弃部分数据；"失败源进 blocked"对 news lane 实为记 error 下轮重试（与 rss 源既有行为一致）。
- 部署 compose 的 api 端口绑定收紧为 127.0.0.1（免认证栈不暴露内网）。
- 已知限制：crawl/batch 不跟进分页（next），超大站点结果可能截断；scrape 冷启动可能超 30s 超时，重试即可。
