/**
 * Fingerprint profiles and the checkout/checkin pool.
 *
 * A profile is the single source of truth for every fingerprint surface
 * (UA, UA-CH metadata, navigator.*, WebGL, headers, proxy). All fields must
 * be mutually consistent — `validator.ts` enforces this before a context is
 * created, because detectors mostly catch *cross-surface contradictions*.
 */

/** Mirrors CDP `Network.UserAgentMetadata` so it can be passed verbatim to Network.setUserAgentOverride. */
export interface UserAgentBrandVersion {
  brand: string;
  version: string;
}

export interface UserAgentMetadata {
  brands: UserAgentBrandVersion[];
  fullVersionList: UserAgentBrandVersion[];
  platform: string; // "Windows" | "macOS" | ...
  platformVersion: string;
  architecture: string;
  bitness: string;
  model: string;
  mobile: boolean;
  fullVersion: string;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface FingerprintProfile {
  id: string;
  /** Pool filter labels, e.g. 'us-desktop', 'windows'. */
  tags: string[];
  userAgent: string;
  /** sec-ch-ua / navigator.userAgentData source, in CDP wire shape. */
  userAgentMetadata: UserAgentMetadata;
  viewport: Viewport;
  /** Defaults to viewport when omitted. */
  screen?: Viewport;
  deviceScaleFactor: number;
  locale: string;
  timezoneId: string;
  /** navigator.platform: 'Win32' | 'MacIntel' | 'Linux x86_64' */
  platform: string;
  /** Value returned for WebGL UNMASKED_RENDERER_WEBGL. */
  webglRenderer: string;
  /** Value returned for WebGL UNMASKED_VENDOR_WEBGL, e.g. 'Google Inc. (NVIDIA)'. */
  webglVendor: string;
  /** Fixed seed driving canvas noise — same profile must be reproducible. */
  canvasNoiseSeed: number;
  /** Opaque key resolved to a Playwright proxy config by a user-supplied ProxyProvider. */
  proxyId: string;
  hardwareConcurrency: number;
  deviceMemory: number;
}

const CHROME_148_BRANDS: UserAgentBrandVersion[] = [
  { brand: 'Not/A)Brand', version: '8' },
  { brand: 'Chromium', version: '148' },
  { brand: 'Google Chrome', version: '148' },
];

const CHROME_148_FULL_VERSION_LIST: UserAgentBrandVersion[] = [
  { brand: 'Not/A)Brand', version: '8.0.0.0' },
  { brand: 'Chromium', version: '148.0.7778.96' },
  { brand: 'Google Chrome', version: '148.0.7778.96' },
];

/**
 * Built-in sample profiles.
 *
 * NOTE: these are *examples* to make the kit runnable out of the box.
 * Production deployments should replace them with profiles harvested from
 * real browsers (see DESIGN.md §3: real captures beat invented values).
 */
export const BUILTIN_PROFILES: FingerprintProfile[] = [
  {
    id: 'sample-win-chrome-desktop',
    tags: ['us-desktop', 'windows', 'desktop'],
    userAgent:
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
      '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    userAgentMetadata: {
      brands: CHROME_148_BRANDS,
      fullVersionList: CHROME_148_FULL_VERSION_LIST,
      platform: 'Windows',
      platformVersion: '15.0.0',
      architecture: 'x86',
      bitness: '64',
      model: '',
      mobile: false,
      fullVersion: '148.0.7778.96',
    },
    viewport: { width: 1920, height: 1080 },
    screen: { width: 1920, height: 1080 },
    deviceScaleFactor: 1,
    locale: 'en-US',
    timezoneId: 'America/New_York',
    platform: 'Win32',
    webglRenderer:
      'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060 (0x00002503) Direct3D11 vs_5_0 ps_5_0, D3D11)',
    webglVendor: 'Google Inc. (NVIDIA)',
    canvasNoiseSeed: 0x5eed0001,
    proxyId: 'us-east-residential-01',
    hardwareConcurrency: 16,
    deviceMemory: 8,
  },
  {
    id: 'sample-mac-chrome-desktop',
    tags: ['us-desktop', 'mac', 'desktop'],
    userAgent:
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 ' +
      '(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36',
    userAgentMetadata: {
      brands: CHROME_148_BRANDS,
      fullVersionList: CHROME_148_FULL_VERSION_LIST,
      platform: 'macOS',
      platformVersion: '14.5.0',
      architecture: 'x86',
      bitness: '64',
      model: '',
      mobile: false,
      fullVersion: '148.0.7778.96',
    },
    viewport: { width: 1440, height: 900 },
    screen: { width: 1440, height: 900 },
    deviceScaleFactor: 2,
    locale: 'en-US',
    timezoneId: 'America/Los_Angeles',
    platform: 'MacIntel',
    webglRenderer: 'ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)',
    webglVendor: 'Google Inc. (Apple)',
    canvasNoiseSeed: 0x5eed0002,
    proxyId: 'us-west-residential-01',
    hardwareConcurrency: 8,
    deviceMemory: 8,
  },
];

/**
 * Hands profiles out and takes them back so concurrent workers never share
 * one identity. checkout(tag) filters by label; a checked-out profile is
 * skipped until checkin returns it.
 */
export class ProfilePool {
  private readonly profiles: FingerprintProfile[];
  private readonly inUse = new Set<string>();

  constructor(profiles: FingerprintProfile[] = BUILTIN_PROFILES) {
    if (profiles.length === 0) throw new Error('ProfilePool requires at least one profile');
    this.profiles = [...profiles];
  }

  checkout(tag?: string): FingerprintProfile {
    const candidates = this.profiles.filter(p => !tag || p.tags.includes(tag));
    if (candidates.length === 0) {
      throw new Error(`ProfilePool: no profile matches tag "${tag ?? '*'}"`);
    }
    const free = candidates.find(p => !this.inUse.has(p.id));
    if (!free) {
      throw new Error(
        `ProfilePool: all ${candidates.length} profile(s) matching "${tag ?? '*'}" are checked out`,
      );
    }
    this.inUse.add(free.id);
    return free;
  }

  checkin(profile: FingerprintProfile | string): void {
    const id = typeof profile === 'string' ? profile : profile.id;
    this.inUse.delete(id);
  }

  /** Non-destructive lookup, does not mark the profile in use. */
  find(id: string): FingerprintProfile | undefined {
    return this.profiles.find(p => p.id === id);
  }

  list(tag?: string): FingerprintProfile[] {
    return this.profiles.filter(p => !tag || p.tags.includes(tag));
  }
}
