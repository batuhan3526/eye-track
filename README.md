# Göz Faresi

Webcam ile göz kontrolü: bilgisayarın kamerası gözünüzün baktığı yeri bulur ve fare imlecini oraya götürür. ALS (ve elini kullanamayan) kullanıcılar için yazıldı. Tıklamak için bir tuş, bir düğme (switch), bir oyun kolu düğmesi, göz kırpma ya da bakıp bekleme kullanılabilir. Özel donanım gerekmez; Windows ve Linux için hazır paketleri vardır. Ayrıca sesli okuma, sesle yazma, hazır cümle ızgarası (AAC) ve acil alarm içerir.

> Program hiçbir garanti olmadan verilir (GPL).

> **macOS desteklenmez.** Program Windows 10/11 ve Linux içindir.

## Sürüm 1.3.2'de neler değişti

- **Kamera araması artık programı kapatamaz — kalıcı düzeltme.** 1.3.1'de de bir test kullanıcısında sihirbaz "Kameralar aranıyor" sırasında kapandı. `cokme.log` bu kez DirectShow'u gösterdi: Media Foundation o kamera numarasında görüntü veremeyince arama DirectShow'a geçiyor, kameranın kendi modunu okuduktan sonra aynı bağlantıda daha büyük bir çözünürlük (1920x1200) istiyordu ve kamera sürücüsü kendi iş parçacığında bellek hatasıyla çöktü. Böyle bir hatayı hiçbir program kodu yakalayamaz; çöken süreç ölür. Bu yüzden artık:
  - **Kamera araması ayrı bir süreçte yapılır.** Bir sürücü çökerse yalnız o süreç ölür; sihirbaz (ya da ayarlar penceresi, ya da sürücünün ilk açılıştaki araması) ayakta kalır, çökmenin olduğu modu atlar ve aramaya kaldığı yerden devam eder. Ölçülmüş modlar korunur. Bir kamera üç kez çöktürürse bırakılır, ölçebildiği modlar kullanılır. Her çökme `cokme.log`'a hangi kamera ve hangi modda olduğuyla yazılır.
  - **Sihirbazın kamera önizlemesi de ayrı süreçte açılır.** Başlatıcı (durum penceresi, sihirbaz, ayarlar) hiçbir kamerayı kendi içinde açmaz.
  - **DirectShow ve Linux'ta (V4L2) görüntü akan bir bağlantıdan artık daha büyük mod istenmez.** Her bağlantıya ilk mod ilk kareden önce, sonrakiler yalnız küçülerek istenir (1.2.0'ın yaptığı gibi); kameranın kendi modu ve MJPG modları kendi bağlantılarında ölçülür.
- Gerçek donanımda denendi: bir USB webcam ve sanal bir DirectShow kamerası (OBS Virtual Camera) ile arama ve önizleme; ölçüm sırasında kamera açıkken gerçek bir bellek hatası (0xC0000005) üç kez çıkarıldı — arama her seferinde devam etti, kamera hemen yeniden açıldı.

## Sürüm 1.3.1'de neler değişti

- **Kurulum sihirbazı kamera adımında kapanıyordu — düzeltildi.** "Kameralar aranıyor" sırasında program bazı kameralarda uyarısız kapanıyordu. Neden: Media Foundation ile açılmış bir kameradan kare okunduktan sonra aynı bağlantıda başka bir çözünürlük istenince OpenCV bir sonraki kareyi eski görüntü belleğinin üstüne yeni boyutta kuruyor; genişlik aynı kalıp yükseklik büyüyünce bellek dışına okuyup süreci öldürüyordu (ör. 1280x720'den sonra 1280x960; kendi modu 1920x1080 olan kameralarda ilk ölçümde). Artık Media Foundation'da her mod kendi bağlantısında, ilk kareden önce istenerek ölçülür — sürücünün kamerayı açtığı yolla aynı. Aynı hata bazı kameralarda Media Foundation ölçümlerini sessizce çöpe atıp düşük çözünürlük seçtiriyordu; o da düzeldi. Arama biraz uzadı (kamera başına en fazla bir dakika). Ayarlar → Kamera → **Kameraları ara** ve sürücünün ilk açılıştaki araması da aynı düzeltmeyi kullanır.
- **Çökme günlüğü.** Program bir sürücü yüzünden yine de uyarısız kapanırsa, her iş parçacığının o anki yeri veri klasöründeki `cokme.log` dosyasına yazılır (Windows kurulumunda `%LOCALAPPDATA%\GozFaresi`). Yardım isterken `gaze.log` ile birlikte gönderin.

## Sürüm 1.3.0'da neler değişti

Bir test kullanıcısının şikâyetleri (imleç ekranın yarısına ulaşıyor, bakılan yerin tersine gidiyor, çok ağır; kamera görüntüsü "uzun ve ince"; ayarlar yalnız dosyada) üzerine:

- **Duruş değişikliğine dayanıklı kalibrasyon.** Yatay ve dikey için model ayrı ayrı, kişinin kendi verisiyle seçilir. İki tur arasında oturuş değişince imleci savuran baş girdileri gerekiyorsa dışarıda kalır. Mevcut kalibrasyonunuz ilk açılışta kayıtlı örneklerden **bir kez** yenilenir: yeniden kalibrasyon gerekmez, eskisi yedekte kalır ve yeni model yalnız eskisinden kötü değilse kullanılır.
- **Kalibrasyon sonuç ekranı.** Kalibrasyon bitince ekranda not (İYİ / ORTA / KÖTÜ), yatay ve dikey hata, imlecin ekranın ne kadarına ulaşabildiği ve gerekirse ipuçları (ışık, kamera yeri, başı sabit tutma, oturuş) birkaç saniye görünür. Not sınırları ekranın boyutuna göre ölçeklenir. Bu ekrandayken **F9 ×2** hemen yeni bir kalibrasyon başlatır.
- **Kamera.** Kamera araması kameranın verdiği bütün modları ölçer (en fazla 1920 genişlik, yavaş modlarda MJPG de denenir) ve görüntüsü gerilmiş modları hiç seçmez. Kayıtlı kalibrasyon hangi modda yapıldıysa kamera o modda açılmaya devam eder; yeni seçilen mod bir sonraki kalibrasyonda kullanılır. Durum penceresi görüntüyü kendi oranında gösterir. Derin model için kameranın görüş açısı ayarlanabilir (`camera_hfov_deg`, varsayılan "auto" = eskisi gibi).
- **Göz kapalı eşiği** kameranın en-boy oranından bağımsız ölçülür; "auto" iken kişinin kendi açık-göz değerinden öğrenilir ve hiçbir kamerada 1.2.0'dan daha sıkı değildir.
- **İmleç hassasiyeti (1-5).** Filtreler kare sayısı yerine saniyeyle çalışır; kamera yavaşlasa da his aynı kalır. 1 = 1.2.0'ın sakin hissi; yeni kurulumda 3 (küçük bir nişan düzeltmesi 1 saniyeden kısa sürede oturur). Pikselleri çok küçük ya da çok büyük ekranlarda piksel ayarları ekranın fiziksel boyutuna göre ölçeklenir; yaygın masaüstü ekranlarında hiçbir sayı değişmez.
- **Ayarlar penceresi.** Yeni **İmleç**, **Büyüteç** ve **Kalibrasyon** sekmeleri; Tıklama, Kamera ve Ses sekmelerinde yeni ayarlar (bakma süresi ve alanı, göz kapalı eşiği, büyüteç alanı, topun hızı ve yolu, tur sayısı, ölçülen kamera modları, görüş açısı, uyarı sesi yüksekliği). Kaydet yalnız değiştirdiğiniz satırları yazar; pencere küçük dizüstü ekranlarına da sığar.

**1.2.0'dan yükseltenler için:**
- İmleç bugünkü gibi davranır: `config.yaml`'ınızda `cursor_sensitivity` satırı olmadığından seviye 1 geçerlidir. Daha hızlı isterseniz Ayarlar → İmleç → **İmleç hassasiyeti**'ni kaydırın (3 = yeni kurulumların ayarı) → Kaydet.
- Kalibrasyonunuz ilk açılışta yenilenir (konsolda "KAYITLI ORNEKLERDEN yenilendi" satırı); eskisi `calibration.json.<sayı>.bak` olarak saklanır.
- Yeni kamera seçimini (ör. 1080p MJPG) kullanmak için: Ayarlar → Kamera → **Kameraları ara** → kamerayı seçin → **Kaydet**, ardından **F9 ×2** ile yeniden kalibre edin. Yapmazsanız kamera, kalibrasyonun yapıldığı modda açılmaya devam eder.
- 1.2.0'a geri dönmek desteklenir: 1.2.0 yeni biçimdeki kalibrasyonu okuyamaz ve kendi yedeğine döner. Bu yedek, 1.3.0'da en fazla bir yeniden kalibrasyon yaptıysanız kesin durur; daha fazlasında 1.2.0'da bir kalibrasyon gerekebilir. 1.2.0'ın kamerası o zaman MJPG'siz bir 1080p moduyla (düşük kare hızı) açılabilir: 1.2.0'da Ayarlar → Kamera → **Kameraları ara**'yı yeniden çalıştırın.

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

**Sorun olursa:** durum penceresindeki "Günlüğü aç" düğmesi `gaze.log` dosyasını açar; yardım isterken onu gönderin. Program uyarısız kapandıysa aynı klasördeki `cokme.log` dosyasını da ekleyin.

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
| Yeniden kalibrasyon | `F9` x2. Top izlenirken tekrar `F9` x2 = vazgeç. Bitince sonuç ekranı notu gösterir; o ekrandayken `F9` x2 = hemen yeniden. Yeni kalibrasyon kötüyse `F2`, "Kalibrasyonu geri al". |
| İmleç hızı, tıklama, büyüteç, kalibrasyon, kamera ayarları | Ayarlar penceresi (Başlat menüsünde "Göz Faresi - Ayarlar"). İmleç ağır ya da titrek geliyorsa: İmleç sekmesi → **İmleç hassasiyeti**. |
| Sürücüyü kapat | `F10` x2. |

## 3. Paketleri kendiniz derleme

Paketler (Windows kurulum + taşınabilir zip, Linux AppImage + tar.gz, kaynak zip) bu depodan üç yolla derlenir. Sürüm numarası `gaze/eye_gaze/__init__.py` içindeki `__version__` değeridir (şu an 1.3.2). Derleme GPU gerektirmez.

### Yol 1: Kurulum gerektirmeden, GitHub Actions ile
Bilgisayarınıza hiçbir şey kurmadan, tarayıcıdan.
1. Depoyu GitHub'da açın (kendi kopyanızda: Fork).
2. Üstte **Actions** sekmesine girin, soldaki listeden **paketle** iş akışını seçin.
3. Sağda **Run workflow** düğmesine, sonra yeşil **Run workflow** düğmesine basın.
4. İş akışı bitince (Windows ve Linux işleri ayrı koşar, her biri en çok 120 dakikaya kadar çalışabilir) çalışmanın sayfasının altındaki **Artifacts** bölümünden indirin: `gozfaresi-windows` (Kurulum.exe ve zip), `gozfaresi-linux-kaynak` (AppImage, tar.gz, GPL kaynak dosyaları, kaynak zip), `gozfaresi-sha256sums` (SHA256SUMS).

Aynı iş akışı `v*` biçiminde bir sürüm etiketi (ör. `v1.3.0`) gönderildiğinde de kendiliğinden çalışır. Sürümü GitHub'da yayınlamak sahibin kararıdır: bu adım iş akışında bilerek yorum satırı olarak durur.

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
- **1.3.0'ın gerçek donanımda denenmeyen parçaları:** gerçek kameralarda MJPG isteme (DirectShow / Media Foundation / V4L2), oturum içinde kamera modunun değişmesi (F9 ile yeni moda geçiş, iptalde eski moda dönüş) ve ekranın fiziksel boyutunun okunması (EDID / xrandr). Hepsi sahte kamera ve sahte donanımla denendi.
- Gözlük takanlarda **dikey isabet** hâlâ sınırlı olabilir: 1.3.0 duruş değişikliğinin yol açtığı savrulmayı giderir, ama zayıf dikey göz sinyalini artıramaz. Sonuç ekranı dikey ulaşım düşükse ipuçlarını gösterir.
- macOS desteklenmez.

## English summary

**Göz Faresi** ("Eye Mouse") moves the mouse cursor with your eyes using an ordinary webcam. It is written for people with ALS and is free software (GPL-3.0-or-later). It also has text-to-speech, voice dictation, an AAC phrase grid and an emergency alarm (hold F12 for 2 s). The interface is Turkish by default; English is selectable (machine-assisted translation, not reviewed by a native speaker).

**Use it:** download the Windows installer (`...-Windows-Kurulum.exe`, or the portable zip) or the Linux AppImage, run it and follow the first-run wizard. The Windows installer is unsigned, so SmartScreen may warn: choose "More info", then "Run anyway". On Linux, pick an Xorg session and press "Install permissions" in the wizard once. Keys are listed in `teslimatlar/KULLANIM-KILAVUZU.md` (in Turkish).

**Build it:** (1) no install: GitHub, Actions, "paketle", Run workflow, then download the artifacts (never yet run on GitHub's servers); (2) Windows: Python 3.11 + Inno Setup 6, then `powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\derle.ps1`; (3) Linux: Docker, then `bash packaging/linux/derle.sh`. Output goes to `dist/surum-<version>/`. From source: `cd gaze && python gozfaresi.py`.

**Licence:** GPL-3.0-or-later with a section-7 additional permission (see `LICENSE`, `packaging/UCUNCU-TARAF-LISANSLAR.txt`). The Turkish voice and the deep gaze model are non-commercial (CC BY-NC-SA 4.0), so the packages as shipped cannot be sold or used commercially.

**New in 1.3.2:** the camera search can no longer take the program down. In 1.3.1 a tester's wizard still vanished at "looking for cameras": with no Media Foundation picture for a camera number the search went on to DirectShow, read the camera's own mode, then asked that same capture for 1920x1200, and the camera driver faulted on its own streaming thread - nothing in a process survives that. The search now runs in a child process: a driver fault ends only the child, and the search goes on where it was, without the mode that faulted (a camera that faults three times is given up with the modes it did measure; each fault is written to `cokme.log`). The wizard's camera preview runs in a child too, so the launcher never opens a camera itself. DirectShow and V4L2 captures are no longer asked a larger mode once frames have flowed (largest first, as 1.2.0 did). Tried on real hardware: a USB webcam and a virtual DirectShow camera, with three real access violations injected while a camera was streaming.

**New in 1.3.1:** the first-run wizard no longer vanishes at "looking for cameras". With Media Foundation, asking an open capture for another size after frames had flowed made OpenCV build the next frame over the old buffer - an access violation that ended the process on cameras whose own mode is e.g. 1920x1080. Every Media Foundation mode is now measured on a capture of its own, asked before its first frame (the way the driver opens it); the search takes up to a minute per camera. A native crash now leaves every thread's stack in `cokme.log` next to `gaze.log`.

**New in 1.3.0:** calibration that survives a change of posture (per-axis model chosen from the person's own data; an existing calibration is refitted once at the first start, the old file kept as a backup), a result screen after each calibration (grade, horizontal/vertical error, reach, hints; F9 x2 there starts a new one), camera modes measured up to 1920 wide with MJPG where it helps and no stretched modes, an aspect-independent closed-eye threshold learnt per person, time-based filters with a cursor sensitivity setting (1 = the 1.2.0 feel, the default for upgraded installs; 3 for new installs), and many more settings in the settings window. Upgraders: move "Imlec hassasiyeti" in Settings to make the cursor faster; for the new camera choice use Settings > Camera > "Look for cameras" > pick > Save, then F9 x2. Rolling back to 1.2.0 is supported (it falls back to its own backup calibration). macOS is not supported.

**Not tested yet:** a real Linux desktop and real Wayland session, the SmartScreen prompt on a real download, real switch hardware, and the GitHub Actions workflow; for 1.3.0, MJPG on real cameras, a real in-session camera mode switch and reading the physical screen size on real monitors.
