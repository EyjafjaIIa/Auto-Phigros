# 用 Windows 自带的离线 OCR 识别一张截图，输出 JSON 行：
#   [{ "text": "...", "x": 0, "y": 0, "w": 0, "h": 0 }, ...]
param(
    [Parameter(Mandatory = $true)][string]$ImagePath,
    [Parameter(Mandatory = $true)][string]$OutPath
)

$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskGeneric = ([System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object {
    $_.Name -eq 'AsTask' -and $_.GetParameters().Count -eq 1 -and
    $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
})[0]

function Await($WinRtTask, $ResultType) {
    $asTask = $asTaskGeneric.MakeGenericMethod($ResultType)
    $netTask = $asTask.Invoke($null, @($WinRtTask))
    $netTask.Wait(-1) | Out-Null
    $netTask.Result
}

[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics, ContentType = WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime] | Out-Null

$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    throw "系统没有 OCR 语言包（设置 → 时间和语言 → 语言 → 添加英语/中文）"
}

$fs = [System.IO.File]::OpenRead((Resolve-Path -LiteralPath $ImagePath).Path)
try {
    $ras = [System.IO.WindowsRuntimeStreamExtensions]::AsRandomAccessStream($fs)
    $decoder = Await ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($ras)) ([Windows.Graphics.Imaging.BitmapDecoder])
    $bitmap = Await ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
    $result = Await ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
} finally {
    $fs.Dispose()
}

$lines = @()
foreach ($line in $result.Lines) {
    $minX = [double]::MaxValue
    $minY = [double]::MaxValue
    $maxX = 0.0
    $maxY = 0.0
    foreach ($word in $line.Words) {
        $r = $word.BoundingRect
        if ($r.X -lt $minX) { $minX = $r.X }
        if ($r.Y -lt $minY) { $minY = $r.Y }
        if (($r.X + $r.Width) -gt $maxX) { $maxX = $r.X + $r.Width }
        if (($r.Y + $r.Height) -gt $maxY) { $maxY = $r.Y + $r.Height }
    }
    $lines += [pscustomobject]@{
        text = $line.Text
        x    = [int]$minX
        y    = [int]$minY
        w    = [int]($maxX - $minX)
        h    = [int]($maxY - $minY)
    }
}

if ($lines.Count -eq 0) {
    $json = "[]"
} elseif ($lines.Count -eq 1) {
    $json = ConvertTo-Json -InputObject $lines -Depth 3 -Compress
} else {
    $json = $lines | ConvertTo-Json -Depth 3 -Compress
}
[System.IO.File]::WriteAllText($OutPath, $json, (New-Object System.Text.UTF8Encoding($false)))
Write-Output ("OCR lines: " + $lines.Count)
