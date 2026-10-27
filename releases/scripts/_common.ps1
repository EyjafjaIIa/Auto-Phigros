# 公共函数：找 python / adb、等设备、打印横幅
$ErrorActionPreference = 'Stop'
$Script:RepoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)

# 让 python 的输出用 UTF-8，和 chcp 65001 对齐
$env:PYTHONIOENCODING = 'utf-8'

function Write-Banner([string]$Text) {
    Write-Host ""
    Write-Host ("=" * 64) -ForegroundColor Cyan
    Write-Host ("  " + $Text) -ForegroundColor Cyan
    Write-Host ("=" * 64) -ForegroundColor Cyan
    Write-Host ""
}

function Find-Python {
    foreach ($name in @('python', 'py')) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { return $cmd.Source }
    }
    $local = Join-Path $Script:RepoRoot ".venv\Scripts\python.exe"
    if (Test-Path $local) { return $local }
    throw "没找到 Python。请安装 https://www.python.org/downloads/ 并勾选 Add python.exe to PATH"
}

function Find-Adb {
    $cmd = Get-Command adb -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    $found = Get-ChildItem -Path $Script:RepoRoot -Recurse -Filter adb.exe -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    throw "没找到 adb。把 scrcpy 解压到仓库目录，或安装 platform-tools 并加入 PATH"
}

function Wait-Device([string]$Adb) {
    while ($true) {
        $out = & $Adb devices 2>&1
        $hit = $out | Where-Object { $_ -match "\sdevice$" } | Select-Object -First 1
        if ($hit) {
            $serial = ($hit -split "\s+")[0]
            Write-Host ("已连接设备：" + $serial) -ForegroundColor Green
            return
        }
        Write-Host "还没有检测到手机。" -ForegroundColor Yellow
        Write-Host "  1) USB 线连上电脑（要能传文件的那种线）"
        Write-Host "  2) 手机：设置 → 关于手机 → 连点版本号 7 次 → 开发者选项 → USB 调试"
        Write-Host "  3) 手机上弹出「允许 USB 调试」时点允许"
        $answer = Read-Host "弄好后按回车重试（输入 q 退出）"
        if ($null -ne $answer -and $answer.Trim() -eq 'q') { throw "用户取消" }
    }
}

function Clear-ConsoleInput {
    # 打歌时按的回车/方向键会留在控制台缓冲区里，读菜单前要清掉
    try {
        while ([Console]::KeyAvailable) {
            [void][Console]::ReadKey($true)
        }
    } catch {
        # 输入被重定向时 KeyAvailable 会抛异常，忽略
    }
}

function Read-Choice {
    param([string]$Prompt = "请输入 1 或 2")
    Clear-ConsoleInput
    Write-Host ($Prompt + "  > ") -ForegroundColor Cyan -NoNewline
    if (-not [Console]::IsInputRedirected) {
        try {
            $key = [Console]::ReadKey($true)
            Write-Host $key.KeyChar
            return ([string]$key.KeyChar)
        } catch {
            # 落到下面的 Read-Host
        }
    }
    $line = Read-Host
    if ($null -eq $line) { return $null }
    return $line.Trim()
}

function Read-Enter {
    param([string]$Prompt = "")
    Clear-ConsoleInput
    if ($Prompt) { Write-Host $Prompt -ForegroundColor Yellow }
    $line = Read-Host
    if ($null -eq $line) { return $null }
    return $line.Trim()
}
