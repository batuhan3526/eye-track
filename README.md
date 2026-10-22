# Göz Faresi

Webcam ile göz kontrolü: bilgisayarın kamerası gözünüzün baktığı yeri bulur ve fare imlecini oraya götürür. ALS (ve elini kullanamayan) kullanıcılar için yazıldı. Tıklamak için bir tuş, bir düğme (switch), bir oyun kolu düğmesi, göz kırpma ya da bakıp bekleme kullanılabilir. Özel donanım gerekmez; Windows ve Linux için hazır paketleri vardır. Ayrıca sesli okuma, sesle yazma, hazır cümle ızgarası (AAC) ve acil alarm içerir.

> Program hiçbir garanti olmadan verilir (GPL).

## İçindekiler
1. [Hızlı başlangıç (kullanıcılar için)](#1-hızlı-başlangıç)
2. [Kullanım](#2-kullanım)
3. [Paketleri kendiniz derleme](#3-paketleri-kendiniz-derleme)
4. [Kaynak koddan çalıştırma](#4-kaynak-koddan-çalıştırma)
5. [Klasör haritası](#5-klasör-haritası)
6. [Lisans](#6-lisans)
7. [Bilinen sınırlar](#7-bilinen-sınırlar-ve-denenmeyenler)
8. [English summary](#english-summary)

## 1. Hızlı başlangıç

Hazır paketi kullanmak için Python, komut satırı ya da teknik bilgi gerekmez. Ayrıntılı anlatım: [packaging/BENI-OKU-KURULUM.txt](packaging/BENI-OKU-KURULUM.txt).

**Windows (önerilen: kurulum dosyası)**
1. `GozFaresi-...-Windows-Kurulum.exe` dosyasına çift tıklayın. Yönetici şifresi istemez.
2. Mavi "Windows bilgisayarınızı korudu" penceresi çıkarsa: **Ek bilgi**, sonra **Yine de çalıştır**. Program ücretli bir imza sertifikasıyla imzalı olmadığı için çıkar; programda sorun olduğu anlamına gelmez. Tarayıcı indirirken uyarırsa **Sakla / Yine de sakla** deyin.
3. Kurulum bitince program açılır ve ilk açılış sihirbazı başlar.

**Windows (taşınabilir, USB bellek):** `GozFaresi-...-Windows-Tasinabilir.zip` dosyasına sağ tıklayıp "Tümünü ayıkla" deyin, `GozFaresi\GozFaresi.exe` dosyasına çift tıklayın. Ayarlar programın kendi klasöründe durur; oradaki `tasinabilir` dosyasını silmeyin.

**Linux (AppImage)**
1. `GozFaresi-...-Linux-x86_64.AppImage` dosyasına sağ tıklayın, Özellikler, İzinler, "Programın çalışmasına izin ver" kutusunu işaretleyin; çift tıklayın.
2. Açılmazsa dosyanın klasöründe terminal açıp şunu yazın: `chmod +x GozFaresi-*-Linux-x86_64.AppImage` ve `./GozFaresi-*-Linux-x86_64.AppImage --appimage-extract-and-run`
3. Oturum açarken "Xorg" / "Ubuntu on Xorg" seçmeniz önerilir (Wayland'de büyüteç ve bakıp-bekleme halkası çalışmaz). Sihirbazdaki "İzinleri kur" düğmesine bir kez basın, parolayı girin, oturumu kapatıp açın. (Bu izin hesabınızı "input" grubuna ekler; bedeli kurulum notunda yazılıdır.) `tar.gz` seçeneği de aynı içeriktir.

**İlk açılış sihirbazı** her adımda tek karar sorar; her adım "Atla" ile geçilir, "Geri" ile dönülür:
1. Dil (Türkçe / English) ve Linux'ta izin.
2. Kamera bulunur ve denenir.
3. Bilgisayarın hızı ölçülür; derin bakış modeli ve dikte modeli buna göre seçilir. Gerekirse modeller internetten indirilir (dikte için birkaç yüz MB); internet yoksa program onlarsız da çalışır.
4. Tıklama ve diğer işler için tuşlar ya da düğmeler seçilir (bir tuş tek iş yapar).
5. Kalibrasyon: ekranda süzülen topu gözünüzle takip edin (yaklaşık 25 saniyelik iki tur). Kalibrasyon kaydedilir; sonraki açılışlarda tekrar gerekmez.

**Dil:** Program Türkçe açılır. İngilizce için sihirbazın ilk adımında "English" seçin ya da Ayarlar, "Dil (Language)" sekmesi, Kaydet. İngilizce metinler makine yardımıyla çevrilmiştir ve anadili İngilizce olan biri tarafından denetlenmemiştir; denetlenmiş dil Türkçedir.

**Kaldırma (Windows):** Ayarlar, Uygulamalar, Göz Faresi, Kaldır. Ayarlarınız ve kalibrasyonunuz korunur; silmek için çıkan kutuyu işaretleyin. Taşınabilir sürümde klasörü silmeniz yeter.

**Sorun olursa:** durum penceresindeki "Günlüğü aç" düğmesi `gaze.log` dosyasını açar; yardım isterken onu gönderin.

## 2. Kullanım

Çıkış, kalibrasyon ve duraklatma tuşları çift basış ister (kazara tetiklenmesin diye). Tuşlar her pencerede çalışır. Hepsi Ayarlar penceresinden değiştirilebilir. Tam liste, takip kötüyse ne yapılacağı ve ayarlar: **[teslimatlar/KULLANIM-KILAVUZU.md](teslimatlar/KULLANIM-KILAVUZU.md)**.

| İş | Nasıl |
|---|---|
| İmleci götürme | Gözünüzle bakın. İmleç baktığınız yere en yakın tıklanabilir öğenin üstüne oturur (hedefe yapışma). |
| Sol tık | `Space` ya da oyun kolu **A**. Parmak yoksa: bakıp bekleme (`dwell_click`) ya da uzun göz kırpma (`blink_click`) ayarı. |
| Sağ tık, çift tık, sürükle, kaydır | `F2` tık menüsü: seçeneğe bakıp `Space`. Oyun kolunda B / Y / X. |
| Büyüteç tık (küçük hedefler) | `F6`: çevre 2,5 kat büyür, hedefe bakıp `Space`. |
| Seslendirme | `F4`: seçili metni yerel Türkçe sesle okur. `F11`: sustur; `F11` x2: son cümleyi tekrarla. |
| Sesle yazma (dikte) | `F3`: basın, konuşun, susun; yazı odaktaki kutuya girer. |
| AAC cümle ızgarası | `F5`: cümleye bakıp `Space` = konuşur. Tekrar `F5` = kapat. |
| Ekran klavyesi | `Scroll Lock` x2: Windows ekran klavyesi. (Yazı ve konuşma için OptiKey de kullanılabilir.) |
| ACİL ALARM | `F12` tuşunu 2 sn basılı tutun: siren ve "Yardım edin!". Durdurmak için yine 2 sn. Duraklatılmışken ve kalibrasyonda da çalışır. |
| Duraklat / devam | `F8` x2. Duraklatınca klavye normale döner; bakım veren yazacaksa önce duraklatın. |
| Hızlı düzeltme | `F7` x2: ortadaki noktaya 2 sn bakın (kayma olduysa). |
| Yeniden kalibrasyon | `F9` x2. Kalibrasyon sırasında tekrar `F9` x2 = vazgeç. Yeni kalibrasyon kötüyse `F2`, "Kalibrasyonu geri al". |
| Sürücüyü kapat | `F10` x2. |

## 3. Paketleri kendiniz derleme

Paketler (Windows kurulum + taşınabilir zip, Linux AppImage + tar.gz, kaynak zip) bu depodan üç yolla derlenir. Sürüm numarası `gaze/eye_gaze/__init__.py` içindeki `__version__` değeridir (şu an 1.2.0). Derleme GPU gerektirmez.

### Yol 1: Kurulum gerektirmeden, GitHub Actions ile
Bilgisayarınıza hiçbir şey kurmadan, tarayıcıdan.
1. Depoyu GitHub'da açın (kendi kopyanızda: Fork).
2. Üstte **Actions** sekmesine girin, soldaki listeden **paketle** iş akışını seçin.
3. Sağda **Run workflow** düğmesine, sonra yeşil **Run workflow** düğmesine basın.
4. İş akışı bitince (Windows ve Linux işleri ayrı koşar, her biri en çok 120 dakikaya kadar çalışabilir) çalışmanın sayfasının altındaki **Artifacts** bölümünden indirin: `gozfaresi-windows` (Kurulum.exe ve zip), `gozfaresi-linux-kaynak` (AppImage, tar.gz, GPL kaynak dosyaları, kaynak zip), `gozfaresi-sha256sums` (SHA256SUMS).

Aynı iş akışı `v*` biçiminde bir sürüm etiketi (ör. `v1.2.0`) gönderildiğinde de kendiliğinden çalışır. Sürümü GitHub'da yayınlamak sahibin kararıdır: bu adım iş akışında bilerek yorum satırı olarak durur.

**Dikkat: bu iş akışı yazıldı ama GitHub'ın sunucularında henüz hiç çalıştırılmadı.** İlk çalıştırmada bir adım hata verebilir.

### Yol 2: Windows'ta kendi bilgisayarınızda
Gerekenler: **Python 3.11** (başka sürüm kabul edilmez; betik denetler) ve **Inno Setup 6** (yoksa yalnız taşınabilir zip çıkar; `-InnoKur` bayrağı onu winget ile kullanıcıya kurar). Depoyu indirin ve kök klasörde PowerShell açın:

```
powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\derle.ps1
```

İsteğe bağlı bayraklar: `-InnoKur` (Inno Setup yoksa kur), `-VenvKoru` (önceki derlemenin sanal ortamını yeniden kullan), `-Python <python.exe>` (Python 3.11'in yolu). Betik temiz bir sanal ortam kurar; sistem Python'unuza hiçbir şey kurmaz. Çıktı: `dist\surum-<sürüm>\` içinde `GozFaresi-<sürüm>-Windows-Kurulum.exe`, `GozFaresi-<sürüm>-Windows-Tasinabilir.zip` ve `SHA256SUMS`.

### Yol 3: Linux'ta (ya da Windows'ta Git Bash ile) Docker ile
Gerekenler: **Docker** ve bash. Kök klasörde:

```
bash packaging/linux/derle.sh
```

Betik Ubuntu 22.04 tabanlı bir Docker imajında derler (böylece Ubuntu 22.04 ve üstü, Debian 12, Mint 21+, Fedora 36+ üzerinde çalışır). Çıktı: `dist/surum-<sürüm>/` içinde `GozFaresi-<sürüm>-Linux-x86_64.AppImage`, `...tar.gz` ve `SHA256SUMS`. Kaynak zip için (git'e işlenmiş hâlini paketler): `bash packaging/kaynak.sh`.

**Süre ve disk:** raporlarda derleme süresi ya da gereken boş disk alanı yazılı değildir; o yüzden söz verilmez. Bilinen tek şey bitmiş paketlerin boyutudur (sürüm 1.2.0): Windows Kurulum.exe 206 MB, Windows zip 259 MB, Linux AppImage 541 MB, Linux tar.gz 584 MB, kaynak zip 1,8 MB; GPL kaynak dosyaları toplam yaklaşık 880 MB. Derleme internetten çok sayıda dosya indirir.

## 4. Kaynak koddan çalıştırma

Geliştiriciler içindir. Python 3.11 gerekir.
```
cd gaze
python gozfaresi.py
```
İlk açılışta `config.default.yaml` kopyalanır ve sihirbaz açılır. `gaze\BASLAT.bat` (ve `KUR*.bat`) paketlemeden önceki eski Windows kaynak kurulumu içindir ve `config.yaml` olmadan açılmaz; yeni kurulumda `python gozfaresi.py` kullanın. Geliştirici notları ve test komutları: [gaze/README.md](gaze/README.md).

## 5. Klasör haritası

| Klasör / dosya | İçerik |
|---|---|
| `gaze/` | Programın kendisi: sürücü (`eye_gaze/`), başlatıcı `gozfaresi.py`, kaynaklar (`resources/`), testler (`tests/`), geliştirici README'si. |
| `packaging/` | Paket derleme: Windows (`windows/`, Inno Setup), Linux (`linux/`, AppImage), `kaynak.sh`, `hazirla.py`, kurulum notu ve üçüncü taraf lisansları. |
| `teslimatlar/` | Kullanıcıya dönük belgeler: kullanım kılavuzu, OptiKey rehberleri ve Türkçe AAC paketi. |
| `.github/workflows/` | `paketle.yml`: GitHub Actions derleme iş akışı. |

## 6. Lisans

Göz Faresi'nin kendi kodu **GNU GPL 3.0 ya da sonraki sürümleri (GPL-3.0-or-later)** ile lisanslıdır: [LICENSE](LICENSE).

Düz anlatımla:
- Ücretsiz kullanabilir, paylaşabilir ve değiştirebilirsiniz.
- Değiştirilmiş bir sürümü başkalarına veren, onun tam kaynak kodunu da aynı lisansla vermek zorundadır. GPL satışı yasaklamaz, ama kapalı kaynak sürüm yapılamaz.
- Program hiçbir garanti olmadan verilir.
- Ek izin (GPL madde 7): Göz Faresi, dikte motorunun içindeki Intel (oneMKL, OpenMP) kitaplıkları, OpenSSL 1.1 ve NVIDIA CUDA/cuDNN/cuBLAS kitaplıklarıyla, değiştirilmemiş hâlde birlikte dağıtılabilir. Göz Faresi'nin kendi kaynağı yine GPL ile verilmelidir. Bu izin piper-tts ve espeak-ng gibi başka GPL işlerini kapsamaz (onlar ayrı bir program olarak çalışır).

**Ticari olmayan iki parça (CC BY-NC-SA 4.0):**
- Türkçe ses (`tr_TR-dfki-medium`). Bu ses, Blizzard 2013 Lessac verisiyle eğitilmiş bir İngilizce sesten türetilmiştir ve o verinin şartları doğrulanamamıştır (açık soru).
- Derin bakış modeli: ağırlıkları ETH-XGaze veri kümesiyle eğitilmiştir; bu veri kümesi ticari olmayan kullanım içindir.

Bu yüzden paket **bu hâliyle satılamaz, ticari amaçla kullanılamaz ve ticari olarak dağıtılamaz**. Kodu satmak isteyen bu iki parçayı çıkarmalı ya da ticari kullanıma izin verenlerle değiştirmelidir.

Üçüncü taraf bileşenlerin listesi ve lisansları: [packaging/UCUNCU-TARAF-LISANSLAR.txt](packaging/UCUNCU-TARAF-LISANSLAR.txt). Paketteki her GPL / LGPL / MPL parçanın tam kaynağı paketlerin yanında ayrı dosyalar olarak verilir.

## 7. Bilinen sınırlar ve denenmeyenler

Hazır paketler Docker/Xvfb, WSLg ve sahte kamera ile denendi; gerçek kullanımda şunlar henüz denenmedi:
- **Gerçek Linux bilgisayar:** gerçek kamera, gerçek masaüstü (GNOME / KDE), NVIDIA GPU paketi, çoklu monitör, Ubuntu 22.04 dışındaki dağıtımlar.
- **Gerçek Wayland oturumu:** "İzinleri kur" sonrası oturum kapat-aç, tuş dinleme, pano.
- **SmartScreen uyarısı** ilk gerçek internet indirmesinde görülmedi (dosya yerelde derlendi); kurulum dosyası imzasızdır.
- Paketli Windows'ta Whisper diktesi (gerçek mikrofon gerekir), Windows kaldırıcıdaki "Ayarları ve kalibrasyonu da sil" kutusu ve Linux'ta İngilizce yedek ses denenmedi.
- Hiçbiri gerçek switch ve gerçek hoparlörle denenmedi; acil alarmı ilk kullanımda düşük seste deneyin.
- **GitHub Actions iş akışı** hiç çalıştırılmadı (bkz. bölüm 3, Yol 1).
- Linux'ta izin kurulmadan kullanılan X11 yedek yolunda program çok uzun süre tamamen meşgul olursa masaüstü oturumu kapanabilir; izni kurmanız (Xorg'da da) önerilir.
- Türkçe ses verisinin lisans şartları açık bir sorudur (bkz. bölüm 6).
- Windows paketinde derin model işlemcide (CPU) çalışır.

## English summary

**Göz Faresi** ("Eye Mouse") moves the mouse cursor with your eyes using an ordinary webcam. It is written for people with ALS and is free software (GPL-3.0-or-later). It also has text-to-speech, voice dictation, an AAC phrase grid and an emergency alarm (hold F12 for 2 s). The interface is Turkish by default; English is selectable (machine-assisted translation, not reviewed by a native speaker).

**Use it:** download the Windows installer (`...-Windows-Kurulum.exe`, or the portable zip) or the Linux AppImage, run it and follow the first-run wizard. The Windows installer is unsigned, so SmartScreen may warn: choose "More info", then "Run anyway". On Linux, pick an Xorg session and press "Install permissions" in the wizard once. Keys are listed in `teslimatlar/KULLANIM-KILAVUZU.md` (in Turkish).

**Build it:** (1) no install: GitHub, Actions, "paketle", Run workflow, then download the artifacts (never yet run on GitHub's servers); (2) Windows: Python 3.11 + Inno Setup 6, then `powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\derle.ps1`; (3) Linux: Docker, then `bash packaging/linux/derle.sh`. Output goes to `dist/surum-<version>/`. From source: `cd gaze && python gozfaresi.py`.

**Licence:** GPL-3.0-or-later with a section-7 additional permission (see `LICENSE`, `packaging/UCUNCU-TARAF-LISANSLAR.txt`). The Turkish voice and the deep gaze model are non-commercial (CC BY-NC-SA 4.0), so the packages as shipped cannot be sold or used commercially.

**Not tested yet:** a real Linux desktop and real Wayland session, the SmartScreen prompt on a real download, real switch hardware, and the GitHub Actions workflow.
