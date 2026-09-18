# Stealth-Kit 设计方案

> Playwright 反检测（Stealth）用户层封装 —— 内部爬虫团队使用。
> 约束：**不修改 Playwright 源码**，全部能力通过公开 API 实现，可随上游版本升级。

## 0. 设计前提

基于对 Playwright 源码（`packages/playwright-core`）的实际勘察，三个事实决定方案形状：

1. **Playwright 默认启动参数已经比较干净**：不传 `--enable-automation`，也不传
   `--disable-blink-features=AutomationControlled`
   （见 `packages/playwright-core/src/server/chromium/chromiumSwitches.ts:54-99`）。
   封装层是"补漏"，不是"重写"。
2. **`navigator.webdriver` 没有任何内置处理**（全仓库搜索 `webdriver` 在 server 源码中无覆盖逻辑）。
   这是必须自补的第一优先项。
3. **二进制指纹在用户层约束下无法真正修补**，但有等效手段：`channel: 'chrome'`
   （类型定义 `packages/playwright-core/types/types.d.ts:25250`，注册逻辑
   `packages/playwright-core/src/server/registry/index.ts:599-603`）直接驱动系统安装的真实
   Chrome，绕开内置 Chromium 的二进制差异。这是本方案对"浏览器二进制指纹"的应对方式。

## 1. 总体架构

```
stealth-kit/
├── src/
│   ├── core/
│   │   ├── launcher.ts        # 启动器：channel、args、ignoreDefaultArgs 策略
│   │   ├── context-factory.ts # Context 工厂：指纹一致性组装
│   │   ├── init-scripts/      # 注入脚本（每个文件负责一个指纹面）
│   │   └── cdp.ts             # CDP 增强：UA override、Emulation
│   ├── fingerprint/
│   │   ├── profiles.ts        # 指纹档案库
│   │   └── validator.ts       # 指纹一致性校验
│   ├── behavior/
│   │   ├── mouse.ts           # 贝塞尔轨迹移动
│   │   ├── typing.ts          # 人类节奏输入
│   │   └── scroll.ts
│   ├── network/
│   │   ├── headers.ts         # 请求头规范化
│   │   └── proxy.ts           # 代理/TLS 层对接说明
│   └── index.ts               # 对外 API：stealth.launch / stealth.newContext
├── detect/                    # 自检：已知检测面回归
└── DESIGN.md
```

核心原则：所有能力只通过公开 API 实现 —— `addInitScript`、`newCDPSession`、
`launch({channel, args, ignoreDefaultArgs})`、`context.route`、`newContext(options)`。
Playwright 升级时只需回归 `detect/` 测试套件。

## 2. 各检测面对策

### 2.1 JS 指纹（init script 层）

注入机制本身干净：`addInitScript` 在 Chromium 下走 CDP
`Page.addScriptToEvaluateOnNewDocument`
（`packages/playwright-core/src/server/chromium/crPage.ts:227-229`），
主世界执行、无 script 标签 DOM 痕迹。封装层覆盖：

| 指纹面 | 对策 |
| --- | --- |
| `navigator.webdriver` | `Object.defineProperty` 删除/置 `undefined`（最高优先级） |
| `navigator.plugins` / `mimeTypes` | 伪造非空数组（headless 下为空是强特征） |
| `navigator.permissions.query` | 修正 `notifications` 等返回不一致项 |
| WebGL | `UNMASKED_VENDOR_WEBGL` / `UNMASKED_RENDERER_WEBGL` 改为与档案一致的真实 GPU 字符串 |
| Canvas | 固定噪声种子（同一 profile 每次结果相同；随机漂移本身即异常） |
| `navigator.languages` / `platform` / `userAgentData` | 与 UA 交叉一致 |

**一致性是灵魂**：`userAgent` 与 `navigator.platform`、`sec-ch-ua`、`userAgentData`
必须互相对得上，检测器最常抓交叉矛盾。由 `fingerprint/validator.ts` 在 context 创建前静态校验。

每个面一个独立 init script 文件，按需组合，避免巨型脚本难调试。

### 2.2 CDP/协议特征

- Playwright 会注入一个隔离世界，world 名形如 `__playwright_utility_world_${guid}`
  （`crPage.ts:92`）。检测方若监听 `Runtime.enable` 的 `executionContextCreated` 事件可见。
  **用户层无法改名**，列入已知残留风险（§5）；缓解方式是 `channel: 'chrome'` + 减少页面存活期内的可疑操作。
- 不做高频 `Runtime.evaluate` 轮询；等待用 Playwright 自带的 `waitForSelector` 等。
- 用 `context.newCDPSession(page)`
  （`packages/playwright-core/src/client/browserContext.ts:501-507`）做**正向增强**：
  - `Network.setUserAgentOverride`（含 `userAgentMetadata`，让 `sec-ch-ua-*` 系列头与 JS 层一致）
  - `Emulation.setTimezoneOverride` 等
  
  协议层修改不会被页面 JS 探测到 `defineProperty` 痕迹，优先于 JS hack。

### 2.3 浏览器二进制/启动特征

- 首选 `channel: 'chrome'`（或 `msedge`），跑真实浏览器二进制。
- 避免 headless；若必须无头，用 Chromium 新 headless（Playwright 默认路径，
  `chromium.ts:380`），并保证 UA 不含 `HeadlessChrome`。
- 用 `ignoreDefaultArgs`（实现 `packages/playwright-core/src/server/browserType.ts:152,179-180`）
  剔除个别带来特征的默认开关；`args` 追加必要参数（`chromium.ts:409`）。
  注意 `ignoreDefaultArgs` 是**精确字符串匹配**，过滤清单随 Playwright 版本回归。

### 2.4 行为/网络特征

- **鼠标**：贝塞尔曲线分段移动 + 随机微抖动；点击前自然 hover。敏感操作不用 `page.click` 的瞬移默认值。
- **输入**：按键间隔对数正态分布，偶发退格修正。
- **时序**：操作间随机停顿，避免恒定 sleep 的节拍器特征。
- **请求头**：`context.route` + `route.continue({ headers })`
  （`packages/playwright-core/src/client/network.ts:76-88`）可改 `sec-ch-ua` 等。
  两个已知坑：forbidden headers 会被透传（`crNetworkManager.ts:347-351`）；重定向时 cookie 头被剥掉。
  **头顺序无法通过 route 完全控制**，敏感目标走代理层（§2.5）。
- **TLS/JA3**：Playwright/CDP 改不了 TLS 握手指纹。正确做法在代理层解决 ——
  浏览器走本地代理（curl-impersonate / uTLS 类转发器），由代理以目标浏览器的 JA3 发起上游 TLS。
  这是 `network/proxy.ts` 存在的意义，不在 Node 层 hack。

## 3. 指纹档案（Fingerprint Profile）

内部团队使用：**档案池**，不每次随机生成。

```ts
interface FingerprintProfile {
  id: string;
  userAgent: string;           // 与下方字段必须交叉一致
  userAgentMetadata: object;   // sec-ch-ua / userAgentData
  viewport: { width: number; height: number };
  deviceScaleFactor: number;
  locale: string;
  timezoneId: string;
  platform: string;
  webglRenderer: string;
  canvasNoiseSeed: number;     // 固定种子，同 profile 可复现
  proxyId: string;             // 指纹与出口 IP 地理绑定
}
```

关键规则：

1. 一个 profile 的所有维度必须自洽（Mac UA 配 Win 的 `navigator.platform` 是低级错误）。
2. profile 与代理 IP 的地理/时区绑定。
3. 档案库从真实浏览器采集生成，比凭空编造可靠。

Context 创建时用 `newContext` 现成选项落地大部分字段
（`types.d.ts:17300-17740`：`userAgent`、`viewport`、`timezoneId`、`locale`、
`deviceScaleFactor`、`screen` 等），其余走 init script + CDP。

## 4. 使用侧 API

```ts
const browser = await stealth.launch({
  channel: 'chrome',
  headless: false,
});

const context = await stealth.newContext(browser, {
  profile: profilePool.checkout('us-desktop'),  // 指纹+代理一体取出
});

const page = await context.newPage();
await human.click(page, 'text=Submit');   // 行为层包装
```

`stealth.launch` 内部组装 channel/args；`stealth.newContext` 完成：
profile 校验 → context 选项 → init scripts 注入 → CDP override → 路由层头规范化。

## 5. 已知残留风险

- `__playwright_utility_world_*` world 名（源码级，封装层不可消除）。
- CDP `Runtime.enable` 的副作用可被极端检测方利用。
- 头的线序与 HTTP/2 指纹只能到代理层控制。

## 6. 验证体系（`detect/`）

反检测能力没有测试就等于没有。回归套件定期跑：

- 自研检测页：采集上述全部指纹面，断言 `webdriver===undefined`、UA/platform/UA-CH 一致等硬指标；
- 公开检测页（人工/半自动）：bot.sannysoft.com、fingerprint.com demo、creepjs，记录得分快照；
- CI 中对 Playwright 版本升级做门禁。
