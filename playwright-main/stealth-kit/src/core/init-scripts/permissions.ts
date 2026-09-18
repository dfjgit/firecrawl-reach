/**
 * navigator.permissions.query
 *
 * In automated browsers the Permissions API and the Notification API can
 * disagree (e.g. query({name:'notifications'}) reports "prompt" while
 * Notification.permission is "denied"). Detectors diff the two. We re-derive
 * the notifications entry from Notification.permission so both surfaces
 * always agree, and disguise the wrapper as a native function.
 */
export function makePermissionsInitScript(): () => void {
  return () => {
    if (!navigator.permissions || typeof navigator.permissions.query !== 'function') return;

    const original = navigator.permissions.query.bind(navigator.permissions);

    const query = (descriptor: PermissionDescriptor): Promise<PermissionStatus> => {
      if (descriptor.name === 'notifications' && typeof Notification !== 'undefined') {
        // Mirror Notification.permission instead of trusting the raw result.
        const state = Notification.permission as PermissionState;
        const status = Object.create(PermissionStatus.prototype);
        Object.defineProperties(status, {
          state: { get: () => state },
          onchange: { value: null, writable: true },
        });
        return Promise.resolve(status as PermissionStatus);
      }
      return original(descriptor);
    };

    Object.defineProperty(query, 'toString', {
      value: () => 'function query() { [native code] }',
      configurable: true,
    });
    // Patch the live Permissions instance in place — replacing
    // navigator.permissions wholesale would break `instanceof Permissions`.
    Object.defineProperty(navigator.permissions, 'query', {
      value: query,
      configurable: true,
    });
  };
}
