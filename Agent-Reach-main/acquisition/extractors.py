# -*- coding: utf-8 -*-
"""列表页 → 条目的三档抽取（M1 实施 L1/L2，L0 由 rss 适配器承担）。

原则：能用低档不用高档；切不动就不切（L2 整页快照兜底），
精细结构化交给下游 LLM，管道永不被解析器卡死。
"""

import re


def split_by_pattern(text: str, pattern: str, max_items: int) -> list:
    """L1 规则切分：按行首正则（如电报 ``^\\d{2}:\\d{2}``）把全文粗切成条目。

    每个匹配位置到下一个匹配位置之间算一条；返回至多 max_items 条。
    一个匹配都没有时返回空列表（调用方应退回 L2）。
    """
    matches = list(re.finditer(pattern, text, re.MULTILINE))
    if not matches:
        return []
    chunks = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if len(chunks) >= max_items:
            break
    return chunks


def make_snapshot(text: str, max_chars: int = 4000) -> str:
    """L2 整页快照：切不动时把干净全文截断成一条"页面快照"条目。"""
    return text.strip()[:max_chars]
