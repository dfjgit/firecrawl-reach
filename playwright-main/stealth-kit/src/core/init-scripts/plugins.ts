/**
 * navigator.plugins / navigator.mimeTypes
 *
 * A headless or freshly-automated browser reports an empty PluginArray,
 * which is a strong automation signal. We fabricate the standard set of
 * built-in PDF plugins that every real desktop Chrome ships.
 *
 * The fakes are built with the genuine prototypes (PluginArray.prototype
 * etc.) so `instanceof` and `Object.prototype.toString` checks still pass,
 * and functions are given native-looking toString output to survive
 * `Function.prototype.toString` probing.
 */
export function makePluginsInitScript(): () => void {
  return () => {
    // Makes a function look like a native method when toString()'d.
    const nativeCode = (name: string) =>
      `function ${name}() { [native code] }`;
    const disguise = <F extends Function>(fn: F, name: string): F => {
      Object.defineProperty(fn, 'toString', {
        value: () => nativeCode(name),
        configurable: true,
      });
      return fn;
    };

    const pluginData = [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Microsoft Edge PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'WebKit built-in PDF', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    ];
    const mimeData = [
      { type: 'application/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
      { type: 'text/pdf', suffixes: 'pdf', description: 'Portable Document Format' },
    ];

    const mimeTypes = mimeData.map(m => {
      const mt = Object.create(MimeType.prototype);
      Object.defineProperties(mt, {
        type: { get: () => m.type },
        suffixes: { get: () => m.suffixes },
        description: { get: () => m.description },
        enabledPlugin: { get: () => plugins[0] },
      });
      return mt as MimeType;
    });

    const plugins = pluginData.map(p => {
      const plugin = Object.create(Plugin.prototype);
      Object.defineProperties(plugin, {
        name: { get: () => p.name },
        filename: { get: () => p.filename },
        description: { get: () => p.description },
        length: { get: () => mimeTypes.length },
        item: { value: disguise((i: number) => mimeTypes[i], 'item') },
        namedItem: { value: disguise((n: string) => mimeTypes.find(m => m.type === n), 'namedItem') },
      });
      mimeTypes.forEach((mt, i) => {
        Object.defineProperty(plugin, i, { get: () => mt });
        Object.defineProperty(plugin, mt.type, { get: () => mt });
      });
      return plugin as Plugin;
    });

    const pluginArray = Object.create(PluginArray.prototype);
    plugins.forEach((p, i) => {
      Object.defineProperty(pluginArray, i, { get: () => p });
      Object.defineProperty(pluginArray, p.name, { get: () => p });
    });
    Object.defineProperties(pluginArray, {
      length: { get: () => plugins.length },
      item: { value: disguise((i: number) => plugins[i], 'item') },
      namedItem: { value: disguise((n: string) => plugins.find(p => p.name === n), 'namedItem') },
      refresh: { value: disguise(() => undefined, 'refresh') },
    });

    const mimeTypeArray = Object.create(MimeTypeArray.prototype);
    mimeTypes.forEach((mt, i) => {
      Object.defineProperty(mimeTypeArray, i, { get: () => mt });
      Object.defineProperty(mimeTypeArray, mt.type, { get: () => mt });
    });
    Object.defineProperties(mimeTypeArray, {
      length: { get: () => mimeTypes.length },
      item: { value: disguise((i: number) => mimeTypes[i], 'item') },
      namedItem: { value: disguise((n: string) => mimeTypes.find(m => m.type === n), 'namedItem') },
    });

    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => pluginArray,
      configurable: true,
    });
    Object.defineProperty(Navigator.prototype, 'mimeTypes', {
      get: () => mimeTypeArray,
      configurable: true,
    });
  };
}
