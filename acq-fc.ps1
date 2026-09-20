<#
  fc 版（firecrawl）采集启动器

  作用：把运行数据隔离到本仓库自己的 .agent-reach 目录，
        避免与 pw 版共用源清单 / 数据目录（此前 fc 版因共用清单一加载就报错）。

  用法：
    .\acq-fc.ps1 run                  # 跑一轮采集（按各源 interval 节流）
    .\acq-fc.ps1 run --source <id>    # 只跑指定源
    .\acq-fc.ps1 run --force          # 忽略节流强制跑
    .\acq-fc.ps1 --help               # 查看 CLI 帮助
#>

$OutputEncoding = [System.Text.Encoding]::UTF8
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$AcqHome = Join-Path $Root ".agent-reach"
$Work = Join-Path $Root "Agent-Reach-main"
$Py = Join-Path $Work ".venv\Scripts\python.exe"

if (-not (Test-Path $Py)) {
    Write-Host "[错误] 找不到 python: $Py"
    exit 1
}

$env:AGENT_REACH_HOME = $AcqHome
Write-Host "[home] $AcqHome"

function Test-Port([string]$HostName, [int]$Port, [int]$TimeoutMs = 400) {
    try {
        $client = New-Object System.Net.Sockets.TcpClient
        $task = $client.ConnectAsync($HostName, $Port)
        $ok = $task.Wait($TimeoutMs) -and $client.Connected
        $client.Close()
        return $ok
    } catch {
        return $false
    }
}

if (Test-Port "127.0.0.1" 7890) {
    $uri = "http://127.0.0.1:7890"
    $env:HTTP_PROXY = $uri
    $env:HTTPS_PROXY = $uri
    $env:http_proxy = $uri
    $env:https_proxy = $uri
    $env:NO_PROXY = "127.0.0.1,localhost"
    $env:no_proxy = "127.0.0.1,localhost"
    Write-Host "[代理] 走 $uri"
} else {
    Remove-Item Env:HTTP_PROXY, Env:HTTPS_PROXY, Env:http_proxy, Env:https_proxy -ErrorAction SilentlyContinue
    Write-Host "[代理] 7890 不可达 -> 直连（海外源会失败，国内源正常）"
}

Push-Location $Work
try {
    & $Py -m acquisition.cli @args
} finally {
    Pop-Location
}

exit $LASTEXITCODE
