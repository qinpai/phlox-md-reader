# Phlox-md-reader -- install only the files the skill needs (30 files, ~380 KB).
# Usage:
#   powershell -ExecutionPolicy Bypass -File install-skill.ps1
#   powershell -ExecutionPolicy Bypass -File install-skill.ps1 -Target D:\my\skills\phlox-md-reader
#   powershell -ExecutionPolicy Bypass -File install-skill.ps1 -Mirror gitee
param(
    [string]$Target = (Join-Path $HOME ".dsh\skills\phlox-md-reader"),
    [ValidateSet("github", "gitee")][string]$Mirror = "github"
)

$ErrorActionPreference = "Stop"
$bases = @{
    github = "https://raw.githubusercontent.com/qinpai/phlox-md-reader/master/"
    gitee  = "https://gitee.com/cric_qin/phlox-md-reader/raw/master/"
}
$base = $bases[$Mirror]
$fallback = if ($Mirror -eq "github") { $bases.gitee } else { $bases.github }

function Get-Text([string]$url) {
    for ($i = 1; $i -le 3; $i++) {
        try { return (Invoke-WebRequest $url -UseBasicParsing -TimeoutSec 30).Content } catch {
            if ($i -eq 3) { throw }
            Start-Sleep -Seconds 2
        }
    }
}

function Get-File([string]$rel) {
    $urls = @($base + $rel, $fallback + $rel)
    $dest = Join-Path $Target ($rel -replace "/", "\")
    $dir = Split-Path $dest -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    foreach ($u in $urls) {
        for ($i = 1; $i -le 3; $i++) {
            try {
                Invoke-WebRequest $u -UseBasicParsing -OutFile $dest -TimeoutSec 60
                return $true
            } catch {
                if ($i -eq 3 -and $u -eq $urls[-1]) { Write-Host ("  FAIL " + $rel) -ForegroundColor Red; return $false }
                Start-Sleep -Seconds 2
            }
        }
    }
}

Write-Host "Phlox-md-reader skill installer"
Write-Host ("  source : " + $base)
Write-Host ("  target : " + $Target)
Write-Host ""

$manifest = ((Get-Text ($base + "skill-files.txt")) -split "`n") | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }
$total = $manifest.Count
$n = 0
$failed = @()
foreach ($rel in $manifest) {
    $n++
    Write-Host ("  [{0,2}/{1}] {2}" -f $n, $total, $rel)
    if (-not (Get-File $rel)) { $failed += $rel }
}

Write-Host ""
if ($failed.Count -eq 0) {
    Write-Host ("Done: " + $total + " files -> " + $Target) -ForegroundColor Green
    Write-Host ""
    Write-Host "Next steps:"
    Write-Host ("  cd `"" + $Target + "`"")
    Write-Host "  pip install -r requirements.txt"
    Write-Host "  python mdread.py doctor"
    Write-Host ""
    Write-Host "Optional chemistry (structures from SMILES):"
    Write-Host "  python install_indigo.py --install"
} else {
    Write-Host ("Failed: " + $failed.Count + " file(s)") -ForegroundColor Red
    $failed | ForEach-Object { Write-Host ("  " + $_) }
    Write-Host "Re-run to retry; already-downloaded files are skipped or overwritten harmlessly."
    exit 1
}