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
  echo HATA: Python bulunamadi. Once Python 3.11 kurun ^(python.org^).
  pause
  exit /b 1
)

echo Gerekli paketler kuruluyor (opencv, mediapipe, numpy, pyyaml)...
echo Bu islem birkac dakika surebilir.
%PYEXE% -m pip install -r requirements.txt
echo.
echo Kurulum bitti. Calistirmak icin BASLAT.bat dosyasini cift tiklayin.
pause
