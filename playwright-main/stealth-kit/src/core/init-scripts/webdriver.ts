/**
 * navigator.webdriver
 *
 * Playwright never hides this flag (verified: no override in the server
 * sources), and any automation-driven Chrome sets it to `true`. This is the
 * single highest-priority patch — almost every detector checks it first.
 */
export function makeWebdriverInitScript(): () => void {
  return () => {
    // Delete any own property first, then install a prototype getter that
    // reports `undefined` — the value a genuine, non-automated browser has.
    try {
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      delete (navigator as any).webdriver;
    } catch {
      /* non-configurable in some engines; the prototype getter below still wins */
    }
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined,
      configurable: true,
    });
  };
}
