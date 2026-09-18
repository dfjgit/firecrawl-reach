import { launch } from './core/launcher';
import { newContext } from './core/context-factory';
import { humanClick, moveMouse } from './behavior/mouse';
import { humanType } from './behavior/typing';
import { humanScroll } from './behavior/scroll';

export const stealth = { launch, newContext };
export const human = {
  click: humanClick,
  type: humanType,
  scroll: humanScroll,
  moveMouse,
};

export { ProfilePool, BUILTIN_PROFILES } from './fingerprint/profiles';
export type { FingerprintProfile, UserAgentMetadata, Viewport } from './fingerprint/profiles';
export { validateProfile, ProfileValidationError } from './fingerprint/validator';
export type { StealthLaunchOptions } from './core/launcher';
export type { StealthContextOptions } from './core/context-factory';
export type { ProxyProvider } from './network/proxy';
