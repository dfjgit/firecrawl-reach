"""
TLS / HTTP fingerprint probe (the measuring stick for the proxy layer).

Neither Playwright nor CDP can rewrite the browser's TLS ClientHello, so
JA3/JA4 can only be fixed at the proxy layer (DESIGN.md §2.5). This script
visits a fingerprint echo service through a fully stealthed context and
prints what the other side actually sees — JA3/JA4, HTTP/2 fingerprint, and
the on-the-wire request headers — diffed against what the (aligned) profile
claims. Deploy your curl-impersonate / uTLS forwarder, then re-run this to
verify the JA3 actually changed.

Usage:
    python detect/fingerprint_check.py [--via-proxy]

    --via-proxy (or STEALTH_VIA_PROXY=1) routes the stealthed context
    through the Go uTLS sidecar (network/tls_proxy.py); the JA3/JA4 then
    shows a Chrome ClientHello instead of the real browser's.

Environment:
    STEALTH_FP_URL   echo endpoint (default https://tls.peet.ws/api/all)
    STEALTH_CHANNEL / STEALTH_HEADED / STEALTH_PROFILE_JSON  as in self_check

Exit code 0 = probe completed (diffs are reported, not gated),
          2 = network unreachable / endpoint error (NOT a stealth failure),
          1 = local error.
"""

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from self_check import launch_browser, load_profile  # noqa: E402

from stealth_kit import stealth  # noqa: E402
from stealth_kit.core.align import align_profile_to_browser  # noqa: E402


def _normalize_headers(raw) -> dict:
    """Accepts ['Name: value', ...] or [{'name':..,'value':..}, ...] or dict."""
    headers = {}
    if isinstance(raw, dict):
        return {str(k).lower(): str(v) for k, v in raw.items()}
    for item in raw or []:
        if isinstance(item, str) and ": " in item:
            k, v = item.split(": ", 1)
            headers[k.lower()] = v
        elif isinstance(item, dict):
            headers[str(item.get("name", "")).lower()] = str(item.get("value", ""))
    return headers


def _extract_headers(data: dict) -> dict:
    """Pulls request headers out of a tls.peet.ws response: h2 HEADERS frame
    first, h1 header list as fallback."""
    for frame in (data.get("http2", {}) or {}).get("sent_frames", []) or []:
        if frame.get("frame_type") == "HEADERS":
            return _normalize_headers(frame.get("headers"))
    return _normalize_headers((data.get("http1", {}) or {}).get("headers"))


def main() -> None:
    via_proxy = ("--via-proxy" in sys.argv
                 or os.environ.get("STEALTH_VIA_PROXY") == "1")
    url = os.environ.get("STEALTH_FP_URL", "https://tls.peet.ws/api/all")
    profile = load_profile()
    browser, description, channel_used = launch_browser()
    expected = align_profile_to_browser(
        profile, channel=channel_used, browser_version=browser.version
    )

    proxy = None
    if via_proxy:
        from stealth_kit.network.tls_proxy import TlsProxy

        proxy = TlsProxy()
        print(f"[fp-check] browser: {description}, endpoint: {url}")
        print(f"[fp-check] routing via uTLS sidecar at {proxy.address}")
    else:
        print(f"[fp-check] browser: {description}, endpoint: {url} (direct)")

    try:
        context = stealth.new_context(browser, profile=profile, tls_proxy=proxy)
        page = context.new_page()
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        body = page.evaluate("document.body ? document.body.innerText : ''")
        context.close()
    except Exception as err:
        print(f"[fp-check] network unreachable or endpoint error: {err}",
              file=sys.stderr)
        print("[fp-check] exit 2 (not counted as a stealth failure)",
              file=sys.stderr)
        sys.exit(2)
    finally:
        if proxy is not None:
            proxy.stop()
        browser.close()

    try:
        data = json.loads(body)
    except ValueError:
        print(f"[fp-check] endpoint did not return JSON: {body[:200]!r}",
              file=sys.stderr)
        sys.exit(2)

    tls = data.get("tls", {}) or {}
    http2 = data.get("http2", {}) or {}
    print("\n--- TLS / transport fingerprint (what the peer sees) ---")
    print(f"ja3_hash : {tls.get('ja3_hash')}")
    print(f"ja3      : {tls.get('ja3')}")
    print(f"ja4      : {tls.get('ja4')}")
    print(f"peetprint: {tls.get('peetprint_hash')}")
    print(f"http_version: {data.get('http_version')}")
    print(f"http/2   : {http2.get('akamai_fingerprint')}")

    headers = _extract_headers(data)
    print("\n--- wire request headers (fingerprint-relevant) ---")
    for name in ("user-agent", "sec-ch-ua", "sec-ch-ua-mobile",
                 "sec-ch-ua-platform", "accept-language", "accept"):
        if name in headers:
            print(f"{name}: {headers[name]}")

    exp_sec_ch_ua = ", ".join(
        f'"{b.brand}";v="{b.version}"' for b in expected.user_agent_metadata.brands
    )
    base_lang = expected.locale.split("-")[0]
    expectations = {
        "user-agent": expected.user_agent,
        "sec-ch-ua": exp_sec_ch_ua,
        "sec-ch-ua-mobile": "?1" if expected.user_agent_metadata.mobile else "?0",
        "sec-ch-ua-platform": f'"{expected.user_agent_metadata.platform}"',
        "accept-language": f"{expected.locale},{base_lang};q=0.9",
    }
    print("\n--- diff vs (aligned) profile ---")
    diffs = 0
    for name, exp in expectations.items():
        act = headers.get(name)
        if act is None:
            print(f"ABSENT  {name}: expected {exp!r}")
            diffs += 1
        elif act != exp:
            print(f"DIFF    {name}:\n          wire    = {act!r}\n          profile = {exp!r}")
            diffs += 1
        else:
            print(f"MATCH   {name}")
    if diffs == 0:
        print("no header diffs - JS/CDP surfaces and wire agree")
    else:
        print(f"{diffs} header diff(s) - sec-ch-ua on the wire is generated by "
              "the browser itself; fix at the proxy layer if it matters")
    print("\nnote: without --via-proxy the JA3/JA4 above is the REAL browser's "
          "TLS fingerprint; with --via-proxy it is the uTLS sidecar's Chrome "
          "ClientHello. Compare the two to verify the proxy layer works.")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as err:
        print(f"[fp-check] fatal: {err}", file=sys.stderr)
        sys.exit(1)
