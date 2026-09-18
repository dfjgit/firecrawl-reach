# Firecrawl 自托管栈

Agent-Reach 的 firecrawl 后端 / 站点级命令依赖的本机服务。

## 启动

```bash
cd Agent-Reach-main/deploy/firecrawl
docker compose up -d
```

首次启动需拉取约数 GB 镜像（api / playwright-service / nuq-postgres / redis / rabbitmq）。
就绪后 API 在 http://localhost:3002 （免认证）。

## 接入 Agent-Reach

```bash
agent-reach configure firecrawl-enabled true
# 非默认地址时：agent-reach configure firecrawl-url http://<host>:3002
agent-reach doctor   # web 渠道体检里应看到 firecrawl 栈可达
```

## 常用命令

- 停止：`docker compose down`
- 日志：`docker compose logs -f api`
- 资源：常驻约 2-4GB 内存；不用时建议 down 掉

## 注意

- 自托管版无 Fire-engine stealth / 代理池（云端独占）；反爬由 Agent-Reach 的 stealth 后端兜底。
- `agent-reach search` 需要额外部署 SearXNG 并设置 SEARXNG_ENDPOINT/SEARXNG_ENGINES 环境变量后再 up。
- 镜像 tag 用 latest；要固定版本时把三个 ghcr.io 镜像改成具体 tag。
- 首次 scrape/crawl 可能因 playwright-service 冷启动超过 30s 客户端超时而失败——重试即可（栈热后正常）。需要更长超时时，手编 `~/.agent-reach/config.yaml` 加一行 `firecrawl_timeout: 60`（configure 没有对应键）。
- crawl/batch 结果当前只取任务状态响应的首页 data，不跟进分页（next）——超大站点的 crawl-site 结果可能截断；如需全量请减小 `--limit` 分批抓取，或关注后续版本。
