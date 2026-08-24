param(
    [string]$DistDirectory = (Join-Path (Split-Path -Parent $PSScriptRoot) 'dist\jharni'),
    [switch]$SkipRuntimeSmokeTest
)

$ErrorActionPreference = 'Stop'
$dist = (Resolve-Path -LiteralPath $DistDirectory -ErrorAction Stop).Path
$required = @(
    'Compitcom-eStamp-Automation.exe',
    '_internal\assets\paddleocr\PP-OCRv6_medium_rec\inference.json',
    '_internal\assets\paddleocr\PP-OCRv6_medium_rec\inference.pdiparams',
    '_internal\assets\paddleocr\PP-OCRv6_medium_rec\inference.yml',
    '_internal\paddle\base\libpaddle.pyd',
    '_internal\torch\lib\torch_cpu.dll',
    '_internal\cv2\cv2.pyd',
    '_internal\playwright\driver\node.exe',
    '_internal\playwright\driver\package\cli.js'
)

$missing = @($required | Where-Object { -not (Test-Path -LiteralPath (Join-Path $dist $_) -PathType Leaf) })
if ($missing.Count -gt 0) {
    throw "Frozen build is missing required files: $($missing -join ', ')"
}

if (-not $SkipRuntimeSmokeTest) {
    $report = Join-Path ([IO.Path]::GetTempPath()) "compitcom-smoke-$PID.txt"
    $previousReport = $env:COMPITCOM_SMOKE_TEST_REPORT
    try {
        $env:COMPITCOM_SMOKE_TEST_REPORT = $report
        $process = Start-Process `
            -FilePath (Join-Path $dist 'Compitcom-eStamp-Automation.exe') `
            -ArgumentList '--packaging-smoke-test' `
            -Wait `
            -PassThru
        $exitCode = $process.ExitCode
        if (-not (Test-Path -LiteralPath $report -PathType Leaf)) {
            throw "Frozen runtime smoke test did not create its report."
        }
        $details = Get-Content -Raw -LiteralPath $report
        if ($exitCode -ne 0) {
            throw "Frozen runtime smoke test failed (exit code $exitCode). $details"
        }
        Write-Host ($details.Trim())
    }
    finally {
        if ($null -eq $previousReport) {
            Remove-Item Env:COMPITCOM_SMOKE_TEST_REPORT -ErrorAction SilentlyContinue
        }
        else {
            $env:COMPITCOM_SMOKE_TEST_REPORT = $previousReport
        }
        Remove-Item -LiteralPath $report -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "Validated Windows package: $dist"
