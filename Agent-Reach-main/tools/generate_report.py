# -*- coding: utf-8 -*-
"""情报采集报告生成器：读采集 JSONL → 知识图谱命中检测 → 生成分析报告。

用法：
    python tools/generate_report.py               # 全部数据
    python tools/generate_report.py --since 6h    # 最近 6 小时
    python tools/generate_report.py --out report.html

规范见 docs/report-format.md（v0.1，迭代中）。
"""
import argparse
import email.utils
import hashlib
import html
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

# LLM 并发解读：每条新闻一个任务，线程池并行调用（每个调用内部是子进程隔离）。
# 并发数可调：默认 4（DeepSeek API 有并发限制，过高会触发限流/超时）
LLM_MAX_WORKERS = int(os.environ.get("LLM_MAX_WORKERS", "4"))

# LLM 解读缓存：同一新闻（item_id + 内容 hash 不变）不重复调 LLM
# 缓存文件：~/.agent-reach/cache/llm_interpret.json
# 生命周期（2026-08-05 完善）：
#   ① TTL：fetched_at 超过 7 天自动清理（覆盖最长常规报告窗口 7d 周报；
#      7 天前的新闻不会再被任何报告处理，缓存无意义）
#   ② 容量：上限 2000 条按 created_at 淘汰最旧（防异常膨胀）
#   ③ 版本：模型/解读引擎变更时整体作废（防旧版本解读残留）
CACHE_DIR = Path.home() / ".agent-reach" / "cache"
LLM_CACHE_FILE = CACHE_DIR / "llm_interpret.json"
CACHE_TTL_DAYS = 7
CACHE_MAX_ITEMS = 2000
_LLM_CACHE: dict = {}
_LLM_CACHE_LOADED = False


def _cache_version() -> str:
    """缓存版本指纹：旗舰模型 + 轻量模型 + 引擎版本。

    任一模型变更时指纹变化，加载缓存时检测不匹配则整体作废
    （深度解读缓存绑旗舰，轻量筛选缓存绑轻量，2026-08-06 双模型）。
    """
    try:
        from llm_interpret import get_config
        cfg = get_config()
        model = cfg.get("model", "unknown")
        lite_model = cfg.get("lite_model", "unknown")
    except Exception:
        model, lite_model = "unknown", "unknown"
    return f"{model}|{lite_model}|interpret-v2"


def _load_llm_cache() -> dict:
    """加载 LLM 解读缓存（懒加载，含版本校验 + TTL 清理）。"""
    global _LLM_CACHE, _LLM_CACHE_LOADED
    if not _LLM_CACHE_LOADED:
        try:
            if LLM_CACHE_FILE.exists():
                data = json.loads(LLM_CACHE_FILE.read_text(encoding="utf-8"))
            else:
                data = {}
        except Exception:
            data = {}
        version = data.get("version", "")
        items = data.get("items", {}) if isinstance(data, dict) else {}
        if version != _cache_version():
            # 引擎/模型版本变更 → 缓存整体作废
            items = {}
        _LLM_CACHE = items
        _LLM_CACHE_LOADED = True
    return _LLM_CACHE


def _save_llm_cache() -> None:
    """保存 LLM 解读缓存（TTL 清理 + 限容 + 版本头）。"""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache = _LLM_CACHE
        # ① TTL 清理：剔除超过 7 天的条目（fetched_at 缺失时用 created_at 兜底）
        cutoff = (datetime.now(timezone.utc) - timedelta(days=CACHE_TTL_DAYS)).isoformat()
        stale = []
        for k, v in cache.items():
            ts = v.get("fetched_at") or v.get("created_at") or ""
            if ts and ts < cutoff:
                stale.append(k)
        for k in stale:
            cache.pop(k, None)
        # ② 限容：超过上限按 created_at 淘汰最旧
        if len(cache) > CACHE_MAX_ITEMS:
            items = sorted(cache.items(), key=lambda kv: kv[1].get("created_at", ""))
            cache = dict(items[-CACHE_MAX_ITEMS:])
            _LLM_CACHE.clear()
            _LLM_CACHE.update(cache)
        payload = {"version": _cache_version(), "items": cache}
        LLM_CACHE_FILE.write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass


def _item_cache_key(item: dict) -> str:
    """条目缓存键：source_id:item_id；无 item_id 用 URL 或内容 hash。"""
    src = item.get("source_id") or item.get("src_id") or "src"
    iid = item.get("item_id") or ""
    if not iid:
        iid = item.get("url") or ""
    if not iid:
        iid = hashlib.md5((item.get("title") or "")[:60].encode("utf-8")).hexdigest()[:12]
    return f"{src}:{iid}"


def _item_content_hash(item: dict) -> str:
    """条目内容哈希：标题+内容，内容变更则缓存失效。"""
    text = (item.get("title") or "") + "|" + (item.get("content") or "")[:200]
    return hashlib.md5(text.encode("utf-8")).hexdigest()[:16]


# 缓存命中统计（报告结束输出，观察去重效果）
CACHE_STATS = {"hit": 0, "miss": 0}


def _cached_interpret(item: dict, hits: list, ctx: str) -> str:
    """带缓存的 LLM 深度解读。

    命中缓存（同 key + 同内容 hash + 同图谱上下文）→ 直接返回缓存；
    否则调 LLM 并写回缓存。LLM 未配置/失败仍回退模板（不缓存模板结果）。
    """
    cache = _load_llm_cache()
    key = _item_cache_key(item)
    content_hash = _item_content_hash(item)
    cached = cache.get(key)
    if cached and cached.get("hash") == content_hash and cached.get("ctx") == ctx:
        CACHE_STATS["hit"] += 1
        return cached.get("interpret", "")
    CACHE_STATS["miss"] += 1
    deep = _llm_deep_interpret(item, hits, ctx)
    if deep:  # 只有真实 LLM 解读才缓存；模板/失败不缓存
        cache[key] = {
            "hash": content_hash,
            "ctx": ctx,
            "interpret": deep,
            "fetched_at": item.get("fetched_at", ""),  # TTL 依据：采集时间
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        _save_llm_cache()
    return deep


# 轻量筛选缓存（2026-08-06 分层第②层）：同 LLM 解读缓存文件，key 加 lite: 前缀
# 生命周期与解读缓存一致（TTL 7 天 + 版本指纹），筛选结果 0/1 存储
LITE_STATS = {"hit": 0, "miss": 0}


def _cached_lite_worth(item: dict, hits: list, ctx: str) -> bool:
    """带缓存的轻量模型价值筛选：True=值得深度解读。

    命中缓存（同 key + 同内容 hash + 同图谱上下文）→ 直接返回；
    否则调轻量模型并写回缓存。LLM 未配置/失败返回 True（召回优先）。
    """
    cache = _load_llm_cache()
    key = "lite:" + _item_cache_key(item)
    content_hash = _item_content_hash(item)
    cached = cache.get(key)
    if cached and cached.get("hash") == content_hash and cached.get("ctx") == ctx:
        LITE_STATS["hit"] += 1
        return bool(cached.get("worth", True))
    LITE_STATS["miss"] += 1
    try:
        from llm_interpret import lite_worth_deep
        worth = lite_worth_deep(item, hits, ctx)
    except Exception:
        worth = True  # 召回优先：筛选失败放行
    cache[key] = {
        "hash": content_hash,
        "ctx": ctx,
        "worth": worth,
        "fetched_at": item.get("fetched_at", ""),
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _save_llm_cache()
    return worth

# 知识图谱（money 项目）可导入时启用命中检测；否则降级为关键词本地匹配
try:
    sys.path.insert(0, str(Path(r"E:\WorkBuddy\money").resolve()))
    from knowledge_graph import KnowledgeGraph

    # 显式指定数据目录（默认回退到相对 cwd 的 knowledge/，从其他目录
    # 运行会加载到空图导致命中率 0%）
    _KG = KnowledgeGraph(data_dir=r"E:\WorkBuddy\money\knowledge")
    KG_AVAILABLE = True
except Exception as _e:  # pragma: no cover
    _KG = None
    KG_AVAILABLE = False

DATA_DIR = Path.home() / ".agent-reach" / "acquisition" / "data"

# ── 时间处理：事件发生时间 / 发布时间 / 采集时间 三时间点 ──
# 2026-08-05 新增：区分"事件发生日期"与"报道日期"，计算报道滞后，
# 评估分析时效价值（消息抢跑场景：周一发生周五报道，市场已定价）


def parse_published_at(item: dict) -> str:
    """解析 published_at 为 UTC ISO 字符串（'' 表示无）。

    兼容 RFC822（'Wed, 05 Aug 2026 00:17:13 GMT'）与 ISO（'2026-08-03 17:45:26 +0800'）。
    """
    raw = (item.get("published_at") or "").strip()
    if not raw:
        return ""
    # ISO 格式：'2026-08-03 17:45:26  +0800'（空格+时区）
    try:
        s = re.sub(r"\s+", " ", raw).replace(" ", "T", 1)
        t = datetime.fromisoformat(s)
        return t.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    # RFC822 / RFC1123：'Wed, 05 Aug 2026 00:17:13 GMT'
    try:
        t = email.utils.parsedate_to_datetime(raw)
        return t.astimezone(timezone.utc).isoformat()
    except Exception:
        return ""


def extract_event_time(item: dict) -> str:
    """提取事件发生时间（UTC ISO，'' 表示无法确定）。

    优先级：财联社电报时间戳（HH:MM + 正文"X月X日"，北京时间转 UTC）→
    正文"X月X日" → published_at 兜底。
    2026-08-06 修复：原逻辑只匹配时间戳就取"当天"，丢失"8月3日"日期信息；
    且财联社时间为北京时间（GMT+8），需转 UTC 再参与滞后计算。
    """
    content = item.get("content") or ""
    title = item.get("title") or ""
    text = content[:300] + " " + title[:100]
    now = datetime.now(timezone.utc)
    # 财联社/电报时间戳（如"10:51:28财联社8月3日电"；24 小时制，含 20-23 点）
    m_time = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?::[0-5]\d)?", text)
    m_date = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m_time and ("财联社" in text or "电报" in text or "电" in text):
        try:
            hh, mm = int(m_time.group(1)), int(m_time.group(2))
            if m_date:
                # 正文带日期：北京时间 → UTC
                cst = timezone(timedelta(hours=8))
                ev_local = datetime(now.year, int(m_date.group(1)), int(m_date.group(2)),
                                    hh, mm, tzinfo=cst)
                if 0 <= (now - ev_local.astimezone(timezone.utc)).days <= 30:
                    return ev_local.astimezone(timezone.utc).isoformat()
            # 无日期：取当天北京时间
            cst = timezone(timedelta(hours=8))
            ev_local = datetime(now.year, now.month, now.day, hh, mm, tzinfo=cst)
            return ev_local.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    # 2. 正文"X月X日"（常见于中文新闻，如"8月3日"）
    m = re.search(r"(\d{1,2})月(\d{1,2})日", text)
    if m:
        try:
            ev = now.replace(month=int(m.group(1)), day=int(m.group(2)),
                             hour=0, minute=0, second=0, microsecond=0)
            # 只接受近 30 天内（防"5月1日"匹配去年）
            if 0 <= (now - ev).days <= 30:
                return ev.isoformat()
        except ValueError:
            pass
    # 3. 兜底：用发布时间
    return parse_published_at(item)


def _tz_aware(ts: str) -> datetime | None:
    """解析 UTC ISO 字符串为 aware datetime；失败返回 None。"""
    try:
        return datetime.fromisoformat(ts)
    except Exception:
        return None


# ── 新闻种类分类（2026-08-06 新增） ──
# 不同种类的新闻时效窗口差异巨大：
# - 股价/行情类：盘中信息 4h 即过期，收盘后基本无交易价值（用户指出"9h 还能叫新鲜？"）
# - 财报类：发布后 24h 内是主要消化窗口
# - 政策类：发布后数天-数周持续影响
# - 宏观数据类：发布日窗口
# - 产业/公司动态类：1-3 天
# 命中实体类型与文本关键词共同判定种类，分级用不同阈值。
_QUOTE_KW = ("股价", "涨停", "跌停", "涨超", "跌超", "现报", "收报", "报", "市值",
             "上涨", "下跌", "走高", "走低", "大涨", "大跌", "反弹", "回落",
             "盘前", "盘中", "盘后", "开盘", "收盘", "转涨", "转跌", "震荡",
             "涨幅", "跌幅", "涨逾", "跌逾", "每股", "美元/股", "港币/股", "元/股",
             "stock", "shares", "trading", "rally", "tumble", "surge", "slide",
             "up", "down", "percent", "jump", "drop", "gain", "loss")
_EARNINGS_KW = ("财报", "业绩", "营收", "净利", "净利润", "归母", "每股收益", "EPS",
                "earnings", "revenue", "profit", "loss", "quarter", "fiscal",
                "guidance", "forecast", "outlook", "results", "income",
                "财报季", "季报", "年报", "中报", "一季报", "三季报")
_POLICY_KW = ("政策", "规划", "印发", "国务院", "发改委", "工信部", "央行", "证监会",
              "降准", "降息", "利率", "LPR", "补贴", "意见", "通知", "纲要",
              "十五五", "行动计划", "实施方案", "监管", "regulation", "policy",
              "fed", "rate", "tariff", "export control", "subsidy")
_MACRO_KW = ("CPI", "PMI", "GDP", "PPI", "非农", "失业率", "通胀", "经济数据",
             "inflation", "gdp", "cpi", "jobs report", "unemployment")


def news_kind(item: dict) -> str:
    """判定新闻种类：quote行情 / earnings财报 / policy政策 / macro宏观 / industry产业。

    优先级：财报 > 政策 > 宏观 > 行情 > 产业（财报新闻往往也含股价词，先判财报避免误归行情）。"""
    text = ((item.get("title") or "") + " " + (item.get("content") or "")).lower()
    text = _clean_text(text, 400)
    if any(k in text for k in _EARNINGS_KW):
        return "earnings"
    if any(k in text for k in _POLICY_KW):
        return "policy"
    if any(k in text for k in _MACRO_KW):
        return "macro"
    if any(k in text for k in _QUOTE_KW):
        return "quote"
    return "industry"


# 各类时效阈值（小时）：🟢新鲜 ≤ g1 / 🟡滞后 ≤ g2 / 🔴已抢跑 > g2
# 2026-08-06 用户定稿：行情 30min/1h/2h；其余全部统一 12h/24h/36h
_FRESH_THRESH = {
    "quote":    {"g1": 0.5, "g2": 1,  "g3": 2},   # 股价：30min 内新鲜，1h 滞后，2h 已抢跑
    "earnings": {"g1": 12,  "g2": 24, "g3": 36},  # 财报
    "policy":   {"g1": 12,  "g2": 24, "g3": 36},  # 政策
    "macro":    {"g1": 12,  "g2": 24, "g3": 36},  # 宏观
    "industry": {"g1": 12,  "g2": 24, "g3": 36},  # 产业
}


def event_lag_info(item: dict) -> dict:
    """计算事件-报道-采集的时间差，返回时效分级信息。

    滞后优先级（2026-08-05 跨源聚类升级）：
    1. 事件全局首报（跨源）→ 本条发布时间：最准确，反映"该事件最早被报道"
    2. 事件时间（正文提取）→ 本条发布时间
    3. 本条发布时间 → 采集时间（近似）
    时效分级按新闻种类（2026-08-06）：quote/earnings/policy/macro/industry 各自阈值。
    """
    event_ts = extract_event_time(item)
    pub_ts = parse_published_at(item)
    first_ts = event_first_pub(item)  # 跨源事件首报
    fetch_raw = item.get("fetched_at", "")
    now = datetime.now(timezone.utc)

    ev_dt = _tz_aware(event_ts)
    pub_dt = _tz_aware(pub_ts)
    first_dt = _tz_aware(first_ts)
    fet_dt = _tz_aware(fetch_raw)

    # 报道滞后：优先 本条发布时间 - 事件全局首报（跨源）
    if first_dt and pub_dt and first_dt <= pub_dt:
        lag_hours = (pub_dt - first_dt).total_seconds() / 3600
        lag_type = "first_to_pub"
    elif ev_dt and pub_dt:
        lag_hours = (pub_dt - ev_dt).total_seconds() / 3600
        lag_type = "event_to_pub"
    elif pub_dt and fet_dt:
        lag_hours = (fet_dt - pub_dt).total_seconds() / 3600
        lag_type = "pub_to_fetch"
    elif ev_dt:
        lag_hours = (now - ev_dt).total_seconds() / 3600
        lag_type = "event_to_now"
    else:
        lag_hours = 0
        lag_type = "unknown"

    # 未来时间兜底（时区/提取错误导致 lag 为负）：按 0 计
    if lag_hours < 0:
        lag_hours = 0
        lag_type = "future_clamped"

    # 时效分级：按新闻种类各自阈值（2026-08-06）
    kind = news_kind(item)
    th = _FRESH_THRESH.get(kind, _FRESH_THRESH["industry"])
    if lag_hours <= th["g1"]:
        freshness = "🟢新鲜"
    elif lag_hours <= th["g2"]:
        freshness = "🟡滞后"
    else:
        freshness = "🔴已抢跑"

    return {
        "event_time": event_ts[:10] if event_ts else "",
        "first_pub": first_ts[:10] if first_ts else "",
        "published_at": pub_ts[:10] if pub_ts else "",
        "fetched_at": fetch_raw[:10] if fetch_raw else "",
        "lag_hours": round(lag_hours, 1),
        "lag_type": lag_type,
        "freshness": freshness,
        "kind": kind,
    }


def fmt_time_line(item: dict) -> str:
    """渲染时间信息行：📅 事件:… | 首报:… | 报道:… | 滞后:…"""
    info = event_lag_info(item)
    parts = []
    if info["event_time"]:
        parts.append(f"事件:{info['event_time']}")
    if info["first_pub"]:
        parts.append(f"首报:{info['first_pub']}")
    if info["published_at"]:
        parts.append(f"报道:{info['published_at']}")
    if info["fetched_at"]:
        parts.append(f"采集:{info['fetched_at']}")
    lag = info["freshness"]
    if info["lag_hours"]:
        lh = info["lag_hours"]
        # <1h 用分钟显示（行情类 30min 阈值需要分钟精度），否则显示小时
        if lh < 1:
            lag += f"({lh * 60:.0f}min)"
        else:
            lag += f"({lh:.0f}h)"
    parts.append(f"时效:{lag}")
    if info.get("kind"):
        parts.append(f"类型:{info['kind']}")
    return " | ".join(parts)


# ── 跨源事件聚类：全局首报时间 ──
# 2026-08-05 新增：单条新闻的发布时间不能代表事件首报（首报可能在其他源更早）。
# 按事件指纹词聚类全库，取每簇最早 published_at 作为"事件首报"，
# 每条新闻的报道滞后 = 本条发布时间 − 事件全局首报时间。

# 指纹提取用的事件核心词（排除过泛词，自包含不依赖 STOPWORDS）
_FINGERPRINT_STOP = {
    "上涨", "下跌", "走高", "走低", "反弹", "回落", "新高", "新低", "市场",
    "报告", "数据", "新闻", "消息", "交易", "投资", "科技", "金融", "经济",
    "电子", "汽车", "AI",
    "发布", "宣布", "推出", "上线", "公布", "报道", "表示", "称", "今日", "昨日",
    "本周", "上周", "本月", "上月", "公司", "企业", "行业", "板块",
    "记者", "电", "日", "月", "年", "以及", "已经", "正在", "将会",
    "中国", "美国", "全球", "世界", "国际", "国内", "首个", "最大", "最新",
}

# 指纹词 → 全库最早 published_at（构建一次，全局复用）
_EVENT_FIRST_MAP: dict[str, str] = {}
# 指纹词 → 涉及的不同源集合（2026-08-06 新增，供价值打分：跨源数 = 热度）
_FINGERPRINT_SOURCES: dict[str, set] = {}


def _item_fingerprint(item: dict) -> set[str]:
    """提取事件指纹词：标题+正文中的核心实体（英文专名 + 中文 3-6 字词）。

    用于跨源聚类——同一事件的新闻应共享指纹词。
    """
    text = (item.get("title") or "") + " " + (item.get("content") or "")
    text = _clean_text(text, 200)
    fings: set[str] = set()
    # 英文专名：DeepSeek / OpenAI / NVIDIA / HBM4 / V4 Flash
    for m in re.finditer(r"[A-Za-z][A-Za-z0-9.\-]{2,20}", text):
        w = m.group(0)
        if w.lower() in ("the", "and", "for", "not", "with", "this", "that"):
            continue
        fings.add(w)
    # 中文 3-6 字连续词（去停用）
    for m in re.finditer(r"[\u4e00-\u9fa5]{3,6}", text):
        w = m.group(0)
        if w in _FINGERPRINT_STOP:
            continue
        # 过滤含叙事/通用后缀的
        if re.search(r"(公司|集团|股份|板块|指数|期货|市场|报告|新闻|日报|周报|"
                     r"今日|昨日|本周|上周|宣布|发布|表示|报道|日电)", w):
            continue
        fings.add(w)
    return fings


def build_event_first_map(sources: dict) -> None:
    """遍历全库构建 指纹词 → 最早 published_at 映射 + 跨源统计。

    每簇首报时间 = 该事件词在所有源中出现的最早发布时间。
    _FINGERPRINT_SOURCES：指纹词 → 涉及的不同源集合（跨源数 = 热度信号）。
    """
    _EVENT_FIRST_MAP.clear()
    _FINGERPRINT_SOURCES.clear()
    for src_id, items in sources.items():
        seen: dict[str, set] = {}  # 指纹词 -> 本源的条目计数（每源至少 1）
        for item in items:
            pub = parse_published_at(item)
            for fp in _item_fingerprint(item):
                # 首报时间
                if pub:
                    cur = _EVENT_FIRST_MAP.get(fp)
                    if cur is None or pub < cur:
                        _EVENT_FIRST_MAP[fp] = pub
                # 跨源计数：每源每指纹词算 1 次（用 set 去重同源重复）
                seen.setdefault(fp, set()).add(src_id)
        for fp, srcs in seen.items():
            _FINGERPRINT_SOURCES.setdefault(fp, set()).update(srcs)


def event_first_pub(item: dict) -> str:
    """该新闻所属事件簇的全库首报时间（UTC ISO，'' 表示无）。

    取该新闻所有指纹词对应的最早首报时间。
    """
    best = ""
    for fp in _item_fingerprint(item):
        cur = _EVENT_FIRST_MAP.get(fp, "")
        if cur and (not best or cur < best):
            best = cur
    return best


def event_cross_src(item: dict) -> int:
    """该新闻所属事件簇的跨源数（不同源报道数，2026-08-06 新增）。

    取该新闻所有指纹词中涉及源数最多的那个，作为事件热度信号。
    """
    best = 0
    for fp in _item_fingerprint(item):
        n = len(_FINGERPRINT_SOURCES.get(fp, set()))
        if n > best:
            best = n
    return best


# ── 价值打分（2026-08-06 新增） ──
# 分层第一道：规则打分（免费），决定哪些命中条目值得 LLM 深度解读。
# 维度：命中级别（P0>P1>P2>P3）→ 时效（新鲜>滞后>抢跑）→ 跨源数（热度）→ 命中数。
def value_score(item: dict, hits: list) -> float:
    """命中条目的价值分：越高越值得深度解读。"""
    s = 0.0
    # 1. 命中级别权重（取最高）
    lv_rank = {"P0": 40, "P1": 30, "P2": 20, "P3": 10}
    top_lv = hits[0][0] if hits else ""
    s += lv_rank.get(top_lv, 0)
    # 2. 时效：新鲜 +20 / 滞后 +5 / 抢跑 0
    info = event_lag_info(item)
    if "🟢" in info["freshness"]:
        s += 20
    elif "🟡" in info["freshness"]:
        s += 5
    # 3. 跨源热度：每多一个源 +6，上限 +30（3 源以上即高度关注）
    cross = event_cross_src(item)
    s += min(cross, 5) * 6
    # 4. 命中数量：多实体命中信息量更大，最多 +10
    s += min(len(hits), 3) * 3
    # 5. 内容质量（2026-08-06 分层优化）：空标题/付费推广噪音降权
    title = (item.get("title") or "").strip()
    content = (item.get("content") or "").strip()
    if len(title) < 4:            # 标题缺失/只有时间戳（如财联社"22:15"）
        s -= 15
    if any(k in content for k in ("专享", "解锁直达", "点击解锁", "付费阅读", "会员专享")):
        s -= 15
    if len(content) < 30:         # 内容过短，信息量不足
        s -= 10
    return max(s, 0.0)

# LLM 深度解读模块（延迟导入 + 缓存）：多线程并发调用时避免重复 import 竞争
_LLM_IMPORTED = False
_LLM_ENABLED = False


def _ensure_llm_imported():
    """线程安全地导入 llm_interpret（import 有 GIL 保护，幂等）。"""
    global _LLM_IMPORTED, _LLM_ENABLED
    if _LLM_IMPORTED:
        return
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from llm_interpret import is_enabled, deep_interpret
        _LLM_ENABLED = is_enabled()
        globals()["_deep_interpret"] = deep_interpret
    except Exception:
        _LLM_ENABLED = False
    finally:
        _LLM_IMPORTED = True


def _llm_deep_interpret(item: dict, hits: list, ctx: str) -> str:
    """调用 LLM 深度解读，未配置/失败返回空串（调用方回退模板）。"""
    _ensure_llm_imported()
    if not _LLM_ENABLED:
        return ""
    try:
        return globals()["_deep_interpret"](item, hits, ctx)
    except Exception:
        return ""

# 过泛词：不作为实体命中（命中会误报）
# 行业/板块级 2 字词（如"电子"命中"三星电子"、"汽车"命中"电动汽车"）误报率高；
# "AI" 作为政策关键词命中过滥（几乎所有科技新闻都含），一并排除，政策命中
# 应依赖更具体的词（算力/数据中心/能源双控等）。
# 注意：纯碱/烧碱/硫酸/玻璃等具体材料实体必须保留（是图谱有效 P0 节点）。
STOPWORDS = {"上涨", "下跌", "走高", "走低", "反弹", "回落", "新高", "新低", "市场",
             "报告", "数据", "新闻", "消息", "交易", "投资", "科技", "金融", "经济",
             "电子", "汽车", "AI"}

# ── 中英实体别名（2026-08-06 新增） ──
# 图谱节点是中文，英文源（bloomberg/cnbc/yahoo/zerohedge 等）文本是英文，
# 纯字符串匹配永远不命中 → 中英适配。这里把高频可翻译实体映射到英文词，
# 构建索引时一并入索引；英文词匹配走大小写不敏感 + 词边界（防 goldman 命中 gold）。
# 仅覆盖"实体类"节点（材料/工艺/器件），排除类别/generic 节点。
EN_ALIASES = {
    # ── 贵金属 / 基本金属 ──
    "黄金": ["gold", "gold price", "gold prices", "bullion", "gold bullion"],
    "白银": ["silver"],
    "铂族": ["platinum group", "platinum", "palladium"],
    "钴": ["cobalt"],
    "镍": ["nickel"],
    "锂矿": ["lithium", "lithium mining", "lithium mine", "lithium mines", "spodumene"],
    "碳酸锂": ["lithium carbonate"],
    "氢氧化锂": ["lithium hydroxide"],
    "磷酸铁锂": ["LFP", "lithium iron phosphate"],
    "三元材料": ["ternary cathode", "NCM cathode", "NCA"],
    "金属锂": ["lithium metal"],
    "钴酸锂": ["lithium cobalt oxide", "LCO"],
    "硫酸钴": ["cobalt sulfate"],
    "硫酸镍": ["nickel sulfate"],
    "铁矿石": ["iron ore"],
    "铜矿": ["copper ore", "copper mining"],
    "铝矿": ["bauxite", "aluminum ore"],
    "镁矿": ["magnesium"],
    "钨矿": ["tungsten", "tungsten ore"],
    "磷矿石": ["phosphate rock", "phosphates"],
    "铟": ["indium"],
    "镓": ["gallium"],
    "锗": ["germanium"],
    "铪": ["hafnium"],
    "铌": ["niobium"],
    "高纯石英": ["high purity quartz", "HPQ"],
    "锆英砂": ["zircon", "zircon sand"],
    "稀土": ["rare earth", "rare-earth", "rare earths"],
    "稀土氧化物": ["rare earth oxide", "rare earth oxides", "REO"],
    "镨钕氧化物": ["praseodymium neodymium oxide", "NdPr oxide"],
    "钕铁硼": ["NdFeB", "neodymium iron boron", "neodymium magnet"],
    "钕铁硼磁钢": ["NdFeB magnet"],
    "永磁材料": ["permanent magnet", "permanent magnets"],
    "钐钴磁体": ["samarium cobalt", "SmCo"],
    "铁氧体": ["ferrite"],
    "石墨": ["graphite"],
    "萤石": ["fluorspar", "fluorite"],
    # ── 化工 ──
    "纯碱": ["soda ash"],
    "烧碱": ["caustic soda"],
    "硫酸": ["sulfuric acid"],
    "钛白粉": ["titanium dioxide", "TiO2", "titanium oxide"],
    "磷酸": ["phosphoric acid"],
    "磷肥": ["phosphate fertilizer", "DAP", "MAP"],
    "草甘膦": ["glyphosate"],
    "聚氨酯": ["polyurethane", "PU foam", "MDI TDI"],
    "聚氨酯泡沫": ["polyurethane foam", "PU foam"],
    "制冷剂": ["refrigerant", "refrigerants"],
    "氢氟酸": ["hydrofluoric acid", "HF acid"],
    "六氟化钨": ["tungsten hexafluoride", "WF6"],
    "氟化铝": ["aluminum fluoride"],
    "氟塑料": ["fluoropolymer"],
    "含氟聚合物": ["fluoropolymer", "fluoropolymers"],
    "乙醇": ["ethanol"],
    "生物柴油": ["biodiesel"],
    "化肥": ["fertilizer", "fertilizers"],
    "大豆": ["soybean", "soybeans", "soy"],
    "玉米": ["corn"],
    "白糖": ["sugar"],
    "豆油": ["soybean oil"],
    "豆粕": ["soybean meal"],
    "淀粉": ["starch"],
    "涂料": ["coating", "coatings", "paint"],
    "油墨": ["ink", "printing ink"],
    "化妆品": ["cosmetics"],
    "洗涤剂": ["detergent"],
    "胶粘剂": ["adhesive", "adhesives"],
    "润滑剂": ["lubricant", "lubricants"],
    "保温材料": ["insulation material", "insulation"],
    "耐火材料": ["refractory", "refractories"],
    "电镀": ["electroplating"],
    # ── 硅 / 光伏 ──
    "硅料": ["polysilicon", "silicon", "silicon metal"],
    "电子级多晶硅": ["electronic grade polysilicon", "EG polysilicon"],
    "多晶硅": ["polysilicon"],
    "单晶硅": ["monocrystalline silicon", "mono silicon"],
    "硅片": ["silicon wafer", "silicon wafers", "wafer"],
    "光伏组件": ["solar module", "solar modules", "PV module", "PV modules", "solar panel", "solar panels"],
    "光伏电池": ["solar cell", "solar cells", "PV cell"],
    "光伏PERC": ["PERC"],
    "HJT": ["HJT", "heterojunction"],
    "光伏银浆": ["silver paste", "PV silver paste"],
    "光伏银粉": ["silver powder"],
    "硅片切割液": ["wafer cutting fluid"],
    "切割刃料": ["cutting abrasive"],
    "石英": ["quartz"],
    # ── 半导体 / 存储 / AI ──
    "半导体芯片": ["semiconductor chip", "semiconductor chips", "semiconductor", "chips"],
    "芯片制造": ["chip manufacturing", "chipmaking", "chip fabrication", "fabs"],
    "半导体制造": ["semiconductor manufacturing", "semiconductor fabrication", "foundry", "foundries"],
    "半导体清洗": ["semiconductor cleaning"],
    "半导体测试": ["semiconductor testing", "chip testing"],
    "存储芯片": ["memory chip", "memory chips", "memory", "DRAM", "NAND", "flash memory"],
    "HBM内存": ["HBM", "high bandwidth memory"],
    "HBM3e": ["HBM3e", "HBM3"],
    "HBM4": ["HBM4"],
    "LPDDR6": ["LPDDR6", "LPDDR"],
    "CXL内存": ["CXL"],
    "SSD控制器": ["SSD controller", "SSD"],
    "GPU互联": ["GPU interconnect", "NVLink", "NVSwitch"],
    "GPU封装": ["GPU packaging", "GPU package"],
    "芯片封装": ["chip packaging", "chip package", "semiconductor packaging", "advanced packaging"],
    "CoWoS封装": ["CoWoS", "chip on wafer"],
    "CPO封装": ["CPO", "co-packaged optics"],
    "TSV硅通孔": ["TSV", "through silicon via"],
    "2.5D封装": ["2.5D"],
    "薄膜封装材料": ["thin film encapsulation", "TFE"],
    "玻璃基板": ["glass substrate", "glass substrates"],
    "ABF载板": ["ABF substrate", "ABF"],
    "ABF薄膜": ["ABF film"],
    "BT树脂": ["BT resin"],
    "覆铜板": ["CCL", "copper clad laminate"],
    "PCB": ["PCB", "PCBs", "printed circuit board"],
    "PCB制造": ["PCB manufacturing"],
    "MLCC": ["MLCC", "multilayer ceramic capacitor"],
    "MLCC粉体": ["MLCC powder"],
    "光模块": ["optical module", "optical modules", "optical transceiver", "optical transceivers"],
    "VCSEL激光器": ["VCSEL"],
    "EML激光器": ["EML"],
    "光刻胶": ["photoresist", "photo resist"],
    "KrF光刻胶": ["KrF photoresist"],
    "ArF光刻胶": ["ArF photoresist"],
    "EUV光刻胶": ["EUV photoresist"],
    "EUV光刻": ["EUV lithography", "EUV"],
    "ArF光刻": ["ArF lithography"],
    "光引发剂": ["photoinitiator"],
    "光刻胶树脂": ["photoresist resin"],
    "光刻胶溶剂": ["photoresist solvent"],
    "前驱体材料": ["precursor", "precursors"],
    "CVD/ALD前驱体": ["CVD precursor", "ALD precursor", "CVD", "ALD"],
    "硅烷气体": ["silane"],
    "磷烷": ["phosphine"],
    "砷烷": ["arsine"],
    "三氟化氮": ["nitrogen trifluoride", "NF3"],
    "四氟化碳": ["carbon tetrafluoride", "CF4"],
    "六氟乙烷": ["hexafluoroethane", "C2F6"],
    "八氟环丁烷": ["octafluorocyclobutane", "C4F8"],
    "六氟化钼": ["molybdenum hexafluoride", "MoF6"],
    "硼烷": ["borane", "diborane"],
    "乙硅烷": ["disilane"],
    "二氯硅烷": ["dichlorosilane"],
    "四氯化硅": ["silicon tetrachloride"],
    "溴化氢": ["hydrogen bromide"],
    "氦气": ["helium"],
    "液氦": ["liquid helium"],
    "高纯氦气": ["high purity helium"],
    "氖气": ["neon"],
    "氪气": ["krypton"],
    "氙气": ["xenon"],
    "氟气": ["fluorine"],
    "氯气": ["chlorine"],
    "氢气": ["hydrogen"],
    "氨气": ["ammonia"],
    "电子级硫酸": ["electronic grade sulfuric acid"],
    "电子级双氧水": ["electronic grade hydrogen peroxide"],
    "电子级盐酸": ["electronic grade hydrochloric acid"],
    "电子级硝酸": ["electronic grade nitric acid"],
    "电子级氨水": ["electronic grade ammonia"],
    "超净高纯试剂": ["ultra high purity reagent"],
    "高纯试剂": ["high purity reagent"],
    "电子氟化液": ["fluorinated coolant", "fluorinated fluid"],
    "氢氟醚": ["hydrofluoroether", "HFE"],
    "全氟聚醚": ["perfluoropolyether", "PFPE"],
    "剥离液": ["stripper", "lift-off solution"],
    "BOE蚀刻液": ["buffered oxide etch", "BOE"],
    "TMAH显影液": ["TMAH"],
    "底部填充胶": ["underfill"],
    "TIM导热材料": ["thermal interface material", "TIM"],
    "环氧塑封料": ["epoxy molding compound", "EMC"],
    "引线框架": ["lead frame", "lead frames"],
    "封装框架": ["package substrate", "package substrates"],
    "键合铜丝": ["bonding wire", "copper bonding wire"],
    "铜键合丝": ["copper wire", "bonding wire"],
    "IC托盘": ["IC tray"],
    "IC管带": ["IC tube"],
    "电子级铜箔": ["electronic copper foil"],
    "电子铜箔": ["electronic copper foil"],
    "锂电铜箔": ["battery copper foil"],
    "铜箔": ["copper foil"],
    "铝箔": ["aluminum foil"],
    "电子级玻纤布": ["electronic fiberglass cloth", "E-glass fabric"],
    "电子级玻纤纱": ["electronic fiberglass yarn"],
    "电子级环氧树脂": ["electronic epoxy resin"],
    "硅微粉": ["silicon micropowder", "silica micropowder"],
    "球形硅微粉": ["spherical silica powder"],
    "球形氧化铝": ["spherical alumina"],
    # ── 散热 / 电源 / 结构件 ──
    "液冷散热": ["liquid cooling", "liquid-cooled"],
    "浸没式冷却": ["immersion cooling"],
    "数据中心散热": ["data center cooling"],
    "散热器": ["heat sink", "heat sinks", "heatsink"],
    "风扇模组": ["fan module", "cooling fan"],
    "服务器机箱": ["server chassis"],
    "高速铜缆": ["high-speed copper cable", "DAC", "direct attach cable"],
    "数字电源": ["digital power"],
    "48V电源": ["48V", "48-volt"],
    "电极": ["electrode"],
    "电刷": ["carbon brush"],
    "电缆": ["cable", "cables"],
    "电线电缆": ["wire and cable"],
    "钣金件": ["sheet metal"],
    "马达轴承": ["motor bearing", "bearings"],
    "电解铝": ["electrolytic aluminum", "primary aluminum"],
    "铝合金": ["aluminum alloy", "aluminum alloys"],
    "镁合金": ["magnesium alloy"],
    "氧化铝": ["alumina", "aluminum oxide"],
    "铝合金板材": ["aluminum sheet"],
    "不锈钢": ["stainless steel"],
    "硬质合金": ["cemented carbide"],
    "合金材料": ["alloy", "alloys"],
    "金属冶炼": ["smelting", "metal smelting"],
    "金属粉体": ["metal powder", "metal powders"],
    "镍粉": ["nickel powder"],
    "铜粉": ["copper powder"],
    "银粉": ["silver powder"],
    "钨粉": ["tungsten powder"],
    "钼粉": ["molybdenum powder"],
    # ── 电池 / 能源 ──
    "动力电池": ["power battery", "EV battery", "EV batteries"],
    "铅酸电池": ["lead-acid battery", "lead acid battery"],
    "锂电池电解液": ["electrolyte", "lithium battery electrolyte"],
    "锂电池负极集流体": ["anode current collector"],
    "锂电池正极集流体": ["cathode current collector"],
    "锂电池粘结剂": ["binder", "battery binder"],
    "锂电池溶剂": ["battery solvent"],
    "铝塑膜": ["aluminum laminated film", "pouch film"],
    "负极材料": ["anode material", "anode materials"],
    "三元前驱体": ["NCM precursor", "precursor cathode"],
    "四氧化三钴": ["cobalt oxide", "Co3O4"],
    "铜箔": ["copper foil"],
    "燃料油": ["fuel oil"],
    "电力设备": ["power equipment"],
    "电网设备": ["grid equipment", "power grid equipment"],
    "电动汽车": ["electric vehicle", "electric vehicles", "EV", "EVs"],
    "移动AI": ["on-device AI", "edge AI", "mobile AI"],
    # ── 下游 / 其他 ──
    "AI服务器": ["AI server", "AI servers", "AI rack"],
    "AI加速器": ["AI accelerator", "AI accelerators"],
    "台积电": ["TSMC", "Taiwan Semiconductor"],
    "玻璃纤维布": ["fiberglass", "glass fiber", "glass fabric"],
    "抛光材料": ["polishing material", "polishing slurry"],
    "催化剂": ["catalyst", "catalysts"],
    "稀土抛光粉": ["rare earth polishing powder", "cerium oxide"],
    "稀土催化剂": ["rare earth catalyst"],
    "仲钨酸铵": ["ammonium paratungstate", "APT"],
    "磷化铟": ["indium phosphide", "InP"],
    "砷化镓": ["gallium arsenide", "GaAs"],
    "InP衬底": ["InP substrate"],
    "GaAs衬底": ["GaAs substrate"],
    "InGaAs外延片": ["InGaAs epitaxial", "InGaAs"],
    "GaAs外延片": ["GaAs epitaxial"],
    "氦气": ["helium"],
    "高端装备": ["high-end equipment"],
    "封装": ["packaging"],
    "食品加工": ["food processing"],
    "制药": ["pharmaceutical", "pharma"],
    "首饰": ["jewelry"],
    "摄影": ["photography"],
    "牙科": ["dental"],
    "造纸": ["paper", "pulp"],
    "食品添加剂": ["food additive"],
    "乙醇": ["ethanol", "ethyl alcohol"],
}


# ── 知识图谱索引（供命中检测） ──

def _build_kg_index():
    """预构建实体索引：实体名 → (级别, 图谱ID)。"""
    idx = {}
    if not KG_AVAILABLE:
        return idx
    # P0: supply_chain 全部节点（含传导链叙事节点）
    # 排除 auto_added/auto-detected 待验证节点——它们可能是自动采集误入的句子片段
    # （如"半导体指数转涨"），参与命中会污染报告。
    # 排除 generic 行业大类泛词节点（机械/汽车/电子等，命中价值低，2026-08-05）
    for n, d in _KG.supply_chain.graph.nodes(data=True):
        if d.get("auto_added") or d.get("category") == "auto-detected":
            continue
        if d.get("generic"):
            continue
        idx[n] = ("P0", f"supply:{n}")
        # 中英别名展开（2026-08-06）：英文词入索引，映射回同一 supply 节点
        for alias in EN_ALIASES.get(n, []):
            idx.setdefault(alias, ("P0", f"supply:{n}"))
    # P0: events 传导链节点（238 个叙事节点，最贴近新闻用词）
    chain_nodes = set()
    for ev in _KG.event_graph.events.values():
        for step in ev.get("transmission_chain", []):
            chain_nodes.add(step.get("from", ""))
            chain_nodes.add(step.get("to", ""))
    for n in chain_nodes:
        if len(n) >= 2:
            idx.setdefault(n, ("P1", f"eventchain:{n}"))
    # P0: policy 名称与关键词
    # gid 编码时效维度：policy:{pid}:L（长期）/ :S（短期），渲染时解析标注
    for pid, p in _KG.policy_graph.policies.items():
        th = "S" if p.get("time_horizon") == "short-term" else "L"
        idx[p.get("name", "")] = ("P2", f"policy:{pid}:{th}")
        for kw in p.get("keywords", []):
            if len(kw) >= 2:
                idx.setdefault(kw, ("P2", f"policy:{pid}:{th}"))
    # P1: events 名称/关键词（含 situation）
    for eid, ev in _KG.event_graph.events.items():
        idx.setdefault(ev.get("name", ""), ("P1", f"event:{eid}"))
    for sit in _KG.event_graph.taxonomy.get("situations", []):
        idx.setdefault(sit.get("name", ""), ("P1", f"situation:{sit['id']}"))
        for kw in sit.get("keywords", []):
            if len(kw) >= 2:
                idx.setdefault(kw, ("P1", f"situation:{sit['id']}"))
    # P3: stock_profiles sector + 公司名（2026-08-06 修复：原来遍历 loader 对象被
    # except 吞掉，P3 从未生效；现在用 .profiles，并加入公司名/ticker 命中）
    try:
        sp_loader = _KG.stock_profiles
        profiles = sp_loader.profiles if hasattr(sp_loader, "profiles") else (
            sp_loader.values() if isinstance(sp_loader, dict) else {})
        for code, prof in profiles.items() if isinstance(profiles, dict) else profiles:
            if not isinstance(prof, dict):
                continue
            for role in prof.get("roles", {}):
                if len(role) >= 2:
                    idx.setdefault(role, ("P3", f"sector:{role}"))
            # 公司名/ticker 命中（如 Sandisk → SNDK，Micron → MU）
            for name_key in ("ticker", "name", "en_name", "alias"):
                nm = prof.get(name_key) or (code if name_key == "ticker" else "")
                if nm and len(str(nm)) >= 2:
                    idx.setdefault(str(nm), ("P3", f"stock:{code}"))
    except Exception:
        pass
    return idx


KG_INDEX = _build_kg_index()


def detect_hits(text: str) -> list:
    """返回 [(级别, 图谱ID, 实体名)]，按 P0→P3 排序去重。

    匹配策略：先精确（实体名在文本中），再子串（实体名包含在文本词中，
    或文本词包含在实体名中，要求 ≥2 字符且非过泛词）。
    英文别名（EN_ALIASES）走大小写不敏感 + 词边界匹配（2026-08-06）。"""
    if not KG_AVAILABLE:
        return []
    text_lower = text.lower()
    hits = []
    for ent, (level, gid) in KG_INDEX.items():
        if not ent or len(ent) < 2 or ent in STOPWORDS:
            continue
        # 英文别名/英文缩写实体：大小写不敏感 + 词边界（避免 gold 命中 golden）
        if _is_ascii(ent):
            if re.search(r"(?<![A-Za-z0-9])" + re.escape(ent.lower()) + r"(?![A-Za-z0-9])", text_lower):
                hits.append((level, gid, ent))
            continue
        if ent in text:
            hits.append((level, gid, ent))
            continue
        # 子串：文本中的词是实体的子串（如"锂"→"碳酸锂"），或实体是文本子串
        # 限制实体长度 ≤6 避免过长实体误配
        if len(ent) <= 6 and ent in text.replace(" ", ""):
            hits.append((level, gid, ent))
    # 去重 + 排序（P0 优先），最多 5 个
    seen = set()
    out = []
    for level, gid, ent in sorted(hits, key=lambda x: x[0]):
        if gid not in seen:
            seen.add(gid)
            out.append((level, gid, ent))
    return out[:5]


_ASCII_RE = None  # 惰性编译

def _is_ascii(s: str) -> bool:
    """实体是否纯 ASCII（英文词/缩写）。中文实体返回 False。"""
    global _ASCII_RE
    if _ASCII_RE is None:
        _ASCII_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 .&()%+-]*$")
    return bool(_ASCII_RE.match(s))


def kg_context(gid: str) -> str:
    """实体的图谱 1 跳上下文（供解读引用）。"""
    if not KG_AVAILABLE:
        return ""
    if gid.startswith("supply:"):
        n = gid.split(":", 1)[1]
        if n in _KG.supply_chain.graph:
            succ = list(_KG.supply_chain.graph.successors(n))[:4]
            pred = list(_KG.supply_chain.graph.predecessors(n))[:4]
            parts = []
            if pred:
                parts.append("上游:" + "/".join(pred))
            if succ:
                parts.append("下游:" + "/".join(succ))
            return "；".join(parts)
    if gid.startswith("eventchain:"):
        n = gid.split(":", 1)[1]
        # 该叙事节点出现在哪些事件的传导链里
        evs = []
        for eid, ev in _KG.event_graph.events.items():
            for step in ev.get("transmission_chain", []):
                if step.get("from") == n or step.get("to") == n:
                    evs.append(eid)
                    break
            if len(evs) >= 3:
                break
        if evs:
            names = [_KG.event_graph.events[e].get("name", e)[:20] for e in evs]
            return "关联事件:" + "/".join(names)
    return ""


def parse_items(since: datetime | None = None) -> list:
    """读取全部源的 JSONL，返回按源分组的条目列表。"""
    sources = {}
    for f in sorted(DATA_DIR.glob("*.jsonl")):
        src_id = f.stem
        items = []
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since:
                fa = item.get("fetched_at", "")
                try:
                    t = datetime.fromisoformat(fa)
                    if t < since:
                        continue
                except ValueError:
                    pass
            items.append(item)
        if items:
            sources[src_id] = items
    return sources


def _clean_text(s: str, limit: int = 120) -> str:
    """清洗条目文本：剥 HTML 标签、还原实体、压缩空白，截断到 limit 字。

    采集内容里常混入 <figure><img ...> 等原始 HTML（如虎嗅封面图），
    直接进 markdown 会被当成真实 HTML 执行，长 URL 不换行把布局撑坏。
    """
    if not s:
        return ""
    s = html.unescape(s)                    # &amp; &lt; 等实体还原为字符
    s = re.sub(r"<[^>]+>", " ", s)          # 剥掉所有 HTML 标签（含 img/figure）
    s = re.sub(r"\s+", " ", s).strip()      # 压缩连续空白/换行
    if len(s) > limit:
        s = s[:limit].rstrip() + "…"
    return s


def _safe_title(item: dict) -> str:
    """条目标题：优先 title；为空时从正文提取首句（财联社电报 title 常为空）。"""
    t = _clean_text(item.get("title") or "", 60)
    if t:
        return t
    c = _clean_text(item.get("content") or "", 120)
    c = re.sub(r"^\d{2}:\d{2}:\d{2}\s*", "", c)      # 去电报时间戳前缀
    c = re.sub(r"^财联社\d+月\d+日电[，,]?\s*", "", c)  # 去"财联社X月X日电"前缀
    # 按句子边界截断（标签尾巴如"中东冲突 阅1.13W 评论(0)"不混入标题）
    m = re.search(r"[^。！？!?]{2,50}", c)
    if m:
        c = m.group(0).strip()
    return c[:60] or "(无标题)"


def _fmt_gid(gid: str) -> str:
    """格式化图谱 ID 显示：policy:{pid}:L/S 后缀转为时效标注。"""
    if gid.startswith("policy:") and gid.endswith((":L", ":S")):
        base, th = gid.rsplit(":", 1)
        return f"{base}[{'短期' if th == 'S' else '长期'}]"
    return gid


def interpret(item: dict, hits: list, use_llm: bool = True) -> str:
    """生成解读文本（事实层 + 图谱层）。

    优先 LLM 深度解读（需配置 llm_interpret），未配置/失败时回退模板解读。
    use_llm=False（2026-08-06 分层）：非 top-N 命中条目只做模板解读，不调 LLM。
    """
    content = item.get("content") or ""
    title = item.get("title") or ""
    text = _clean_text((title + " " + content).strip())
    brief = text[:120]
    lines = [f"**信息**：{brief}"]
    if hits:
        top = hits[0]
        ctx = kg_context(top[1])
        # 时效信息并入图谱上下文（2026-08-05）：LLM 解读能看到事件/报道时间差，
        # 且缓存键自动区分不同时效的解读
        tinfo = event_lag_info(item)
        lag_note = f"事件:{tinfo['event_time'] or '未知'} 首报:{tinfo['first_pub'] or '未知'} " \
                   f"报道:{tinfo['published_at'] or '未知'} 滞后:{tinfo['lag_hours']:.0f}h({tinfo['freshness']})"
        ctx_llm = (ctx + "；" + lag_note) if ctx else lag_note
        # LLM 深度解读（可选）：带缓存（同新闻不重复调 LLM），失败自动回退模板
        deep = _cached_interpret(item, hits, ctx_llm) if use_llm else ""
        lines.append(f"**图谱关联**：{top[0]} 命中 `{_fmt_gid(top[1])}`（{top[2]}）" +
                     (f"；图谱上下文：{ctx}" if ctx else ""))
        if deep:
            lines.append("**深度解读**：" + deep.replace("\n", " "))
        else:
            lines.append("**解读**：该信息与知识图谱已有节点直接相关，可沿图谱上下文" +
                         "进一步追踪产业链传导。")
    else:
        lines.append("**图谱关联**：P4 无命中——知识图谱暂无对应节点，建议后续积累。")
        lines.append("**解读**：该信息暂未进入知识图谱覆盖范围。")
    return "\n".join(lines)


def gen_actions(sources: dict) -> list:
    """生成知识图谱补充/更新建议清单。"""
    actions = []
    for src_id, items in sources.items():
        for item in items:
            text = _clean_text((item.get("title") or "") + " " + (item.get("content") or ""))
            hits = detect_hits(text)
            if hits:
                top = hits[0]
                actions.append(f"- [关联] {src_id} → `{_fmt_gid(top[1])}`（{top[0]}）" +
                               ("，建议人工核对图谱数据一致性" if top[0] == "P1" else ""))
    return actions


def render_markdown(sources: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d")
    out = [f"# 情报采集报告 {today}", ""]
    total = sum(len(v) for v in sources.values())

    # 构建跨源事件首报映射（全库指纹聚类，供时效计算使用）
    build_event_first_map(sources)

    # 每源展示上限：命中条目优先（最多 15 条），无命中条目取最新 5 条
    MAX_HIT_SHOWN = 15
    MAX_NOHIT_SHOWN = 5
    # 深度解读上限（2026-08-06 分层）：全库价值分 top N 才调 LLM，
    # 其余命中条目只做模板解读。分层 = 规则打分(免费) → LLM 深度(旗舰只干值得的活)。
    DEEP_LLM_LIMIT = int(os.environ.get("DEEP_LLM_LIMIT", "30"))

    # 第一遍：全量条目命中检测（纯本地，快）——不再截断，742 条全扫
    # 记录每源全部条目 + 命中，供排序与统计
    src_scored: dict[int, list] = {}  # src_idx -> [(orig_idx, item, hits)]
    hit_n = 0
    for src_idx, (src_id, items) in enumerate(sources.items(), 1):
        scored = []
        for orig_idx, item in enumerate(items):
            text = _clean_text((item.get("title") or "") + " " + (item.get("content") or ""))
            hits = detect_hits(text)
            if hits:
                hit_n += 1
            scored.append((orig_idx, item, hits))
        src_scored[src_idx] = scored

    # 每源排序：命中条目按价值分降序（P0 权重+时效+跨源热度），无命中按最新
    # 展示顺序 = 命中条目（前 15）+ 无命中条目（最新 5）
    show_plan: dict[int, list] = {}  # src_idx -> [(orig_idx, item, hits)] 展示集合
    deep_targets: list[tuple] = []   # 全局价值分 top 命中，供 LLM 深度解读
    for src_idx, scored in src_scored.items():
        hits_list = [(oi, it, hs) for oi, it, hs in scored if hs]
        nohit_list = [(oi, it, hs) for oi, it, hs in scored if not hs]
        # 2026-08-06：价值分排序替代纯级别排序
        hits_list.sort(key=lambda t: value_score(t[1], t[2]), reverse=True)
        shown = hits_list[:MAX_HIT_SHOWN] + nohit_list[-MAX_NOHIT_SHOWN:]
        show_plan[src_idx] = shown
        deep_targets.extend((src_idx, oi, it, hs) for oi, it, hs in shown if hs)

    # 全库价值分 top N → LLM 深度解读（2026-08-06 三级分层）
    # ① 规则层（免费）：价值分 top 候选池
    # ② 轻量模型层：语义判断"是否有增量价值"，过滤候选
    # ③ 旗舰层：对通过的做深度解读（DEEP_LLM_LIMIT）
    RULE_CANDIDATE_LIMIT = int(os.environ.get("RULE_CANDIDATE_LIMIT", "60"))
    deep_targets.sort(key=lambda t: value_score(t[2], t[3]), reverse=True)
    candidates = deep_targets[:RULE_CANDIDATE_LIMIT]

    # 轻量模型筛选（召回优先：判断失败/超时一律放行）
    _ensure_llm_imported()
    if _LLM_ENABLED and candidates:
        kept = []
        for t in candidates:
            src_idx, oi, item, hits = t
            top = hits[0]
            ctx = kg_context(top[1])
            tinfo = event_lag_info(item)
            lag_note = f"事件:{tinfo['event_time'] or '未知'} 首报:{tinfo['first_pub'] or '未知'} " \
                       f"报道:{tinfo['published_at'] or '未知'} 滞后:{tinfo['lag_hours']:.0f}h({tinfo['freshness']})"
            ctx_llm = (ctx + "；" + lag_note) if ctx else lag_note
            if _cached_lite_worth(item, hits, ctx_llm):
                kept.append(t)
        deep_targets = kept
    else:
        deep_targets = candidates
    deep_targets = deep_targets[:DEEP_LLM_LIMIT]
    deep_keys = {(t[0], t[1]) for t in deep_targets}

    # 第二遍：仅对 top-N 命中条目并发 LLM 解读（控制调用量，避免全量调 LLM）
    # key = (src_idx, orig_idx)
    results: dict[tuple, str] = {}
    with ThreadPoolExecutor(max_workers=LLM_MAX_WORKERS) as ex:
        future_map = {ex.submit(interpret, t[2], t[3]): (t[0], t[1]) for t in deep_targets}
        for fut in as_completed(future_map):
            key = future_map[fut]
            try:
                results[key] = fut.result()
            except Exception as _e:
                results[key] = ""  # 兜底：单条失败不阻塞报告

    # 第三遍：按源组装（命中优先在前，无命中"其他要闻"在后）
    shown_total = 0
    for src_idx, (src_id, items) in enumerate(sources.items(), 1):
        out.append(f"## {src_idx}. {src_id}")
        shown = show_plan[src_idx]
        shown_total += len(shown)
        hits_shown = [t for t in shown if t[2]]
        nohit_shown = [t for t in shown if not t[2]]
        if hits_shown:
            for i, (oi, item, hits) in enumerate(hits_shown, 1):
                out.append(f"### {i}. {_safe_title(item)}")
                # 时间信息行：事件/报道/采集 + 时效分级（2026-08-05 新增）
                out.append(f"⏱ {fmt_time_line(item)}")
                # 分层：top-N 有 LLM 深度解读；其余命中走带命中标注的模板解读
                if (src_idx, oi) in deep_keys:
                    out.append(results.get((src_idx, oi)) or interpret(item, hits))
                else:
                    out.append(interpret(item, hits, use_llm=False))
                url = item.get("url", "")
                if url:
                    out.append(f"  链接：{url}")
                out.append("")
        if nohit_shown:
            out.append("**其他要闻**（无图谱命中）：")
            for oi, item, hits in nohit_shown:
                out.append(f"- {_safe_title(item)}"
                           + (f"  {item.get('url', '')}" if item.get("url") else ""))
            out.append("")
    # 图谱动作
    out.append("## 2. 知识图谱补充/更新说明")
    actions = gen_actions(sources)
    if actions:
        out.extend(actions)
    else:
        out.append("- 本轮无图谱命中，无需变更。")
    out.append("")
    hit_rate = hit_n / total * 100 if total else 0
    out.append(f"## 3. 缺口与建议")
    out.append(f"- 本轮图谱命中率：{hit_rate:.0f}%（目标 ≥40%）；"
               f"P0-P3 命中 {hit_n}/{total} 条（按全量采集计）。")
    out.append("- 建议：扩大知识图谱实体覆盖（尤其事件/政策关键词），提升命中率。")
    out.append("")
    out.append("---")
    out.append("> 规范 v0.1（迭代中）· 由 tools/generate_report.py 生成 · 见 docs/report-format.md")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser(description="情报采集报告生成器")
    ap.add_argument("--since", default="", help="只看最近时段，如 6h/1d")
    ap.add_argument("--out", default="", help="输出文件（默认 stdout）")
    args = ap.parse_args()

    since = None
    if args.since:
        m = re.match(r"^(\d+)([hd])$", args.since)
        if m:
            n, unit = int(m.group(1)), m.group(2)
            since = datetime.now(timezone.utc) - timedelta(hours=n if unit == "h" else n * 24)

    sources = parse_items(since)
    if not sources:
        print("（无数据：data 目录为空或 since 过滤后无条目。"
              "海外源首跑为 baseline 建档，需下一轮才产生条目。）")
        return
    md = render_markdown(sources)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
        print(f"报告已写入 {args.out}")
    else:
        print(md)
    # 缓存统计（观察 LLM 解读去重效果）
    if CACHE_STATS["hit"] or CACHE_STATS["miss"]:
        total_c = CACHE_STATS["hit"] + CACHE_STATS["miss"]
        print(f"[缓存] LLM 解读: 命中 {CACHE_STATS['hit']} / 新调 {CACHE_STATS['miss']} "
              f"（缓存命中率 {CACHE_STATS['hit'] / total_c * 100:.0f}%）")
    # 轻量筛选统计（2026-08-06 分层第②层）
    if LITE_STATS["hit"] or LITE_STATS["miss"]:
        total_l = LITE_STATS["hit"] + LITE_STATS["miss"]
        print(f"[筛选] 轻量模型: 命中 {LITE_STATS['hit']} / 新调 {LITE_STATS['miss']} "
              f"（筛选 {total_l} 条候选）")


if __name__ == "__main__":
    main()
