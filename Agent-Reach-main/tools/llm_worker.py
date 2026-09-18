# -*- coding: utf-8 -*-
"""LLM 调用子进程 worker：由 llm_interpret.chat() 通过 subprocess 启动。

stdin 收 JSON 请求 {prompt, system, timeout, max_tokens}，
stdout 回 JSON 结果 {"ok": true, "text": ...} 或 {"ok": false, "error": ...}。

真实网络调用 _chat_direct 放在这里执行：DeepSeek API 偶发原生崩溃
（进程直接退出、无 traceback），与报告主进程隔离，崩溃只影响本 worker。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from llm_interpret import _chat_direct  # noqa: E402


def main() -> int:
    try:
        req = json.loads(sys.stdin.read() or "{}")
        text = _chat_direct(
            req.get("prompt", ""),
            system=req.get("system", ""),
            timeout=int(req.get("timeout", 90)),
            max_tokens=int(req.get("max_tokens", 500)),
            model_key=req.get("model_key", "model"),
        )
        print(json.dumps({"ok": True, "text": text}, ensure_ascii=False))
        return 0
    except BaseException as e:  # noqa: BLE001 —— 任何失败都转 JSON 错误，由主进程回退模板
        print(json.dumps({"ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}"},
                         ensure_ascii=False))
        return 1


if __name__ == "__main__":
    sys.exit(main())
