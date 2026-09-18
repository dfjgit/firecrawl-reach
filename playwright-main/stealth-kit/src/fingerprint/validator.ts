import type { FingerprintProfile } from './profiles';

export class ProfileValidationError extends Error {
  constructor(
    public readonly profileId: string,
    public readonly problems: string[],
  ) {
    super(`Invalid fingerprint profile "${profileId}":\n  - ${problems.join('\n  - ')}`);
    this.name = 'ProfileValidationError';
  }
}

function chromeMajorVersion(userAgent: string): string | undefined {
  // Real desktop Chrome freezes the tail as "Chrome/<major>.0.0.0".
  const m = userAgent.match(/Chrome\/(\d+)\.\d+\.\d+\.\d+/);
  return m?.[1];
}

/**
 * Cross-consistency checks run before any context is created. A profile whose
 * surfaces contradict each other (Mac UA + Win32 platform, UA version ≠
 * UA-CH version, ...) is the single most common way detectors catch stealth
 * setups, so we fail hard instead of shipping a broken identity.
 */
export function validateProfile(profile: FingerprintProfile): void {
  const problems: string[] = [];
  const { userAgent: ua, userAgentMetadata: meta } = profile;

  if (ua.includes('HeadlessChrome')) {
    problems.push('userAgent must not contain "HeadlessChrome"');
  }

  // --- UA platform vs navigator.platform ---
  if (ua.includes('Windows')) {
    if (profile.platform !== 'Win32') {
      problems.push(`UA says Windows but platform is "${profile.platform}" (expected "Win32")`);
    }
    if (meta.platform !== 'Windows') {
      problems.push(`UA says Windows but userAgentMetadata.platform is "${meta.platform}"`);
    }
  } else if (ua.includes('Mac OS X') || ua.includes('Macintosh')) {
    if (profile.platform !== 'MacIntel') {
      problems.push(`UA says macOS but platform is "${profile.platform}" (expected "MacIntel")`);
    }
    if (meta.platform !== 'macOS') {
      problems.push(`UA says macOS but userAgentMetadata.platform is "${meta.platform}"`);
    }
  } else if (ua.includes('Linux')) {
    if (!profile.platform.startsWith('Linux')) {
      problems.push(`UA says Linux but platform is "${profile.platform}"`);
    }
    if (meta.platform !== 'Linux') {
      problems.push(`UA says Linux but userAgentMetadata.platform is "${meta.platform}"`);
    }
  } else {
    problems.push('userAgent platform token not recognized (expected Windows / Mac OS X / Linux)');
  }

  // --- UA Chrome major version vs UA-CH brands ---
  const uaMajor = chromeMajorVersion(ua);
  if (!uaMajor) {
    problems.push('could not parse Chrome version from userAgent');
  } else {
    const brandEntry = meta.brands.find(b => b.brand === 'Chromium' || b.brand === 'Google Chrome');
    if (!brandEntry) {
      problems.push('userAgentMetadata.brands has no Chromium/Google Chrome entry');
    } else if (brandEntry.version !== uaMajor) {
      problems.push(
        `UA Chrome major version ${uaMajor} != brands["${brandEntry.brand}"] = ${brandEntry.version}`,
      );
    }
    const fullEntry = meta.fullVersionList.find(
      b => b.brand === 'Chromium' || b.brand === 'Google Chrome',
    );
    if (!fullEntry) {
      problems.push('userAgentMetadata.fullVersionList has no Chromium/Google Chrome entry');
    } else if (!fullEntry.version.startsWith(`${uaMajor}.`)) {
      problems.push(
        `UA Chrome major version ${uaMajor} != fullVersionList["${fullEntry.brand}"] = ${fullEntry.version}`,
      );
    }
    if (!meta.fullVersion.startsWith(`${uaMajor}.`)) {
      problems.push(`userAgentMetadata.fullVersion "${meta.fullVersion}" != UA major ${uaMajor}`);
    }
  }

  // --- Geometry sanity ---
  const { viewport, screen } = profile;
  const inRange = (n: number) => n >= 320 && n <= 7680;
  if (!inRange(viewport.width) || !inRange(viewport.height)) {
    problems.push(`viewport ${viewport.width}x${viewport.height} is outside a plausible range`);
  }
  if (screen) {
    if (!inRange(screen.width) || !inRange(screen.height)) {
      problems.push(`screen ${screen.width}x${screen.height} is outside a plausible range`);
    }
    if (screen.width < viewport.width || screen.height < viewport.height) {
      problems.push(
        `screen ${screen.width}x${screen.height} is smaller than viewport ${viewport.width}x${viewport.height}`,
      );
    }
  }

  if (problems.length > 0) throw new ProfileValidationError(profile.id, problems);
}
