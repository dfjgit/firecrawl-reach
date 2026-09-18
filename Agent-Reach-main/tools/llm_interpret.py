# -*- coding: utf-8 -*-
"""LLM 深度解读模块（OpenAI 兼容接口，零第三方依赖）。

配置来源（优先级从高到低）：
  1. 环境变量  LLM_API_KEY / LLM_BASE_URL / LLM_MODEL
  2. ~/.agent-reach/config.yaml 的 llm: 段：
       llm:
         api_key: "sk-xxx"
         base_url: "https://api.deepseek.com/v1"   # 兼容 /chat/completions 即可
         model: "deepseek-chat"
         enabled: true

未配置时 is_enabled() 返回 False，调用方回退模板解读。
失败（网络/超时/4xx）时抛 LLMError，调用方回退模板解读，不中断报告生成。
"""
import json
import os
import subprocess
import sys
import urllib.request
import urllib.error
from pathlib import Path

CFG_PATH = Path.home() / ".agent-reach" / "config.yaml"


class LLMError(Exception):
    pass


def _load_cfg() -> dict:
    """读取 llm 配置段（yaml 只支持最简 key: value 形式）。"""
    cfg = {}
    try:
        for line in CFG_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or ":" not in line:
                continue
            k, v = line.split(":", 1)
            cfg[k.strip()] = v.strip().strip("\"'")
    except Exception:
        pass
    llm = {}
    # llm: 段为嵌套，简化处理：直接读 llm_* 前缀或顶层 llm 键的后续缩进行
    try:
        text = CFG_PATH.read_text(encoding="utf-8")
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if ln.strip().startswith("llm:") or ln.strip().startswith("llm:"):
                j = i + 1
                while j < len(lines) and (lines[j].startswith("  ") or lines[j].startswith("\t")):
                    kv = lines[j].strip()
                    if ":" in kv:
                        k, v = kv.split(":", 1)
                        llm[k.strip()] = v.strip().strip("\"'")
                    j += 1
                break
    except Exception:
        pass
    return llm


def get_config() -> dict:
    """合并环境变量与配置文件，返回双模型配置。

    旗舰（深度解读）：api_key / base_url / model
    轻量（价值筛选）：lite_api_key / lite_base_url / lite_model
    轻量未配置时回退用旗舰的服务与模型（功能不缺失）。
    2026-08-06 升级：旗舰与轻量可指向不同厂商（如旗舰 qwen、轻量 deepseek），
    api_key/base_url 也按模型区分。
    """
    env = {
        "api_key": os.environ.get("LLM_API_KEY", ""),
        "base_url": os.environ.get("LLM_BASE_URL", ""),
        "model": os.environ.get("LLM_MODEL", ""),
        "lite_api_key": os.environ.get("LLM_LITE_API_KEY", ""),
        "lite_base_url": os.environ.get("LLM_LITE_BASE_URL", ""),
        "lite_model": os.environ.get("LLM_LITE_MODEL", ""),
    }
    file_cfg = _load_cfg()
    cfg = {k: (env[k] or file_cfg.get(k, "")) for k in env}
    # 轻量未单独配置 → 回退旗舰服务/模型
    if not cfg["lite_api_key"]:
        cfg["lite_api_key"] = cfg["api_key"]
    if not cfg["lite_base_url"]:
        cfg["lite_base_url"] = cfg["base_url"]
    if not cfg["lite_model"]:
        cfg["lite_model"] = cfg["model"]
    cfg["enabled"] = bool(cfg["api_key"] and cfg["base_url"] and cfg["model"])
    return cfg


def is_enabled() -> bool:
    return get_config()["enabled"]


def _chat_direct(prompt: str, system: str = "", timeout: int = 90, max_tokens: int = 500,
                 model_key: str = "model") -> str:
    """真实网络调用（仅供 llm_worker.py 子进程使用）。

    不直接在报告进程内调用：DeepSeek 偶发原生崩溃（连续 ~25 次后无
    traceback 直接退出），子进程隔离可保证报告生成不中断。
    model_key: "model"=旗舰（api_key/base_url/model）
               "lite_model"=轻量（lite_api_key/lite_base_url/lite_model）
    2026-08-06：旗舰与轻量可指向不同厂商，各自独立的 key/url/model。
    """
    cfg = get_config()
    if not cfg["enabled"]:
        raise LLMError("LLM 未配置（需 api_key/base_url/model）")
    if model_key == "lite_model":
        api_key, base_url, model = cfg["lite_api_key"], cfg["lite_base_url"], cfg["lite_model"]
    else:
        api_key, base_url, model = cfg["api_key"], cfg["base_url"], cfg["model"]
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {
        "model": model,
        "messages": [m for m in
                     ([{"role": "system", "content": system}] if system else []) +
                     [{"role": "user", "content": prompt}]],
        "temperature": 0.3,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return data["choices"][0]["message"]["content"].strip()
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise LLMError(f"LLM HTTP {e.code}: {body}") from e
    except Exception as e:
        raise LLMError(f"LLM 调用失败: {str(e)[:200]}") from e


def chat(prompt: str, system: str = "", timeout: int = 90, max_tokens: int = 500,
         model_key: str = "model") -> str:
    """调用 OpenAI 兼容 /chat/completions，返回 assistant 文本。

    通过子进程执行真实网络调用：DeepSeek API 偶发原生崩溃（进程直接退出、
    无 traceback），子进程隔离可保证报告生成不中断。子进程失败（崩溃/
    超时/错误）时抛 LLMError，调用方回退模板解读。
    model_key: "model"=旗舰 / "lite_model"=轻量（2026-08-06 双模型）。
    """
    if not is_enabled():
        raise LLMError("LLM 未配置（需 api_key/base_url/model）")
    worker = Path(__file__).resolve().parent / "llm_worker.py"
    req = json.dumps({
        "prompt": prompt,
        "system": system,
        "timeout": timeout,
        "max_tokens": max_tokens,
        "model_key": model_key,
    }, ensure_ascii=False)
    try:
        r = subprocess.run(
            [sys.executable, str(worker)],
            input=req.encode("utf-8"),
            capture_output=True,
            timeout=timeout + 30,   # 网络超时 + 进程启动余量
        )
    except subprocess.TimeoutExpired:
        raise LLMError(f"LLM 子进程超时（>{timeout + 30}s）") from None
    if r.returncode != 0:
        # 原生崩溃（无 stdout）或非零退出
        err = (r.stderr or b"").decode("utf-8", errors="replace")[:200]
        raise LLMError(f"LLM 子进程退出码 {r.returncode}: {err}")
    try:
        out = json.loads(r.stdout.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise LLMError("LLM 子进程输出异常（可能原生崩溃）") from None
    if not out.get("ok"):
        raise LLMError(str(out.get("error", "LLM 子进程失败"))[:300])
    return out["text"]


def deep_interpret(item: dict, hits: list, kg_context_str: str) -> str:
    """生成深度解读：事实层 + 图谱层（一致/矛盾/补充）+ 含义层。

    hits: [(级别, 图谱ID, 实体名)]；kg_context_str: 已算好的图谱上下文摘要。
    任何失败都抛 LLMError，由调用方回退模板。
    """
    title = (item.get("title") or "").strip()
    content = (item.get("content") or "").strip()[:500]
    url = (item.get("url") or "").strip()

    hit_desc = "; ".join(
        f"{lv} 命中 {gid}（{ent}）" for lv, gid, ent in hits[:5]
    ) or "P4 无命中"

    system = (
        "你是个人知识图谱的深度解读助手。基于给定新闻信息与知识图谱命中情况，"
        "输出三段式解读：\n"
        "【事实层】这条信息的核心事实（2-3 句）；\n"
        "【图谱层】与知识图谱的一致性/矛盾/补充（明确引用命中的图谱实体，"
        "无命中就说明图谱缺失什么）；\n"
        "【含义层】对产业链/政策/市场可能意味着什么，以及值得后续关注的点。\n"
        "控制在 180 字内，客观、具体、不空泛。"
    )
    prompt = (
        f"标题：{title}\n"
        f"内容：{content}\n"
        f"链接：{url}\n\n"
        f"知识图谱命中：{hit_desc}\n"
        f"图谱上下文：{kg_context_str or '（无）'}\n\n"
        "请给出三段式深度解读。"
    )
    return chat(prompt, system=system, max_tokens=600)


def lite_worth_deep(item: dict, hits: list, kg_context_str: str) -> bool:
    """轻量模型判断：这条新闻是否值得旗舰深度解读（2026-08-06 分层第②层）。

    规则层（value_score）判"结构化价值"（级别/时效/热度），判不了语义增量——
    这条新闻对产业链/市场有没有独立的、未被图谱覆盖的增量信息。
    返回 True=值得 / False=不值得。失败（LLM 不可用）返回 True（召回优先：
    筛选器漏判的损失 >> 多解读一条的成本）。
    """
    title = (item.get("title") or "").strip()
    content = (item.get("content") or "").strip()[:500]
    hit_desc = "; ".join(
        f"{lv} 命中 {gid}（{ent}）" for lv, gid, ent in hits[:5]
    ) or "P4 无命中"

    system = (
        "你是情报筛选助手。判断一条新闻是否值得做深度产业链/市场分析。\n"
        "值得的情况：财报/业绩/重大合同/产能/价格变动/政策/技术突破/并购/供需变化，"
        "且对产业链或市场有可分析的增量信息。\n"
        "不值得的情况：纯股价快讯（无新信息）、重复旧闻、广告/软文、无关花边、"
        "只有泛泛观点没有具体事实。\n"
        "只输出一个词：YES 或 NO。拿不准时输出 YES（宁多勿漏）。"
    )
    prompt = (
        f"标题：{title}\n"
        f"内容：{content}\n\n"
        f"知识图谱命中：{hit_desc}\n"
        f"图谱上下文：{kg_context_str or '（无）'}\n\n"
        "是否值得深度分析？只回答 YES 或 NO。"
    )
    try:
        # max_tokens≥100：v4-flash 思考型模型在 max_tokens 过小时输出被思考 token 吃光返回空
        r = chat(prompt, system=system, max_tokens=100, timeout=60, model_key="lite_model")
    except LLMError:
        return True  # 召回优先：筛选失败放行，别漏掉重要新闻
    return r.strip().upper().startswith("YES")


if __name__ == "__main__":
    cfg = get_config()
    print("LLM 配置状态:", "已启用" if cfg["enabled"] else "未配置")
    print("  base_url:", cfg["base_url"] or "（空）")
    print("  model:", cfg["model"] or "（空）")
    print("  api_key:", "***" if cfg["api_key"] else "（空）")
    if cfg["enabled"]:
        r = chat("用一句话介绍你自己。", system="你是测试助手。", max_tokens=50)
        print("连通测试:", r)
