# Göz Faresi (gaze_driver) — Webcam Göz-Bakışı İmleç Sürücüsü

Standart bir webcam'den göz-bakışını kestirip **fare imlecini** (Windows ve Linux) süren, özgür (GPL-3.0-or-later) bir Python programı. Bu dosya **geliştiriciler** içindir; son kullanıcı için: `../packaging/BENI-OKU-KURULUM.txt` (kurulum) ve `../teslimatlar/KULLANIM-KILAVUZU.md` (günlük kullanım).

## Ne yapar / ne yapmaz

- ✅ Gözünüzle baktığınız yere imleci (kabaca) götürür.
- ✅ **Tıklar:** parmak düğmesi (klavye tuşu, switch arayüzü, XInput / genel joystick) ile sol/sağ/çift tık ve sürükleme — OptiKey gerekmez. "Gözle nişan al, parmakla ateşle" → dwell yorgunluğu olmaz. Parmak hiç yoksa `dwell_click` / `blink_click` açılabilir.
- ✅ **Konuşur ve yazar:** yerel sinir-ağı sesiyle seslendirme (F4), yerel Whisper diktesi (F3) ve tam ekran AAC cümle ızgarası (F5) — hiçbiri internet gerektirmez (modeller bir kez indirildikten sonra).
- ✅ Türkçe ve İngilizce arayüz (yeni dil = bir YAML dosyası, aşağıda).
- ❌ Klavye/kelime tahmini yapmaz; serbest yazı için OptiKey ya da işletim sisteminin ekran klavyesi (ScrLk×2) kullanılır.

## Yapı

| Yol | Ne |
|---|---|
| `gozfaresi.py` | Giriş noktası. Argümansız: **başlatıcı** (durum penceresi, ilk açılış sihirbazı, Ayarlar; sürücüyü alt süreç olarak çalıştırır ve çökerse yeniden başlatır). `--surucu [..]` sürücünün kendisi, `--sihirbaz` sihirbazla aç, `--ayarlar` Ayarlar açık. Paketli exe de buradan başlar. |
| `gaze_driver.py` | Sürücü (kamera → bakış → imleç, tuşlar, kalibrasyon, AAC, büyüteç…). |
| `eye_gaze/` | Modüller: `paths` (veri klasörleri), `platform/` (Windows, Linux X11, Linux evdev/Wayland), `ui/` (başlatıcı), `i18n`, `deep`/`deep_onnx` (derin model), `downloader`, `hardware` (makine.json), `config_writer`… |
| `resources/` | Pakete giren dosyalar: `lang/` (dil katalogları), `aac/` (Türkçe + İngilizce cümle paketleri), `fonts/`, `faces/`; derleme sırasında `models/` ve `piper/` doldurulur (git'te yok). |
| `config.default.yaml` | Yeni kurulumun ayar şablonu (anahtar ve değerleri `config.yaml` ile aynı tutulur, test edilir). |
| `linux/` | `izin-kur.sh` + udev kuralı (Linux giriş aygıtı izni, pkexec ile root çalışır; LF satır sonu şart). |
| `../packaging/` | Derleme: PyInstaller spec, Inno Setup, AppImage, kaynak zip, lisans toplayıcı (`hazirla.py`). |

## Kaynaktan çalıştırma

Python 3.11 gerekir.

```bash
# Windows (kaynak kurulum; paketin sabit sürümleri:
#          ../packaging/windows/requirements-win.txt)
pip install -r requirements.txt
# Linux (Docker test imajıyla aynı sürümler; sistem paketleri, ör. python3.11-tk,
#        libegl1, libgles2: ../packaging/linux/Dockerfile)
pip install -r ../packaging/linux/requirements-linux.txt

cd gaze
python gozfaresi.py
```

- **Kaynak zip'ten açılan klasörde** (`config.yaml`, `config.son-iyi*`, `makine.json` ve hiçbir `calibration*` dosyası yok) ilk açılışta `config.default.yaml` → `config.yaml` kopyalanır ve sihirbaz açılır. `config.yaml` her kurulumun kendi dosyasıdır: git'te izlenmez (`.gitignore`) ve zip'e girmez (`.gitattributes` `export-ignore`).
- **Bir git klonu da** aynı şekilde ilk açılış gibi başlar (`gaze/config.yaml` git'te yoktur).
- Kaynak kurulumda bunlardan **biri bile varsa** sihirbaz kendiliğinden açılmaz ve hiçbir şey yazmaz; `python gozfaresi.py --sihirbaz` onu salt-okunur gösterir.
- **`BASLAT.bat` paketlemeden önceki eski Windows kaynak kurulumu içindir:** `gaze_driver.py`'yi doğrudan çalıştırır, `gozfaresi.py`'yi bilmez ve `config.yaml` yoksa sürücü 2 koduyla çıkar. Kaynaktan kuranlar `python gozfaresi.py` ile başlatmalıdır. (`BASLAT.bat`, `KUR*.bat`, `OTOMATIK-BASLAT-KUR.bat` eski kurulum için olduğu gibi duruyor.)
- **Kaynak checkout'ta eksik olanlar** (paketlerde var):
  - MediaPipe yüz modeli (`face_landmarker.task`) ilk açılışta internetten iner.
  - Derin bakış modelinin ONNX'i (`resources/models/eth-xgaze-resnet18.onnx`) git'te yok. Ya `tools/derin_onnx_cevir.py` ile üretin (CPU torch + timm + onnx gerekir), ya Windows'ta `KUR-DERIN.bat` ile torch + ptgaze kurun. İkisi de yoksa sürücü geometrik modda çalışır ve bunu söyler.
  - Paketli Türkçe piper sesi (`resources/piper/`) derleme çıktısıdır. Windows kaynak kurulumunda `KUR-SES.bat` kurar; yoksa işletim sisteminin sesi konuşur.
- **Veri klasörü:** kaynak kipte her şey `gaze/` içinde kalır (eskisi gibi). Paketli: Windows `%LOCALAPPDATA%\GozFaresi`, taşınabilir zip'te exe'nin yanı, Linux `~/.config/gozfaresi` (+ modeller `~/.local/share/gozfaresi`). Windows'ta veri yolu ASCII değilse, yalnız dar-karakter C API'leriyle dosya açan parçalar (piper/espeak sesi, CTranslate2 Whisper modelleri) `C:\Users\Public\GozFaresi` altına gider (`paths.ascii_dir`). Kaldırıcı burayı silmez.
- **Geliştirme / deneme ortam değişkenleri:** `GOZFARESI_DATA_DIR` veri klasörünü değiştirir (testler ve smoke'lar gerçek kuruluma hiç dokunmaz); `GOZFARESI_FAKE_CAMERA=<resim>` kamera yerine sabit bir resim verir (ör. `resources/faces/benchmark.jpg`); GPU'suz deneme için `CUDA_VISIBLE_DEVICES=""`.
- Süreç bilerek **DPI-unaware**'dir (kalibrasyon mantıksal pikselde). Manifeste DPI-aware konmaz, `SetProcessDpiAwareness` çağrılmaz.

## Tuşlar (varsayılan)

Hepsi her pencerede çalışır; `F7`–`F10` **çift basış** ister:
`F12` 2 sn basılı acil alarm · `F11` konuşmayı sustur · `F10`×2 çıkış · `F9`×2 yeniden kalibrasyon · `F8`×2 duraklat · `F7`×2 hızlı düzeltme · `F6` büyüteçli tık · `F5` AAC ızgarası · `F4` seslendir · `F3` sesle yaz · `F2` tık menüsü · `ScrLk`×2 ekran klavyesi · `Space` sol tık.
Tümü Ayarlar penceresinden ya da `config.yaml`'dan değişir; boş `""` = kapalı. "Bir tuş tek iş yapar": aynı tuş iki işe atanırsa Ayarlar kaydetmez (`keymap.find_conflicts`).

## Ayarlar (`config.yaml`)

Kod değiştirmeden ayarlanır; Ayarlar penceresi dosyayı **yorumlarını koruyarak** yazar (`config_writer`). Önemliler:
- `camera_index`: `"auto"` (program bulur) ya da 0, 1, …
- `cursor_sensitivity`: imleç hassasiyeti `1`…`5` ya da `"auto"` (= 3). **Satır yoksa 1** (1.2.0'ın davranışı; yükseltilen kurulumlar). `mincutoff`, `beta`, `max_cursor_speed`, `deadzone`, `gaze_median_seconds` `"auto"` (ya da 1.2.0 fabrika değeri) kaldıkça seviyeye uyar; başka bir sayı o satır için geçerlidir (`eye_gaze/sensitivity.py`). Filtreler saniyeyle çalışır (`eye_gaze/timebase.py`); piksel değerleri ekranın fiziksel boyutu referanstan uzaksa ölçeklenir (`eye_gaze/physical.py`).
- `mincutoff`: **düşük = daha çok yumuşatma**; `beta`: **yüksek = hızlı harekette daha az gecikme**.
- `calibration_path`: `"rows"` (varsayılan) / `"grid"`; `camera_hfov_deg`: `"auto"` ya da derece (yalnız yeni kalibrasyonla geçerli); `blink_closed_threshold`: `"auto"` (kişiden öğrenilir; 0.18 = auto) ya da sayı.
- `deep_gaze`: `auto` (hız ölçümüne göre) / `true` / `false`. Değiştirmek yeniden kalibrasyon gerektirebilir.
- `language`: programın dili, katalog kodu (`tr`, `en`, …). **Satır yoksa Türkçe.** `config.default.yaml`'da bilerek yok (şablon `config.yaml`'dan türetilir); sihirbaz ya da Ayarlar seçilince ekler.

## Diller

- Kataloglar: `resources/lang/<kod>.yaml` (iç içe YAML, anahtar = noktalı yol). `_` ile başlayan üst anahtarlar dil bilgisidir: `_name` (seçicide kendi adı), `_decimal` (`,` / `.`), `_defaults` (o dilin sesi `tts_voice`, dikte dili `stt_language`, AAC kökü `aac_root`).
- Arama sırası: seçilen dil → İngilizce → Türkçe → anahtarın kendisi. Eksik metin bir kez `[dil] …` satırıyla günlüğe yazılır; hiçbir şey çökmez.
- **Yeni dil eklemek:** `resources/lang/<kod>.yaml` ekleyin (`en.yaml`'ı kopyalayıp çevirin). İsteğe bağlı: `resources/aac/` altına o dilin AAC paketi ve `_defaults.aac_root`; doğal piper sesi için `eye_gaze/models_manifest.py` içindeki `VOICE_PACKS`'e bir indirme girdisi. Ses yoksa işletim sisteminin o dildeki sesi kullanılır; Türkçe dışındaki dillerde yanlış dilde bir ses seçilmez (o dilde hiç ses yoksa seslendirme kapalı kalır, alarmda yalnız siren çalar; açılışta söylenir).
- Bu turda çevrilmeyenler: `config.yaml` yorumları (Türkçe kalır), konsol / `gaze.log` satırlarının çoğu (ASCII Türkçe).
- Dil seçimi: sihirbazın ilk adımının üst bölümü, Ayarlar → "Dil (Language)" sekmesi. İngilizce piper sesi (`en_US-ljspeech-medium`) sihirbazın İndirmeler adımında iner.
- **İngilizce metinler makine yardımıyla çevrildi:** `en.yaml` ve İngilizce AAC cümleleri anadili İngilizce olan biri tarafından denetlenmedi; düzeltmeler memnuniyetle alınır. Denetlenmiş dil Türkçedir. *(English: machine-assisted translations, not checked by a native speaker; corrections welcome. Turkish is the reviewed language.)*

## Testler

```bash
# Windows (gaze/ içinde; GPU'ya dokunmaz)
CUDA_VISIBLE_DEVICES="" python -m pytest -q -p no:cacheprovider

# Linux: Docker ubuntu:22.04 + Xvfb, GPU'suz, ağsız (Git Bash'ten de çalışır)
bash ../packaging/linux/test.sh            # ek argümanlar pytest'e gider: -k linux_x11
```

`test.sh` kaynağı konteynere **tar akışıyla** verir (bind-mount yok: Docker Desktop ASCII olmayan Windows yollarını güvenilir çeviremiyor). Platforma özel testler diğer işletim sisteminde atlanır. Kamera, gerçek klavye ve GPU testlerde kullanılmaz; MediaPipe/kamera kısmı paket smoke'larında sahte kamerayla, gerçek kullanımda gerçek webcam'le doğrulanır.

## Derleme (paketler)

Sürüm numarası: `eye_gaze/__init__.py` → `__version__`. Çıktılar `../dist/surum-<sürüm>/` altına gider (git'e girmez); her betik `SHA256SUMS`'u yeniden yazar.

| Ne | Komut | Not |
|---|---|---|
| Windows kurulum + taşınabilir zip | `powershell -NoProfile -ExecutionPolicy Bypass -File packaging\windows\derle.ps1 [-VenvKoru] [-InnoKur]` | Temiz bir venv `packaging/out/venv-win` (sistem Python'a hiçbir şey kurulmaz); ONNX derin model yoksa ayrı bir CPU-torch venv'de çevrilir. Inno Setup yoksa yalnız zip çıkar (yüksek sesle söylenir); `-InnoKur` onu winget ile kullanıcıya kurar. |
| Linux AppImage + tar.gz | `bash packaging/linux/derle.sh` | Docker imajında derler. `GOZFARESI_DOCKERSIZ=1` Docker'sız (CI). appimagetool ve type2 runtime SHA ile sabitlenmiş. |
| Kaynak zip | `bash packaging/kaynak.sh` | `git archive HEAD`. Yerel `gaze/config.yaml` **girmez** (`.gitattributes` `export-ignore`); kalibrasyon, `.npz`, model, günlük, `makine.json` girerse betik durur. |
| CI | `.github/workflows/paketle.yml` | `v*` etiketinde iki işletim sisteminde derler. **Yazıldı, hiç çalıştırılmadı.** |

- **Paket denetimi** (`python packaging/hazirla.py denetle <onedir>`, iki betik de çalıştırır): pakette derleyen bilgisayara ait hiçbir şey (kalibrasyon, `.npz`, `gaze.log`, `makine.json`, `config.yaml`, `cache/`) olmamalı; gereken her dosya (modeller, ses, kataloglar, `config.default.yaml`, `lisanslar/`) olmalı.
- **Lisanslar:** `hazirla.py lisanslar` paketin yanına `lisanslar/` klasörünü (her bağımlılığın tam lisans metni) koyar; lisans dosyası bulunamayan yeni bir bağımlılık derlemeyi durdurur. `hazirla.py gpl-kaynak` piper-tts ve espeak-ng'nin kaynak arşivlerini (SHA ile sabit) paketlerin yanına koyar.
- Ölçülen boyutlar (1.2.0): Kurulum.exe 241 MB, taşınabilir zip 306 MB, AppImage 571 MB, tar.gz 617 MB, kaynak zip 1,7 MB. Sınırlar: Windows < 600 MB, AppImage < 700 MB.
- Derleme ve smoke'lar GPU kullanmaz (`CUDA_VISIBLE_DEVICES` boş / `-1`).
- **Derin model sabit:** `hazirla.py onnx-denetle` ONNX'i yalnız kendi `.sha256` dosyasıyla değil, `ONNX_SHA` (bugüne kadar gönderilen, torch modeliyle doğrulanmış model, `add49768…`) ile de karşılaştırır. Model Hugging Face `hysts/ptgaze-eth-xgaze-resnet18` revizyon `5b6aebd0…` ağırlıklarından (sha256 `d1c91b2a…`) torch 2.5.1 ile çevrildi; başka ağırlıklardan ya da başka bir torch ile çıkan bir model derlemeyi durdurur (derin kalibrasyonlar bu modelin çıktısına göre yapılmıştır).

## OptiKey'e bağlama

OptiKey → Management Console (ALT+M) → **Pointing & Selecting** → `Source = "Mouse Position"`. Böylece OptiKey, bu sürücünün sürdüğü imleci okur; seçim parmak düğmesiyle yapılır. Ayrıntı: `../teslimatlar/faz1/01-optikey-kurulum-ayar-rehberi.md`.

## Beklenti uyarısı (önemli)

Webcam göz-bakışının doğruluğu **~2.5° ile sınırlıdır** ve **piksel-hassas değildir** (kızılötesi Tobii ~1.1°). Bu, OptiKey'in büyük tuşları için yeterli; küçük hedefler için **F6 büyüteç + parmak düğmesi** ya da hedefe yapışma kullanın. Doğruluk; **ışık, gözlük yansıması, göz-kamera mesafesi ve pitozis (göz kapağı düşüklüğü)** ile düşer — `../teslimatlar/faz1/04-ergonomi-isik-webcam-kontrol-listesi.md`'ye uyun. Bu araç **deneyseldir**.

## Sorun giderme

- **Kamera açılmıyor**: başka bir program kamerayı tutuyor olabilir; `camera_index`'i `"auto"` ya da 1, 2 deneyin (Ayarlar → Kamera → "Kameraları ara").
- **İmleç kötü takip ediyor**: ışığı önden/sabit yapın, `F9`×2 ile yeniden kalibre edin, mesafeyi sabitleyin.
- **İmleç titriyor / geç kalıyor**: Ayarlar → İmleç → İmleç hassasiyeti (titrekse düşürün, geçse artırın). İnce ayar: `mincutoff`'u düşürmek daha sakin, `beta`'yı artırmak daha az gecikme.
- **Yüz bulunamıyor**: kamera yüzü net/önden görsün; arkadan ışık olmasın.
- **Ne olduğunu görmek**: `gaze.log` (veri klasöründe; başlatıcıda "Günlüğü aç").
- **Linux**: Xorg oturumu önerilir; Wayland'de "İzinleri kur" (evdev/uinput) şarttır, Xorg'da da önerilir (izinsiz X11 yolu XGrabKey yedeğidir). AppImage açılışta eksik sistem kitaplıklarını (libEGL, libGLESv2, libxcb-*) apt paket adlarıyla söyler; ör. WSLg'deki Ubuntu 24.04'te libGLESv2 varsayılan olarak yoktur.

## Güvenlik notları

- **Tek örnek soketi:** başlatıcı `127.0.0.1` üzerinde bir port dinler; ikinci bir başlatıcı oraya `goster` / `ayarlar` / `sihirbaz` yazar. Kimlik denetimi yoktur: aynı bilgisayardaki **her yerel kullanıcı** (ya da programı) `sihirbaz` gönderebilir, sihirbaz açıkken sürücü (imleç ve acil alarm) duraklar, sihirbaz kapanınca döner. Paylaşılan bir bilgisayarda yerel bir hizmet engelleme yoludur.
- **`C:\Users\Public\GozFaresi`:** her yerel kullanıcı yazabilir. Oraya inen Whisper model dosyaları durum denetiminde yalnız **boyutlarıyla** doğrulanır (indirme sırasında SHA-256 denetlenir, sonradan değil); başka bir kullanıcı aynı boyutta bir dosya koyarsa program onu kullanır.
- **Linux `input` grubu:** "İzinleri kur" kullanıcıyı `input` grubuna ekler; o hesapta çalışan her program bütün klavyeleri (parolalar dahil) okuyabilir.

## Lisans

```
Göz Faresi - webcam ile göz-bakışı fare sürücüsü
Copyright (C) 2026 Göz Faresi (eye-track) projesi

This program is free software: you can redistribute it and/or modify it
under the terms of the GNU General Public License as published by the Free
Software Foundation, either version 3 of the License, or (at your option)
any later version.

This program is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
FITNESS FOR A PARTICULAR PURPOSE. See the GNU General Public License for
more details.

You should have received a copy of the GNU General Public License along
with this program. If not, see <https://www.gnu.org/licenses/>.
```

Tam metin: `LICENSE` (bu klasörde ve deponun kökünde; paketlerde `LICENSE.txt`).

**Ek izin / Additional permission** (GPL-3.0 madde 7; GPL metninin kendisine eklenmedi):

> As an additional permission under section 7 of the GNU GPL version 3, the copyright holders of Göz Faresi give you permission to combine Göz Faresi with, and convey the result together with, the following libraries that are not GPL-compatible, in unmodified form: Intel oneMKL and Intel OpenMP runtime (as used by CTranslate2), OpenSSL 1.1.x, NVIDIA CUDA/cuDNN/cuBLAS runtime libraries. The corresponding source of Göz Faresi itself must still be provided under the GPL. This permission does not extend to other GPL-licensed works distributed with Göz Faresi (such as piper-tts and espeak-ng), which run as a separate program.

Türkçe açıklama: Göz Faresi'nin telif sahipleri, GPL 3 madde 7'nin izin verdiği bir EK İZİN veriyor. Göz Faresi, GPL ile uyumlu olmayan şu kitaplıklarla, değiştirilmemiş hâlleriyle, birlikte çalıştırılabilir ve birlikte dağıtılabilir: Intel oneMKL ve Intel OpenMP (dikte motoru CTranslate2'nin içinde), OpenSSL 1.1.x, NVIDIA CUDA/cuDNN/cuBLAS. Göz Faresi'nin kendi kaynak kodu yine GPL ile verilmek zorundadır. Bu izin Göz Faresi'yle birlikte dağıtılan başka GPL yazılımlarını (piper-tts, espeak-ng) kapsamaz; onlar paketlerde ayrı bir program olarak (GozFaresi-Ses / gozfaresi-ses) çalışır ve yukarıdaki kitaplıkları hiç yüklemez.

Paketlerde piper-tts ve espeak-ng ayrı bir programda (`GozFaresi-Ses.exe` / `gozfaresi-ses`, kaynağı `gozfaresi_ses.py`) çalışır; ana süreç onları hiç yüklemez, ses süreci de oneMKL / OpenSSL / OpenCV / CTranslate2'yi yüklemez (`eye_gaze/ses_isci.py`, `hazirla.py denetle`). Kaynak kurulumda piper eskisi gibi programın içinde çalışır.

Paketlerdeki Türkçe ses (`tr_TR-dfki-medium`) ve derin bakış modeli (ETH-XGaze ile eğitilmiş) **ticari olmayan** lisanslıdır (CC BY-NC-SA 4.0): paket bu hâliyle satılamaz ve ticari amaçla kullanılamaz. Ayrıntı: `../packaging/UCUNCU-TARAF-LISANSLAR.txt`.

**Türkçe özet:** Göz Faresi'ni ücretsiz kullanabilir, paylaşabilir ve değiştirebilirsiniz. Değiştirilmiş bir sürümü başkalarına dağıtan, onun **tam kaynak kodunu aynı lisansla** yayımlamak zorundadır; kapalı kaynaklı (tescilli) bir sürüm yapılamaz. GPL satışı yasaklamaz, ama satan da kaynağı aynı özgürlüklerle vermek zorundadır. Program **hiçbir garanti olmadan** verilir.
*English: free to use, share and modify; anyone distributing a modified version must publish its full source under the same licence (GPL-3.0-or-later); no warranty.*

**Üçüncü taraf parçalar** (özet; tam liste `../packaging/UCUNCU-TARAF-LISANSLAR.txt`):
- `piper-tts` ve içindeki `espeak-ng` GPL-3.0-or-later'dır. Paketteki her GPL / LGPL / MPL parçanın (piper, espeak-ng, PyInstaller önyükleyicisi, certifi, tqdm; Linux'ta ayrıca python-xlib, OpenCV'nin FFmpeg'i ve Qt 5'i, libquadmath, Ubuntu'nun GLib / JACK / ALSA kitaplıkları) **tam kaynağı** paketlerle birlikte ayrı dosyalar olarak yayımlanır; liste ve SHA-256 değerleri `KAYNAKLAR-<OS>.txt` (`hazirla.py gpl-kaynak`). PyAV, OpenCV'nin Windows FFmpeg eklentisi, DirectML ve PortAudio'nun ASIO derlemesi pakete hiç konmaz (`gozfaresi.spec`; `hazirla.py denetle` bunları görürse derlemeyi durdurur).
- Pakete gömülü Türkçe piper sesi `tr_TR-dfki-medium` ve derin modelin eğitim verisi ETH-XGaze **CC BY-NC-SA 4.0**'dır: **ticari olmayan** kullanım, kaynak gösterme, aynı lisansla paylaşma. Bu kısıt programın koduna değil, o ses ve veriye aittir. **Açık soru:** Türkçe ses, model kartına göre `lessac` sesinden ince ayarla türetilmiştir; o sesin Blizzard 2013 Lessac veri şartları (https://www.cstr.ed.ac.uk/projects/blizzard/2013/lessac_blizzard2013/license.html) doğrulanamadı. Ses bilerek tutuluyor (en iyi açık Türkçe ses); ticari olmayan kullanımı projenin amacıyla uyumlu.
- İngilizce ses `en_US-ljspeech-medium` (isteğe bağlı indirme): LJ Speech verisi, kamu malı; ses sıfırdan eğitilmiştir (model kartı).
- Kaynak kurulumdaki isteğe bağlı XTTS v2 modeli Coqui Public Model License altındadır (ticari olmayan). OpenAI Whisper ve faster-whisper modelleri MIT'tir.

---
*Bu yazılım deneyseldir ve bilgilendirme amaçlıdır; klinik karar için uzmana danışın.*
