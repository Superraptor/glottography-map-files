<#
.SYNOPSIS
    Convenience launcher for the Glottography to Commons pipeline.
    Automatically discovers an available Python runtime on the system.
#>

param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ScriptArgs
)

$pythonExe = $null

if (Get-Command python -ErrorAction SilentlyContinue) {
    try {
        $test = & python --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = "python"
        }
    } catch {}
}

if (-not $pythonExe -and (Get-Command py -ErrorAction SilentlyContinue)) {
    try {
        $test = & py -3 --version 2>&1
        if ($LASTEXITCODE -eq 0) {
            $pythonExe = "py"
        }
    } catch {}
}

if (-not $pythonExe) {
    $candidates = @(
        "C:\Program Files\Inkscape\bin\python.exe",
        "C:\Program Files\Python311\python.exe",
        "C:\Program Files\Python312\python.exe",
        "C:\Program Files\Python310\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
        "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
    )
    foreach ($cand in $candidates) {
        if (Test-Path $cand) {
            $pythonExe = $cand
            break
        }
    }
}

if (-not $pythonExe) {
    Write-Error "Python 3 is required to run this pipeline. Please install Python 3 or ensure it is in your PATH."
    exit 1
}

$mainScript = Join-Path $PSScriptRoot "main.py"
& $pythonExe $mainScript @ScriptArgs
exit $LASTEXITCODE
