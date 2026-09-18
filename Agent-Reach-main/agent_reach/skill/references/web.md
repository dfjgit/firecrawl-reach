# 网页阅读

通用网页、RSS。

## 通用网页 (Jina Reader)

```bash
# 读取任意网页内容
curl -s "https://r.jina.ai/URL"

# 示例
curl -s "https://r.jina.ai/https://example.com/article"
```

**适用场景**: 大多数网页可以直接用 Jina Reader 读取。

## firecrawl（站点级抓取，需先启用）

单页阅读链路：Jina Reader → firecrawl → stealth，自动回退，无需人工干预。

```bash
# 启用（一次性）：栈在 deploy/firecrawl/，docker compose up -d 后
agent-reach configure firecrawl-enabled true

# 枚举站点全部 URL
agent-reach map <站点URL> --limit 100

# 整站爬取为 markdown（结果写入 acquisition 数据目录的 JSONL）
agent-reach crawl-site <站点URL> --limit 50

# 批量抓取 URL 清单（每行一个 URL）
agent-reach batch urls.txt

# 搜索（需栈配 SearXNG）
agent-reach search "关键词" --limit 5
```

**适用场景**: 需要整站/批量抓取，或 Jina 读不下来的单页。定期整站源在
sources.yaml 用 `kind: firecrawl_crawl` + 入口 url（见
config/sources.example.yaml）。

**限制**: 自托管栈无反检测，反爬验证由 stealth 兜底；`search` 依赖栈内 SearXNG。

## Web Reader (MCP)

```bash
# 读取网页内容 (Markdown 格式)
mcporter call 'web-reader.webReader(url: "https://example.com")'

# 保留图片
mcporter call 'web-reader.webReader(url: "https://example.com", retain_images: true)'

# 纯文本格式
mcporter call 'web-reader.webReader(url: "https://example.com", return_format: "text")'
```

**适用场景**: 需要更精确控制输出格式时使用。

## RSS (feedparser)

```python
python3 -c "
import feedparser
for e in feedparser.parse('FEED_URL').entries[:5]:
    print(f'{e.title} — {e.link}')
"
```

**适用场景**: 订阅博客、新闻源、播客等 RSS feed。

## 选择指南

| 场景 | 推荐工具 |
|-----|---------|
| 通用网页 | Jina Reader (`curl r.jina.ai`) |
| 整站/批量抓取 | firecrawl（需先启用） |
| 需要图片/格式控制 | web-reader MCP |
| RSS 订阅 | feedparser |
