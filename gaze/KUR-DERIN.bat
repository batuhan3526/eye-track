@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- Python'u PATH'e guvenmeden bul ---
set "PYEXE="
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" set "PYEXE=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
if not defined PYEXE (
  py -3 --version >nul 2>nul
  if not errorlevel 1 set "PYEXE=py -3"
)
if not defined PYEXE (
  python --version >nul 2>nul
  if not errorlevel 1 set "PYEXE=python"
)
if not defined PYEXE (
  echo HATA: Python bulunamadi. Once KUR.bat ile temel kurulumu yapin.
  pause
  exit /b 1
)

echo ============================================================
echo   DERIN BAKIS MODELI kurulumu (GPU onerilir, ~3 GB indirme)
echo   NVIDIA GPU yoksa da kurulabilir; model CPU'da yavas calisir.
echo ============================================================
%PYEXE% -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu121
%PYEXE% -m pip install --no-deps ptgaze
%PYEXE% -m pip install omegaconf timm coloredlogs safetensors huggingface_hub scipy
echo.
echo Kurulum bitti. config.yaml icinde deep_gaze: true yapin ve
echo BASLAT.bat ile acip F9 x2 ile YENIDEN KALIBRE edin.
pause
