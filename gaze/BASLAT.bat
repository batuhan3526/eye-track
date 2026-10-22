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
  echo HATA: Python bulunamadi.
  echo Python 3.11 kurulu degilse kurun; kuruluysa once KUR.bat calistirin.
  pause
  exit /b 1
)

echo ===============================================
echo   Webcam goz-surucusu baslatiliyor
echo   - Kalibrasyon gerekiyorsa: suzulen yesil TOPU gozunuzle takip edin
echo   - Imleci gozunuzle surun, parmak dugmesiyle (Space) secin
echo   - Cikis: F10 iki kez  ^|  Duraklat: F8 iki kez
echo   - Surucu beklenmedik sekilde kapanirsa KENDILIGINDEN yeniden acilir
echo ===============================================
echo Python: %PYEXE%

rem --- Bekci dongusu ---
rem Cikis kodu 0 = kullanici F10 x2 ile cikti -> pencere kapanir.
rem Cikis kodu 2 = config.yaml duzeltilmeli -> yeniden baslatmak ise yaramaz.
rem Diger her sey (cokme, kamera acilamadi) -> 5 sn sonra yeniden baslat:
rem bu kullanici sureci kendisi yeniden baslatamaz.
:dongu
%PYEXE% gaze_driver.py
set "KOD=%ERRORLEVEL%"
if "%KOD%"=="0" goto bitti
if "%KOD%"=="2" goto ayar
echo.
echo Surucu beklenmedik sekilde kapandi (kod %KOD%). 5 sn sonra YENIDEN baslatiliyor...
echo (Vazgecmek icin bu pencereyi kapatin.)
timeout /t 5 /nobreak >nul
goto dongu

:ayar
echo.
echo Surucu ayar hatasi yuzunden BASLATILAMADI - yukaridaki [ayar] satirlarina bakin.
pause
exit /b 2

:bitti
echo.
echo Surucu kapandi.
timeout /t 3 >nul
