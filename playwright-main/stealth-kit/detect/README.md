# detect/ — 反检测回归

## 自动自检

```bash
npm run self-check
```

用 stealth launch + newContext 打开本地页面，采集本包覆盖的全部指纹面并断言与 profile 一致
（`navigator.webdriver`、platform、UA、plugins、languages、WebGL renderer、userAgentData、时区等）。
全部通过 exit 0，任一失败 exit 1 并打印失败项。

环境变量：

- `STEALTH_CHANNEL`：`chrome`（默认）/ `msedge` / `chromium`（扫描本地 ms-playwright 缓存的已下载 Chromium，不触发下载）。
- `STEALTH_HEADED=1`：有头运行（默认无头）。

## 公开检测站（人工 / 半自动回归）

自动自检只覆盖"我们知道自己改了什么"；第三方检测器的未知探针需要定期人工对拍：

- https://bot.sannysoft.com — 经典综合检测表
- https://abrahamjuliot.github.io/creepjs/ — 指纹一致性与"谎言"检测
- https://fingerprint.com/demo — 商业检测 demo
- https://pixelscan.net — UA/字体/硬件一致性

建议：每次升级 Playwright 或改动 profile 后，用 `stealth.launch` + `stealth.newContext`
打开上述站点，截图得分存档对比。Playwright 版本升级时以本目录作为 CI 门禁（DESIGN.md §6）。
