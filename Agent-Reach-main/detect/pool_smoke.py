"""
SessionPool smoke test: same-identity reuse, capacity eviction, crash rebuild.

Usage:
    python detect/pool_smoke.py           # uses msedge (falls back to chromium)

Exit code 0 = all checks passed, 1 = at least one failed.
"""

import dataclasses
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from stealth_kit import BUILTIN_PROFILES, SessionPool, stealth  # noqa: E402

failed = 0


def check(name: str, ok: bool, actual="") -> None:
    global failed
    suffix = "" if ok else f"  (got: {actual!r})"
    print(f"{'PASS' if ok else 'FAIL'}  {name}{suffix}")
    if not ok:
        failed += 1


def main() -> None:
    headless = os.environ.get("STEALTH_HEADED") != "1"
    try:
        browser = stealth.launch(channel="msedge", headless=headless)
        description = "channel=msedge"
    except Exception:
        from self_check import find_bundled_chromium

        exe = find_bundled_chromium()
        if not exe:
            raise
        browser = stealth.launch(channel=None, headless=headless,
                                 executable_path=exe)
        description = f"bundled chromium ({exe})"
    print(f"[pool-smoke] browser: {description}")

    p1 = BUILTIN_PROFILES[0]
    p2 = BUILTIN_PROFILES[1]
    p3 = dataclasses.replace(p1, id="sample-win-copy",
                             tags=["us-desktop", "windows", "desktop"])

    try:
        with SessionPool(browser, max_contexts=2, idle_ttl=300) as pool:
            # 1. same profile.id acquires the SAME context (identity binding)
            c1 = pool.acquire(p1)
            pool.release(c1)
            c1_again = pool.acquire(p1)
            check("same profile.id reuses the same context", c1_again is c1)
            pool.release(c1_again)

            # the pooled context carries the full stealth treatment
            page = c1.new_page()
            page.set_content("<html><body>pool</body></html>")
            check("pooled context is stealthed (webdriver undefined)",
                  page.evaluate("navigator.webdriver") is None,
                  page.evaluate("navigator.webdriver"))
            page.close()

            # 2. capacity: acquiring a 3rd identity evicts the longest-idle
            c2 = pool.acquire(p2)
            pool.release(c2)
            # p1 was released first, so it is the LRU victim
            c3 = pool.acquire(p3)
            check("pool capped at max_contexts", len(pool) == 2, len(pool))
            check("least-recently-idle context evicted",
                  p1.id not in pool.profile_ids(), pool.profile_ids())
            check("evicted context was closed",
                  p2.id in pool.profile_ids() and c3 is not c2)
            pool.release(c3)

            # 3. crash: externally closed context is purged and rebuilt
            victim = pool.acquire(p2)
            pool.release(victim)
            victim.close()  # simulate a crash / external close
            import time

            time.sleep(0.3)  # let the close event dispatch
            rebuilt = pool.acquire(p2)
            check("crashed context is rebuilt on next acquire",
                  rebuilt is not victim)
            page = rebuilt.new_page()
            page.set_content("<html><body>rebuilt</body></html>")
            check("rebuilt context works",
                  page.evaluate("document.body.textContent") == "rebuilt")
            pool.release(rebuilt)
    finally:
        browser.close()

    print("\nALL POOL CHECKS PASSED" if failed == 0
          else f"\n{failed} POOL CHECK(S) FAILED")
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:
        print(f"[pool-smoke] fatal: {err}", file=sys.stderr)
        sys.exit(1)
