# stealth-kit

> **状态：冻结（reference only）。** 本目录是 TypeScript 参考实现。功能已完整移植至
> Python 版 `stealth_kit` 包（Playwright Python sync API），现位于
> 本伞形项目内 `Agent-Reach-main/stealth_kit/`（`../../Agent-Reach-main/stealth_kit/`）；
> 后续演进（profile 对齐、会话池、
> 采集工具、TLS 代理等）都在那边进行；TS 版不再更新，除非 Node 生态另有需求。

Playwright 反检测（stealth）用户层封装。**只用 Playwright 公开 API，不修改 Playwright 源码。**
设计依据见 `DESIGN.md`。

## 用法

```ts
import { stealth, human, ProfilePool } from 'stealth-kit';

const pool = new ProfilePool(); // 内置示例档案；生产替换为真实采集
const profile = pool.checkout('us-desktop');

const browser = await stealth.launch({ channel: 'chrome', headless: false });
const context = await stealth.newContext(browser, {
  profile,
  // 可选：把 profile.proxyId 解析为 Playwright proxy 配置
  proxyProvider: id => ({ server: `http://proxy.internal:8080`, username: 'u', password: 'p' }),
});
const page = await context.newPage();
await page.goto('https://example.com');

await human.click(page, 'text=Submit');   // 贝塞尔轨迹 + 先 hover 再点
await human.type(page, '#q', 'hello');    // 对数正态按键间隔 + 偶发退格
await human.scroll(page, { distance: 800 });

pool.checkin(profile);
```

## 结构

- `src/fingerprint/` — 指纹档案池 + 交叉一致性校验（context 创建前 fail-fast）
- `src/core/init-scripts/` — 每个指纹面一个注入脚本（webdriver / plugins / permissions / WebGL / Canvas / navigator.\*）
- `src/core/` — launcher（channel、args、ignoreDefaultArgs）、context 工厂、CDP 增强（UA override + 时区）
- `src/network/` — 请求头规范化（sec-ch-ua 等）、代理对接（TLS/JA3 说明在 proxy.ts 注释）
- `src/behavior/` — 拟人鼠标/键盘/滚动
- `detect/` — 自检与公开检测站回归说明（`npm run self-check`）

## 构建与自检

```bash
npm install
npm run typecheck   # tsc --noEmit
npm run self-check  # 构建并跑 detect/self-check（可用 STEALTH_CHANNEL / STEALTH_HEADED 控制）
```

## 已知残留风险

- `__playwright_utility_world_*` isolated world 名（源码级，用户层不可消除）
- CDP `Runtime.enable` 副作用可被极端检测方利用
- 请求头线序 / HTTP/2 指纹 / TLS JA3 只能到代理层解决（curl-impersonate / uTLS）
