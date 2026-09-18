"""
navigator.webdriver

Playwright never hides this flag (verified: no override in the server
sources), and any automation-driven Chrome sets it to `true`. This is the
single highest-priority patch — almost every detector checks it first.

JS logic extracted verbatim from stealth-kit/src/core/init-scripts/webdriver.ts.
"""

WEBDRIVER_INIT_SCRIPT = r"""
// Delete any own property first, then install a prototype getter that
// reports `undefined` — the value a genuine, non-automated browser has.
try {
  delete navigator.webdriver;
} catch (e) {
  /* non-configurable in some engines; the prototype getter below still wins */
}
Object.defineProperty(Navigator.prototype, 'webdriver', {
  get: () => undefined,
  configurable: true,
});
"""


def make_webdriver_init_script() -> str:
    return WEBDRIVER_INIT_SCRIPT
