@echo off
chcp 65001 >nul
cd /d "%~dp0"

rem --- Python'u PATH'e guvenmeden bul (KUR-DERIN.bat ile ayni sira) ---
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

set "VOICEDIR=C:\Users\Public\piper-voices"
set "SORUN=0"

echo ============================================================
echo   YEREL KONUSMA YIGINI kurulumu (TTS + dikte)
echo   Internet gerekir. Whisper modeli ile birlikte ~2 GB indirme.
echo ============================================================

echo.
echo [1/6] Pillow (Turkce metin cizimi)
%PYEXE% -m pip install "Pillow>=10.0"
if errorlevel 1 set "SORUN=1"

echo.
echo [2/6] Piper TTS (yerel Turkce sinir-agi sesi)
%PYEXE% -m pip install piper-tts
if errorlevel 1 set "SORUN=1"

echo.
echo [3/6] Turkce Piper sesi -^> %VOICEDIR%
rem NOT: Piper resmi ses katalogunda su an TEK Turkce ses var: tr_TR-dfki-medium.
rem (tr_TR-fahrettin / tr_TR-fettah adlari katalogda yok, indirme 404 verir.)
if not exist "%VOICEDIR%" mkdir "%VOICEDIR%"
%PYEXE% -m piper.download_voices tr_TR-dfki-medium --data-dir "%VOICEDIR%"
if errorlevel 1 set "SORUN=1"

echo.
echo [4/6] espeak-ng verisini ASCII yola kopyala
rem ZORUNLU: piper'in espeak motoru (C eklentisi) Turkce karakterli yolu okuyamaz.
rem Python "C:\Users\<Turkce harfli ad>\..." altinda kurulu oldugu icin paket ici veri
rem dizini calismaz; ASCII bir yola kopyalayip kodda espeak_data_dir ile veriyoruz.
%PYEXE% -c "import os,shutil,piper; s=os.path.join(os.path.dirname(piper.__file__),'espeak-ng-data'); d=r'%VOICEDIR%\espeak-ng-data'; shutil.copytree(s,d,dirs_exist_ok=True); print('espeak-ng-data ->',d)" 2>nul
if errorlevel 1 (
  echo HATA: ESPEAK VERISI KOPYALANAMADI - yukaridaki [2/6] adimini kontrol edin.
  set "SORUN=1"
)

echo.
echo [5/6] Mikrofon + Whisper (yerel dikte)
%PYEXE% -m pip install sounddevice openai-whisper
if errorlevel 1 set "SORUN=1"

echo.
echo [6/6] Whisper 'medium' dikte modeli (~1.5 GB, uzun surebilir)
echo       Simdi indirilirse ilk dikte aninda beklemek gerekmez.
%PYEXE% -c "import whisper; whisper.load_model('medium'); print('WHISPER MEDIUM HAZIR')"
if errorlevel 1 (
  echo UYARI: Dikte modeli indirilemedi. Sesli yazma ilk kullanimda
  echo        indirmeyi tekrar deneyecek - goz faresi bundan etkilenmez.
)

echo.
echo === Dogrulama ===

%PYEXE% -c "import numpy, mediapipe, whisper, sounddevice, piper, PIL; print('PAKETLER YUKLENDI'); print('numpy', numpy.__version__)"
if errorlevel 1 set "SORUN=1"

rem Gercek sentez testi. Sadece 'import piper' yeterli DEGIL: espeak verisi
rem eksikse piper ice aktarilir ama ilk konusmada surec komple cokup kapanir.
%PYEXE% -c "from piper import PiperVoice; v=PiperVoice.load(r'%VOICEDIR%\tr_TR-dfki-medium.onnx', espeak_data_dir=r'%VOICEDIR%\espeak-ng-data'); print('SES TESTI OK', sum(len(c.audio_int16_bytes) for c in v.synthesize('Merhaba')))"
if errorlevel 1 set "SORUN=1"

echo.
if "%SORUN%"=="1" goto EKSIK

echo ============================================================
echo   KURULUM TAMAM - yerel konusma yigini kullanima hazir.
echo ============================================================
goto SON

:EKSIK
echo ============================================================
echo   KURULUM EKSIK - internete baglanip bu dosyayi tekrar
echo   calistirin. Sorun surerse yukaridaki ilk HATA/ERROR
echo   satirini not alin.
echo   NOT: Goz faresi bu eksige ragmen normal calisir.
echo ============================================================

:SON
echo.
pause
