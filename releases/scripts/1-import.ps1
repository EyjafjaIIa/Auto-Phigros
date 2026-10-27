param([switch]$DryRun)

. "$PSScriptRoot\_common.ps1"
Write-Banner "Phigros 谱面导入"

$python = Find-Python
$adb = Find-Adb
Write-Host ("python: " + $python)
Write-Host ("adb:    " + $adb)
$existing = @(Get-ChildItem (Join-Path $Script:RepoRoot 'charts') -Filter *.json -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -ne 'manifest.json' })
if ($existing.Count -gt 0) {
    Write-Host ("注意：charts\ 里已经有 " + $existing.Count + " 份谱面，这次会重新拉取并覆盖。") -ForegroundColor Yellow
}
Wait-Device $adb

if ($DryRun) {
    Write-Host "（DryRun：环境检查通过，不实际拉取/解包）" -ForegroundColor Yellow
    return
}

Push-Location $Script:RepoRoot
try {
    Write-Host ""
    $apk = Join-Path $Script:RepoRoot 'apk\base.apk'
    $rootApk = Get-ChildItem $Script:RepoRoot -Filter *.apk -File -ErrorAction SilentlyContinue |
        Select-Object -First 1
    $apkDir = 'apk'
    $doPull = $true

    if (Test-Path $apk) {
        $mb = [int]((Get-Item $apk).Length / 1MB)
        Write-Host ("已经拉取过 apk\base.apk（" + $mb + " MB）。") -ForegroundColor Yellow
        Clear-ConsoleInput
        $answer = Read-Host "直接回车 = 复用现有安装包；输入 p 再回车 = 重新从手机拉取"
        if ($null -ne $answer -and $answer.Trim() -eq 'p') { $doPull = $true } else { $doPull = $false }
    } elseif ($rootApk) {
        $mb = [int]($rootApk.Length / 1MB)
        Write-Host ("仓库根目录已有安装包 " + $rootApk.Name + "（" + $mb + " MB），直接用它解包。") -ForegroundColor Green
        $apkDir = '.'
        $doPull = $false
    }

    if ($doPull) {
        Write-Host "[1/3] 从手机拉取安装包（约 2.5 GB，几分钟到十几分钟）..." -ForegroundColor Cyan
        & $python tools\import_charts.py pull --adb $adb
        if ($LASTEXITCODE -ne 0) { throw "拉取安装包失败" }
    } else {
        Write-Host "[1/3] 复用现有安装包，跳过拉取" -ForegroundColor DarkGray
    }

    Write-Host ""
    Write-Host "[2/3] 解出 apk 里的 assets（也是几个 GB，第一次会慢）..." -ForegroundColor Cyan
    & $python tools\import_charts.py unpack --apk-dir $apkDir
    if ($LASTEXITCODE -ne 0) { throw "解包失败" }

    Write-Host ""
    Write-Host "[3/3] 解码谱面 ..." -ForegroundColor Cyan
    $stale = @(Get-ChildItem charts -Filter *.json -ErrorAction SilentlyContinue)
    if ($stale.Count -gt 0) {
        Write-Host ("  清理 charts\ 里旧的 " + $stale.Count + " 个文件（calibration.json 不受影响）") -ForegroundColor DarkGray
        $stale | Remove-Item -Force
    }
    $catalog = Get-ChildItem -Path "apk" -Recurse -Filter "catalog.json" -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $catalog) { throw "没找到 catalog.json，说明解包不完整" }
    $aaDir = Split-Path -Parent $catalog.FullName
    & $python run.py tracks extract $aaDir --out charts
    if ($LASTEXITCODE -ne 0) { throw "解码谱面失败" }

    $charts = @(Get-ChildItem charts -Filter *.json -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne 'manifest.json' })
    Write-Host ""
    Write-Host ("完成！charts\ 下现在有 " + $charts.Count + " 份谱面。") -ForegroundColor Green
    Write-Host "接下来可以双击「2-自动打歌.bat」。"
} finally {
    Pop-Location
}
