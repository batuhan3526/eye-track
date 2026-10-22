# packaging/windows/derle.ps1
# Builds the Windows packages of Goz Faresi into dist\surum-<version>\:
#   GozFaresi-<v>-Windows-Kurulum.exe     Inno Setup, per user, no admin
#   GozFaresi-<v>-Windows-Tasinabilir.zip portable (the "tasinabilir" marker)
#   SHA256SUMS                            every file in the folder
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\derle.ps1
#       [-VenvKoru] [-InnoKur] [-Python <python.exe>]
#
#   -VenvKoru  reuse packaging\out\venv-win when it exists (default: a clean
#              venv every time, so nothing from an earlier build leaks in)
#   -InnoKur   if Inno Setup is missing, install it with winget (user scope).
#              Without it a missing Inno Setup means: zip only, said loudly.
#   -Python    the Python 3.11 that creates the venvs (default: the
#              GOZFARESI_PYTHON variable, the per-user 3.11, then
#              python on PATH)
#
# Windows PowerShell 5.1 compatible and ASCII only (5.1 reads a BOM-less
# script in the ANSI code page). Never installs into the system Python: the
# source install on the development machine runs on it. All build venvs and
# intermediate files live in packaging\out\ (git-ignored).
[CmdletBinding()]
param(
    [switch]$VenvKoru,
    [switch]$InnoKur,
    [string]$Python = ''
)

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2

# No build step needs a GPU. An empty value would DELETE the variable in
# PowerShell (= every GPU visible); -1 is an index no device has.
$env:CUDA_VISIBLE_DEVICES = '-1'
$env:PYTHONUTF8 = '1'
$env:PIP_DISABLE_PIP_VERSION_CHECK = '1'

function Say([string]$m) { Write-Host "[derle] $m" }

function Run([string]$exe, [string[]]$argv) {
    & $exe @argv
    if ($LASTEXITCODE -ne 0) {
        throw "komut basarisiz ($LASTEXITCODE): $exe $($argv -join ' ')"
    }
}

$Repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Pack = Join-Path $Repo 'packaging'
$Out = Join-Path $Pack 'out'
$Gaze = Join-Path $Repo 'gaze'
New-Item -ItemType Directory -Force $Out | Out-Null

$init = Get-Content -Raw -Encoding UTF8 (Join-Path $Gaze 'eye_gaze\__init__.py')
if ($init -notmatch '(?m)^__version__\s*=\s*"([0-9.]+)"') {
    throw 'gaze\eye_gaze\__init__.py icinde __version__ yok'
}
$Ver = $Matches[1]
$Dist = Join-Path $Repo "dist\surum-$Ver"
New-Item -ItemType Directory -Force $Dist | Out-Null
Say "surum $Ver -> $Dist"

# --- the base interpreter (only ever used to create venvs) -------------------
if (-not $Python) { $Python = $env:GOZFARESI_PYTHON }
if (-not $Python) {
    $local = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    if (Test-Path $local) { $Python = $local }
}
if (-not $Python) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if ($cmd) { $Python = $cmd.Source }
}
if (-not $Python) { throw 'Python 3.11 bulunamadi (-Python ile verin)' }
$pv = & $Python -c "import sys; print('%d.%d' % sys.version_info[:2])"
if ($pv -ne '3.11') { throw "Python 3.11 gerekli, bulunan: $pv ($Python)" }

# --- 1-2. the build venv -----------------------------------------------------
$Venv = Join-Path $Out 'venv-win'
$VPy = Join-Path $Venv 'Scripts\python.exe'
if ($VenvKoru -and (Test-Path $VPy)) {
    Say "mevcut venv kullaniliyor: $Venv"
} else {
    if (Test-Path $Venv) { Remove-Item -Recurse -Force $Venv }
    Say "temiz venv: $Venv"
    Run $Python @('-m', 'venv', $Venv)
    Run $VPy @('-m', 'pip', 'install', '--upgrade', 'pip')
    Run $VPy @('-m', 'pip', 'install', '-r', (Join-Path $PSScriptRoot 'requirements-win.txt'))
}
# The plain CPU onnxruntime only (Task 18, R39): DirectML.dll is Microsoft's
# own redistributable and must not ship next to the GPL-3.0 piper/espeak-ng.
# A reused venv from an older build may still hold onnxruntime-directml:
# refuse it instead of freezing it. Listing providers opens no device.
Run $VPy @('-c', "import os, onnxruntime as o; p = o.get_available_providers(); d = os.path.join(os.path.dirname(o.__file__), 'capi', 'DirectML.dll'); assert 'DmlExecutionProvider' not in p and 'CPUExecutionProvider' in p, p; assert not os.path.exists(d), d; print('onnxruntime', o.__version__, p)")
Run $VPy @('-c', "import importlib.metadata as m; n = [d.metadata['Name'] for d in m.distributions() if d.metadata['Name'].lower().startswith('onnxruntime-')]; assert not n, ('onnxruntime-directml/-gpu in the venv: build a clean one (no -VenvKoru)', n)")
Run $VPy @('-c', "import cv2, mediapipe, numpy, yaml, PIL, tkinter, faster_whisper, ctranslate2, piper, sounddevice, keyboard, comtypes, winsdk, certifi, PyInstaller; assert cv2.__version__.startswith('4.13.'), cv2.__version__; print('ithalat tamam, opencv', cv2.__version__)")
# comtypes writes its COM wrappers (comtypes.gen) on first use; a frozen
# build cannot. Generate UI Automation (target snapping) and SAPI (the Tolga
# fallback voice) into the venv now, so the spec freezes them.
Run $VPy @('-c', "import os, comtypes.client as c; c.GetModule('UIAutomationCore.dll'); c.GetModule(os.path.expandvars(r'%SystemRoot%\System32\Speech\Common\sapi.dll')); from comtypes.gen import UIAutomationClient, SpeechLib; print('comtypes.gen tamam')")

# --- 3. the deep gaze model (ONNX) ------------------------------------------
$Hazirla = Join-Path $Pack 'hazirla.py'
& $VPy $Hazirla 'onnx-denetle'
if ($LASTEXITCODE -eq 3) {
    Say 'derin model yok ya da bozuk: CPU torch ile uretiliyor'
    $TVenv = Join-Path $Out 'venv-onnx-cpu'
    $TPy = Join-Path $TVenv 'Scripts\python.exe'
    if (-not (Test-Path $TPy)) {
        Run $Python @('-m', 'venv', $TVenv)
        Run $TPy @('-m', 'pip', 'install', 'torch==2.5.1', '--index-url', 'https://download.pytorch.org/whl/cpu')
        Run $TPy @('-m', 'pip', 'install', '-r', (Join-Path $Pack 'requirements-onnx.txt'))
    }
    # CUDA_VISIBLE_DEVICES is -1 here; the converter also hides the GPU itself.
    Run $TPy @((Join-Path $Gaze 'tools\derin_onnx_cevir.py'))
    Run $VPy @($Hazirla, 'onnx-denetle')
} elseif ($LASTEXITCODE -ne 0) {
    throw "hazirla.py onnx-denetle basarisiz ($LASTEXITCODE)"
}

# --- staged data (MediaPipe model, piper voice + espeak-ng-data) -------------
$Stage = Join-Path $Out 'stage'
$env:GOZFARESI_STAGE = $Stage
Run $VPy @($Hazirla, 'sahne', '--cikti', $Stage)

# --- 4. PyInstaller ----------------------------------------------------------
$PyiDist = Join-Path $Out 'pyi-win\dist'
$PyiWork = Join-Path $Out 'pyi-win\build'
Run $VPy @('-m', 'PyInstaller', '--noconfirm', '--clean', '--log-level', 'WARN',
           '--distpath', $PyiDist, '--workpath', $PyiWork,
           (Join-Path $Pack 'gozfaresi.spec'))
$Onedir = Join-Path $PyiDist 'GozFaresi'
$Exe = Join-Path $Onedir 'GozFaresi.exe'
if (-not (Test-Path $Exe)) { throw "PyInstaller cikti vermedi: $Exe" }

# The process must stay DPI-unaware (calibrations are in logical pixels):
# refuse an exe whose embedded manifest asks for DPI awareness.
$bytes = [System.IO.File]::ReadAllBytes($Exe)
$text = [System.Text.Encoding]::ASCII.GetString($bytes)
if ($text -match '<dpiAware|<dpiAwareness') {
    throw 'GozFaresi.exe manifesti DPI-aware: kalibrasyonlari bozar, derleme durduruldu'
}
Say 'manifest DPI-unaware (dogru)'

# Documents next to the exe (both the installer and the zip carry them),
# and the full licence texts of everything bundled (lisanslar\).
Copy-Item (Join-Path $Pack 'BENI-OKU-KURULUM.txt') $Onedir -Force
Copy-Item (Join-Path $Pack 'UCUNCU-TARAF-LISANSLAR.txt') $Onedir -Force
Copy-Item (Join-Path $Gaze 'LICENSE') (Join-Path $Onedir 'LICENSE.txt') -Force
Run $VPy @($Hazirla, 'lisanslar', $Onedir)

# The bundle guard (hazirla.py denetle): nothing of THIS machine may ship -
# calibration, samples, logs, caches, hardware profile, or a config.yaml
# (which would also switch the installed copy into portable mode) - and
# every file a package needs, lisanslar\ included, must be there.
Run $VPy @($Hazirla, 'denetle', $Onedir)

# Corresponding Source of the GPL-3.0 parts (piper-tts, espeak-ng), published
# next to the packages.
Run $VPy @($Hazirla, 'gpl-kaynak', $Dist)

# --- 5. Inno Setup -----------------------------------------------------------
function Find-Iscc {
    $c = @((Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'))
    if (${env:ProgramFiles(x86)}) { $c += (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe') }
    if ($env:ProgramFiles) { $c += (Join-Path $env:ProgramFiles 'Inno Setup 6\ISCC.exe') }
    foreach ($p in $c) { if (Test-Path $p) { return $p } }
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    return $null
}
$Iscc = Find-Iscc
if (-not $Iscc -and $InnoKur) {
    Say 'Inno Setup yok: winget ile kuruluyor (kullanici izniyle, -InnoKur)'
    Run 'winget' @('install', '--id', 'JRSoftware.InnoSetup', '-e', '--scope', 'user',
                   '--accept-source-agreements', '--accept-package-agreements',
                   '--disable-interactivity')
    $Iscc = Find-Iscc
}
$Setup = Join-Path $Dist "GozFaresi-$Ver-Windows-Kurulum.exe"
if ($Iscc) {
    Run $Iscc @('/Q', "/DSurum=$Ver", "/DKaynak=$Onedir", "/DCikti=$Dist",
                "/DSimge=$(Join-Path $Gaze 'resources\icon.ico')",
                (Join-Path $PSScriptRoot 'kurulum.iss'))
    if (-not (Test-Path $Setup)) { throw "Inno Setup cikti vermedi: $Setup" }
    Say ("kurulum: {0:N1} MB" -f ((Get-Item $Setup).Length / 1MB))
} else {
    Write-Warning ('Inno Setup bulunamadi: YALNIZ ZIP uretiliyor. Kurulum.exe icin ' +
                   'Inno Setup 6 kurun ya da -InnoKur ile calistirin.')
}

# --- 6. portable zip ---------------------------------------------------------
$Zip = Join-Path $Dist "GozFaresi-$Ver-Windows-Tasinabilir.zip"
Run $VPy @($Hazirla, 'tasinabilir-zip', $Onedir, $Zip)
$limit = 600MB
foreach ($f in @($Zip, $Setup)) {
    if ((Test-Path $f) -and (Get-Item $f).Length -ge $limit) {
        throw ("{0} 600 MB sinirini asiyor ({1:N1} MB)" -f (Split-Path -Leaf $f), ((Get-Item $f).Length / 1MB))
    }
}

# --- 7. SHA256SUMS -----------------------------------------------------------
Run $VPy @($Hazirla, 'sha256sums', $Dist)
Say 'bitti:'
Get-ChildItem $Dist | ForEach-Object { Say ("  {0,-50} {1,10:N1} MB" -f $_.Name, ($_.Length / 1MB)) }
