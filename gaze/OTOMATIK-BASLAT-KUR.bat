@echo off
chcp 65001 >nul
rem Goz-surucusunu Windows oturumu acilinca KENDILIGINDEN baslatir.
rem Kullanici surucuyu kendisi baslatamaz; bilgisayar yeniden basladiginda
rem (guncelleme, elektrik kesintisi) birinin gelip BASLAT.bat'a tiklamasi
rem gerekmesin diye "Baslangic" klasorune bir kisayol koyar.
rem Geri almak icin: KALDIR parametresiyle calistirin:  OTOMATIK-BASLAT-KUR.bat KALDIR

set "HEDEF=%~dp0BASLAT.bat"
set "KISAYOL=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\Goz-Surucusu.lnk"

if /i "%~1"=="KALDIR" (
  if exist "%KISAYOL%" del "%KISAYOL%"
  echo Otomatik baslatma KALDIRILDI.
  pause
  exit /b 0
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut($env:KISAYOL);" ^
  "$s.TargetPath=$env:HEDEF; $s.WorkingDirectory=(Split-Path $env:HEDEF);" ^
  "$s.WindowStyle=7; $s.Description='Webcam goz-surucusu'; $s.Save()"
if errorlevel 1 (
  echo HATA: kisayol olusturulamadi.
  pause
  exit /b 1
)
echo Tamam: surucu artik Windows oturumu acilinca kendiliginden baslayacak.
echo.
echo ONERILER (bakim veren icin):
echo  - Ayarlar ^> Hesaplar ^> Oturum acma: "uyku sonrasi oturum acma" = Hicbir zaman
echo  - Ayarlar ^> Sistem ^> Guc: uyku = Hicbir zaman (uyku kamerayi keser)
echo  - Yonetici pencereleri (Gorev Yoneticisi, UAC onaylari) gozle TIKLANAMAZ;
echo    bunlar icin yardim gerekir.
pause
