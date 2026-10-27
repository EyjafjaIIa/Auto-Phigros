param([switch]$DryRun, [switch]$TestPlay)

. "$PSScriptRoot\_common.ps1"
Write-Banner "Phigros 自动打歌"

$python = Find-Python
$adb = Find-Adb
Wait-Device $adb

Push-Location $Script:RepoRoot
try {
    $charts = @(Get-ChildItem (Join-Path $Script:RepoRoot 'charts') -Filter *.json -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne 'manifest.json' })
    if ($charts.Count -eq 0) {
        throw "charts\ 里还没有谱面，请先双击「1-导入谱面.bat」"
    }

    $outDir = "out"
    New-Item -ItemType Directory -Force -Path $outDir | Out-Null
    $shot = Join-Path $outDir "screen.png"
    $ocrJson = Join-Path $outDir "ocr.json"

    # 屏幕分辨率 -> 「开始」按钮坐标（右下角那个三角）
    $wm = (& $adb shell wm size) | Select-Object -Last 1
    $m = [regex]::Match($wm, '(\d+)\s*x\s*(\d+)')
    if (-not $m.Success) { throw "读不到屏幕分辨率" }
    $w = [int]$m.Groups[1].Value
    $h = [int]$m.Groups[2].Value
    if ($w -lt $h) { $tmp = $w; $w = $h; $h = $tmp }
    $tapX = [int]($w * 0.95)
    $tapY = [int]($h * 0.87)
    $tap = "$tapX,$tapY"

    $round = 0
    while ($true) {
        $round++

        Write-Host ""
        Write-Host ("[" + $round + "] 截屏识别 ...") -ForegroundColor Cyan
        cmd /c "`"$adb`" exec-out screencap -p > `"$shot`""
        if (-not (Test-Path $shot) -or (Get-Item $shot).Length -lt 10000) {
            throw "截屏失败，确认手机已解锁、游戏停在前台"
        }

        & powershell -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\ocr.ps1" `
            -ImagePath $shot -OutPath $ocrJson | Out-Null

        $raw = & $python run.py identify $shot --ocr $ocrJson
        $json = ($raw | Where-Object { $_ -match '^\s*\{' } | Select-Object -Last 1)
        if ($LASTEXITCODE -ne 0 -or -not $json) {
            if ($round -eq 1) {
                throw ("识别失败（把手机停在选歌界面再试）：" + ($raw -join " / "))
            }
            Write-Host ""
            Write-Host "没识别到选歌界面（游戏可能还停在结算页）。" -ForegroundColor Yellow
            $retry = Read-Enter "请点回选歌界面、选中曲目，然后按回车重试（输入 q 退出）。"
            if ($retry -eq 'q') { return }
            continue
        }
        $info = $json | ConvertFrom-Json

        Write-Host ""
        Write-Host ("  曲目 : " + $info.song) -ForegroundColor Green
        Write-Host ("  曲师 : " + $info.artist)
        Write-Host ("  难度 : " + $info.difficulty) -ForegroundColor Green
        Write-Host ("  谱面 : " + $info.chart)
        Write-Host ("  匹配 : " + $info.score)

        if ($info.difficulty -eq '?') {
            Write-Host ""
            Write-Host "没认出难度，请手动输入 EZ / HD / IN / AT：" -ForegroundColor Yellow
            $answerRaw = Read-Enter ""
            $answer = if ($null -eq $answerRaw) { "" } else { $answerRaw.ToUpper() }
            $chart = Join-Path "charts" ($info.song + "." + $info.artist + "." + $answer + ".json")
            if (-not (Test-Path $chart)) { throw ("没有这份谱面：" + $chart) }
            $info.chart = $chart
        }

        Write-Host ""
        Write-Host ("屏幕 " + $w + "x" + $h + "，「开始」按钮按 " + $tap) -ForegroundColor DarkGray

        if ($DryRun) {
            Write-Host ""
            Write-Host "（DryRun：只识别，不启动打歌）" -ForegroundColor Yellow
            return
        }

        Write-Host ""
        Write-Host "5 秒后开始 —— 进歌后，在第一个音符压到判定线时按回车或空格。" -ForegroundColor Yellow
        Write-Host "过程中可以用 ↑/↓（20ms）、PageUp/PageDown（200ms）微调。" -ForegroundColor Yellow
        Write-Host ""
        if ($TestPlay) {
            Write-Host "（TestPlay：跳过实际打歌，只验证结束菜单）" -ForegroundColor Yellow
            Start-Sleep -Seconds 1
        } else {
            & $python run.py play $info.chart --backend scrcpy --start-tap $tap --countdown 5
        }

        Write-Host ""
        Write-Host "────────────────────────────────────────────" -ForegroundColor Cyan
        Write-Host "  本轮结束，偏移已写入 calibration.json" -ForegroundColor DarkGray
        Write-Host "  1 = 记住这次偏移，再打一遍" -ForegroundColor Green
        Write-Host "  2 = 退出" -ForegroundColor Gray
        Write-Host "  （直接按键即可，不用按回车）" -ForegroundColor DarkGray
        Write-Host "────────────────────────────────────────────" -ForegroundColor Cyan

        $choice = Read-Choice "请输入 1 或 2"
        if ($null -eq $choice) { $choice = "" }
        if ($choice -ne '1') {
            Write-Host "再见。" -ForegroundColor Green
            return
        }

        Write-Host ""
        $again = Read-Enter "请回到选歌界面、选中同一首（或想打的下一首），然后按回车继续（输入 q 回车退出）。"
        if ($again -eq 'q') { return }
    }
} finally {
    Pop-Location
}
