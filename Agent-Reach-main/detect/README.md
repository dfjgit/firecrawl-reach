# detect/ — 反检测回归

## 自动自检

```bash
python detect/self_check.py
```

用 stealth launch + new_context 打开本地页面，采集本包覆盖的全部指纹面并断言与 profile 一致
（`navigator.webdriver`、platform、UA、plugins、languages、WebGL renderer、userAgentData、时区等）。
全部通过打印 PASS 并 exit 0，任一失败列出失败项 exit 1。

环境变量：

- `STEALTH_CHANNEL`：`chrome`（默认）/ `msedge` / `chromium`（扫描本地 ms-playwright 缓存的已下载 Chromium，不触发下载）。
  未显式设置时按 chrome → msedge → chromium 顺序回退；显式设置后失败即报错，不回退。
- `STEALTH_HEADED=1`：有头运行（默认无头）。
- `STEALTH_PROFILE_JSON`：用采集的 profile JSON（文件或目录）替代内置示例档案跑自检。

脚本会把 `../src` 插入 `sys.path`，无需先安装包。

## 其他工具

- `python detect/pool_smoke.py` — SessionPool 冒烟：同 profile 复用、超容量回收、崩溃重建。
- `python detect/collect_profile.py` — 在真实浏览器（默认 chrome，回退 msedge）采集指纹，
  产出 `detect/collected/*.json`，可被 `ProfilePool.load_json_dir()` 加载。
  有头采集更真实：`STEALTH_HEADED=1 python detect/collect_profile.py`。
- `python detect/fingerprint_check.py` — TLS/JA3/HTTP2 指纹与线上请求头回显对比
  （默认 https://tls.peet.ws/api/all，`STEALTH_FP_URL` 覆盖）；网络不可达 exit 2。
  加 `--via-proxy`（或 `STEALTH_VIA_PROXY=1`）经 Go uTLS sidecar 复测，验证 JA3 改写。
- `python detect/egress_stub.py http|socks5 [port]` — 本地出口代理桩（非 stealth 组件），
  配合 sidecar 的 `-upstream` 验证代理链：日志里的 `CONNECT host:port` 行即流量经过出口的证据。
  实测存档（2026-07-30）：http 与 socks5 两种链路上 tls.peet.ws 均返回 ja4
  `t13d1516h2_8daaf6152771_d8a2da3f94cd`（Chrome），stub 日志确认 `CONNECT tls.peet.ws:443`。

## TLS/JA3 基线存档（2026-07-30，Edge 150.0.4078.99，tls.peet.ws/api/all）

直连（Edge 真实 TLS 栈）：

```
ja3_hash : 6433b99245286d3589c76afea801411b   # 注：Chrome 系扩展排列每会话随机，ja3 串/hash 逐次变化
ja3      : 771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,0-27-23-10-45-18-17613-65037-5-51-13-43-16-35-11-65281,4588-29-23-24,0
ja4      : t13d1516h2_8daaf6152771_806a8c22fdea   # 稳定，可作基线锚点
peetprint: 67c3e9111bed9e7f03d2f21d6d88994b
http_version: h2
http/2   : 1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p
```

经 sidecar（uTLS HelloChrome_Auto）：

```
ja3_hash : 959d26be0762eb094a88c55009f1cfae   # 同样逐次变化（uTLS 模拟 Chrome 的扩展随机排列）
ja3      : 771,4865-4866-4867-49195-49199-49196-49200-52393-52392-49171-49172-156-157-47-53,13-35-17613-5-11-16-23-10-51-18-65037-0-43-65281-27-45,4588-29-23-24,0
ja4      : t13d1516h2_8daaf6152771_d8a2da3f94cd   # = 标准 Chrome 桌面 ja4，改写生效的判据
peetprint: 1d4ffe9b0e34acac0bd883fa7f79d7b5
http_version: h2   # h2 经代理正常，akamai 指纹与直连一致（浏览器侧 H2 栈未变）
http/2   : 1:65536;2:0;4:6291456;6:262144|15663105|0|m,a,s,p
```

判读：两组的 ja4 后缀不同（`806a8c22fdea` → `d8a2da3f94cd`）即证明 ClientHello 已从
Edge 真实指纹换成 uTLS Chrome 指纹；两组线上请求头（UA/sec-ch-ua/accept-language）
均与对齐后 profile MATCH。

## 公开检测站（人工 / 半自动回归）

自动自检只覆盖"我们知道自己改了什么"；第三方检测器的未知探针需要定期人工对拍：

- https://bot.sannysoft.com — 经典综合检测表
- https://abrahamjuliot.github.io/creepjs/ — 指纹一致性与"谎言"检测
- https://fingerprint.com/demo — 商业检测 demo
- https://pixelscan.net — UA/字体/硬件一致性

建议：每次升级 Playwright 或改动 profile 后，用 `stealth.launch` + `stealth.new_context`
打开上述站点，截图得分存档对比。Playwright 版本升级时以本目录作为 CI 门禁（DESIGN.md §6）。
