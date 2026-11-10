# Phlox-md-reader -- install ONLY the files the skill needs (30 files, ~380 KB).
#
# Two ways to run it:
#   1) from an unpacked copy of the repo (no network needed):
#        powershell -ExecutionPolicy Bypass -File install-skill.ps1
#   2) from anywhere (it downloads the repo zip first, ~1.7 MB):
#        powershell -ExecutionPolicy Bypass -File install-skill.ps1 -FromNetwork
#
# Options:
#   -Target <dir>   where to install (default: $HOME\.dsh\skills\phlox-md-reader)
#   -Mirror gitee   use the Gitee mirror for the zip download (default: github)
#   -Lite           skip the bundled KaTeX (saves ~550 KB; formulas show as raw $...$)
param(
    [string]$Target = (Join-Path $HOME ".dsh\skills\phlox-md-reader"),
    [ValidateSet("github", "gitee")][string]$Mirror = "github",
    [switch]$FromNetwork,
    [switch]$Force,
    [switch]$Lite
)

$ErrorActionPreference = "Stop"
$skillFiles = @("SKILL.md", "mdread.py", "README.md", "LICENSE", "requirements.txt", "install_indigo.py")

function Test-ZipGood([string]$path) {
    # a truncated download looks like a zip but has no central directory / too few entries
    try {
        Add-Type -AssemblyName System.IO.Compression.FileSystem -ErrorAction SilentlyContinue
        $z = [IO.Compression.ZipFile]::OpenRead($path)
        $n = $z.Entries.Count
        $z.Dispose()
        return ($n -ge 40)
    } catch { return $false }
}

function Get-Zip([string]$name) {
    $urls = if ($Mirror -eq "gitee") {
        @("https://gitee.com/cric_qin/phlox-md-reader/repository/archive/master.zip")
    } else {
        @("https://codeload.github.com/qinpai/phlox-md-reader/zip/refs/heads/master",
          "https://gitee.com/cric_qin/phlox-md-reader/repository/archive/master.zip")
    }
    $dest = Join-Path ([IO.Path]::GetTempPath()) $name
    foreach ($u in $urls) {
        for ($i = 1; $i -le 3; $i++) {
            try {
                Write-Host ("  downloading (try " + $i + ") " + $u)
                Remove-Item $dest -Force -ErrorAction SilentlyContinue
                # curl.exe resumes/retries better than Invoke-WebRequest on flaky links
                $code = & curl.exe -sSL --retry 5 --retry-all-errors --connect-timeout 20 --max-time 600 -o $dest $u -w "%{http_code}" 2>$null
                $size = if (Test-Path $dest) { (Get-Item $dest).Length } else { 0 }
                if ($code -eq "200" -and (Test-ZipGood $dest)) {
                    Write-Host ("  ok: " + [math]::Round($size / 1KB) + " KB")
                    return $dest
                }
                Write-Host ("  incomplete download (" + [math]::Round($size / 1KB) + " KB, http=" + $code + "), retrying")
            } catch {
                Write-Host ("  failed: " + $_.Exception.Message) -ForegroundColor Yellow
            }
            Start-Sleep -Seconds 3
        }
    }
    throw "cannot download a complete repo zip (network keeps cutting off). Try -Mirror gitee, or download the zip in a browser and run this script from the unpacked folder."
}

# --- figure out the source tree ------------------------------------------------
$srcRoot = $null
if (-not $FromNetwork) {
    $here = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
    if (Test-Path (Join-Path $here "skill-files.txt")) {
        $srcRoot = $here
    } else {
        Write-Host "skill-files.txt not found next to this script; switching to network mode." -ForegroundColor Yellow
        $FromNetwork = $true
    }
}

$tmpZip = $null
if ($FromNetwork) {
    $tmpZip = Get-Zip "phlox-md-reader-master.zip"
    $unz = Join-Path ([IO.Path]::GetTempPath()) ("phloxunz_" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    Expand-Archive $tmpZip -DestinationPath $unz -Force
    $srcRoot = (Get-ChildItem $unz -Directory | Select-Object -First 1).FullName
}

$manifestName = if ($Lite) { "skill-files-lite.txt" } else { "skill-files.txt" }
$manifest = (Get-Content (Join-Path $srcRoot $manifestName) -Encoding UTF8) |
    ForEach-Object { $_.Trim() } | Where-Object { $_ -ne "" }

Write-Host ""
Write-Host "Phlox-md-reader skill installer"
Write-Host ("  source : " + $srcRoot)
Write-Host ("  target : " + $Target)
Write-Host ("  flavor : " + $(if ($Lite) { "lite (no KaTeX; formulas stay raw)" } else { "full (offline KaTeX included)" }))
Write-Host ("  files  : " + $manifest.Count)
Write-Host ""

if ((Test-Path $Target) -and -not $Force) {
    $existing = Get-ChildItem $Target -Force -ErrorAction SilentlyContinue
    if ($existing) {
        Write-Host ("target already exists and is not empty: " + $Target) -ForegroundColor Yellow
        Write-Host "re-run with -Force to overwrite, or pick another -Target" -ForegroundColor Yellow
        exit 1
    }
}

$missing = @()
foreach ($rel in $manifest) {
    $from = Join-Path $srcRoot ($rel -replace "/", "\")
    $to = Join-Path $Target ($rel -replace "/", "\")
    if (-not (Test-Path $from)) { $missing += $rel; continue }
    $dir = Split-Path $to -Parent
    if (-not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    Copy-Item $from $to -Force
}
Write-Host ("copied " + ($manifest.Count - $missing.Count) + " / " + $manifest.Count + " files")

if ($missing.Count -gt 0) {
    Write-Host "missing in source:" -ForegroundColor Red
    $missing | ForEach-Object { Write-Host ("  " + $_) }
}

# clean up temp
if ($tmpZip -and (Test-Path $tmpZip)) { Remove-Item $tmpZip -Force -ErrorAction SilentlyContinue }
if ($FromNetwork -and $unz) { Remove-Item $unz -Recurse -Force -ErrorAction SilentlyContinue }

Write-Host ""
Write-Host ("Done -> " + $Target) -ForegroundColor Green
Write-Host ""
Write-Host "Next steps:"
Write-Host ("  cd `"" + $Target + "`"")
Write-Host "  pip install -r requirements.txt"
Write-Host "  python mdread.py doctor"
Write-Host ""
Write-Host "Optional chemistry (structures from SMILES):"
Write-Host "  python install_indigo.py --install"