# Usage: .\run.ps1 scrcpy-test
# Switches to the repo folder and picks a Python that has the optional deps.
# If execution policy blocks it:
#   powershell -ExecutionPolicy Bypass -File .\run.ps1 scrcpy-test
# (Kept ASCII-only on purpose: Windows PowerShell 5.1 reads BOM-less .ps1 as ANSI.)
param([Parameter(ValueFromRemainingArguments = $true)][string[]]$Rest)

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

# Windows PowerShell 5.1 默认控制台是 936/GBK，Python 输出 UTF-8 会变乱码
$env:PYTHONIOENCODING = 'utf-8'
try { $null = & chcp.com 65001 } catch { }

$candidates = @()
if ($env:PHIGROS_PYTHON) { $candidates += $env:PHIGROS_PYTHON }
$candidates += "C:\Users\GJS\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\python.exe"
$candidates += (Get-Command python -ErrorAction SilentlyContinue | Select-Object -First 1).Source
$candidates += (Get-Command py -ErrorAction SilentlyContinue | Select-Object -First 1).Source

$full = $null
$fallback = $null
foreach ($exe in $candidates) {
    if (-not $exe) { continue }
    if (-not (Test-Path $exe)) { continue }
    & $exe -c "import sys" *> $null
    if ($LASTEXITCODE -ne 0) { continue }
    & $exe -c "import PIL, numpy" *> $null
    if ($LASTEXITCODE -eq 0) { $full = $exe; break }
    if (-not $fallback) { $fallback = $exe }
}

if ($full) {
    $python = $full
} elseif ($fallback) {
    $python = $fallback
    Write-Host ('[run] using ' + $python + ' (no Pillow/numpy: preview and sync unavailable)') -ForegroundColor Yellow
    Write-Host ('[run] install them: "' + $python + '" -m pip install -r requirements.txt') -ForegroundColor Yellow
} else {
    Write-Host "No usable Python found. Install Python 3.9+ and add it to PATH." -ForegroundColor Red
    exit 2
}

& $python "$root\run.py" @Rest
exit $LASTEXITCODE
