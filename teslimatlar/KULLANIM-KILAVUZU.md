# 📋 Kullanım Kılavuzu — Günlük Kullanım (Hızlı Başvuru)

"Gözle nişan al, parmakla ateşle." Göz-sürücüsü **yalnızca imleci sürer**; tıklama parmak düğmesiyle, yazı/konuşma OptiKey ile yapılır.

> Bu kılavuz iki kullanım için geçerli: **(A) paketten kurulan program** (Windows kurulum dosyası, taşınabilir zip, Linux AppImage / tar.gz — aşağıda 0. bölüm) ve **(B) kaynak koddan kurulum** (`python gozfaresi.py`; eski Windows kaynak kurulumunda `gaze\BASLAT.bat`). Tuşlar ikisinde de aynıdır. Kılavuzda geçen `config.yaml`, paketli programda **Ayarlar penceresinden** değiştirilir (elle düzenlemek isterseniz dosya ayarlar klasöründedir, aşağıya bakın).

---

## 0. Paketten kurulum (yeni bir bilgisayara, bir kez)

Ayrıntılı, adım adım anlatım paketin içindeki **`BENI-OKU-KURULUM.txt`** dosyasındadır. Kısaca:

**Windows (kurulum dosyası, önerilen)**
1. `GozFaresi-…-Windows-Kurulum.exe` → çift tıkla. Yönetici şifresi istemez.
2. Mavi "Windows bilgisayarınızı korudu" penceresi çıkarsa: **Ek bilgi** → **Yine de çalıştır** (program imzasız olduğu için çıkar, sorun değildir).
3. Kurulum bitince program açılır ve **ilk açılış sihirbazı** başlar.

**Windows (taşınabilir, USB bellek):** zip'i "Tümünü ayıkla" ile açın, `GozFaresi\GozFaresi.exe` → çift tıkla. Ayarlar programın kendi klasöründe durur; oradaki `tasinabilir` dosyasını silmeyin.

**Linux**
1. AppImage: dosyaya sağ tık → Özellikler → İzinler → **"Programın çalışmasına izin ver"** → çift tıkla.
   Çift tıklayınca açılmazsa (GNOME'da olur) o klasörde terminal açıp: `chmod +x GozFaresi-*.AppImage` ve `./GozFaresi-*.AppImage --appimage-extract-and-run`
2. tar.gz: arşivi açın, `gozfaresi/gozfaresi` → çift tıkla (açılmazsa terminalde `./gozfaresi`).
3. Oturum açarken **"Xorg" / "Ubuntu on Xorg"** seçmeniz önerilir (Wayland'de büyüteç ve bakıp-bekleme halkası çalışmaz).
4. Sihirbazın ilk adımındaki **"İzinleri kur"** düğmesine basın (parola bir kez sorulur, sonra oturumu kapatıp açın). Wayland'de şarttır; Xorg'da da önerilir.

**İlk açılış sihirbazı** sırayla sorar: dil (Türkçe / English) → kamera → bilgisayarın hızı (derin model ve dikte modeli buna göre seçilir) → indirmeler (internet yoksa atlanır, program onlarsız da çalışır) → tuşlar / düğmeler ("bir tuş tek iş yapar") → kalibrasyon. Her adım **Atla** ile geçilebilir. Sonradan her şey **Ayarlar** penceresinden değişir (Başlat menüsünde "Göz Faresi - Ayarlar").

**Ayarlar ve kalibrasyon nerede?**

| Kullanım | Klasör |
|---|---|
| Windows kurulum | `%LOCALAPPDATA%\GozFaresi` (Türkçe harfli kullanıcı adında ses kopyası ve dikte modelleri ayrıca `C:\Users\Public\GozFaresi`) |
| Windows taşınabilir | programın kendi klasörü |
| Linux | ayarlar `~/.config/gozfaresi`, indirilen modeller `~/.local/share/gozfaresi` |
| Kaynak kurulum | `gaze\` |

**Dil:** sihirbazın ilk adımı ya da Ayarlar → **Dil (Language)** sekmesi → Kaydet. `config.yaml`'daki karşılığı `language: "en"` (satır yoksa Türkçe). İngilizce doğal ses (`en_US-ljspeech-medium`, kamu malı LJ Speech verisiyle eğitilmiş) sihirbazın **İndirmeler** adımından iner; o zamana kadar bilgisayarın kendi İngilizce sesi konuşur.

**İngilizce metinler hakkında:** İngilizce ekran metinleri ve İngilizce AAC cümleleri makine yardımıyla çevrilmiştir; anadili İngilizce olan biri tarafından denetlenmedi. Düzeltmeler memnuniyetle alınır. Denetlenmiş dil Türkçedir. *(English: the English texts and AAC phrases are machine-assisted translations, not checked by a native speaker; corrections are welcome. Turkish is the reviewed language.)*

**Kaldırma (Windows):** Ayarlar → Uygulamalar → Göz Faresi → Kaldır. Kalibrasyon **korunur**; silinsin isterseniz çıkan "Ayarları ve kalibrasyonu da sil" kutusunu işaretleyin. `C:\Users\Public\GozFaresi` (varsa) elle silinir.

**Sorun olursa:** durum penceresindeki **"Günlüğü aç"** düğmesi `gaze.log`'u açar; yardım isterken onu gönderin.

**Kaynak koddan (geliştirici / kaynak zip):** `gaze` klasöründe `python gozfaresi.py` ile başlatın. İlk açılışta `config.default.yaml` kopyalanır ve sihirbaz açılır. `BASLAT.bat` yalnız paketlemeden önceki eski Windows kaynak kurulumu içindir: kaynak zip'te `config.yaml` olmadığından onunla açılmaz.

---

## 1. Her açılışta (~1 dakika)
1. Webcam yüzü **önden** görsün; ışık **önden/yumuşak** olsun (gözlüğe yansımasın).
2. `gaze\BASLAT.bat` → **çift tıkla**. (Paketten kurduysanız: masaüstündeki / Başlat menüsündeki **Göz Faresi** kısayolu. Linux'ta AppImage ya da `gozfaresi`.)
3. **Kalibrasyon:** ekranda **süzülen yeşil TOPU gözünle takip et** (~25 sn × 2 tur; turlar arasında "pozisyonunu hafifçe değiştir" der — yaslanma telafisi böyle öğrenilir). Klasik sabit-nokta modu istersen: `config.yaml` → `calibration_mode: "dots"`.
4. Bitince **imleç gözünle gelir**.

> 💾 Kalibrasyon **kaydedilir**; sonraki açılışlarda **otomatik yüklenir** (her seferinde tekrar kalibre etmen gerekmez). Takip bozulursa **`F8` ile duraklatıp** ya da **`F9` ×2 ile yeniden kalibre** et.

> 🖱️ **Tıklama artık sürücünün kendinde:** parmağınla **Space** tuşuna (veya gamepad **A**) bas = sol tık. Yani tarayıcı/oyun/Windows'ta **OptiKey olmadan** tıklayabilirsin. Tıklama tuşunu `config.yaml` → `left_click_key`'ten değiştirebilirsin.

---

## 2. ⌨️ Tuş / klavye kontrolleri (kullanım sırasında)

| Tuş / girdi | Ne yapar | Nerede |
|---|---|---|
| **`F12` 2 sn BASILI TUT** | 🚨 **ACİL ALARM**: yüksek siren + "Yardım edin!" — duraklatılmışken, yüz görünmezken ve kalibrasyonda da çalışır. Durdurmak için yine 2 sn basılı tut (120 sn sonra kendi durur) | Her yerde |
| **`F11`** | **Konuşmayı sustur**; **× 2** = son cümleyi **tekrar söyle** | Her pencerede |
| **`F2`** | **Tık menüsü**: büyük düğmeler — Sağ tık, Çift tık, Sürükle, Kaydır yukarı/aşağı, **Kalibrasyonu geri al**, İptal. Seçeneğe **BAK + Space**; işlem menünün açıldığı noktaya yapılır. Tekrar `F2` = kapat | Her pencerede |
| **`F10` × 2** | Göz-sürücüsünü **kapat** | Her pencerede (global) |
| **`F9` × 2** | **Yeniden kalibre et**. Kalibrasyon sırasında **tekrar `F9` × 2 = vazgeç** (önceki kalibrasyon korunur) | Her pencerede |
| **`F8` × 2** | **Duraklat / Devam** (imleci dondur — okurken/dinlenirken). Duraklatılmışken F9 kalibrasyon başlatmaz (önce F8×2 ile devam et) | Her pencerede |
| **`F7` × 2** | **Hızlı düzeltme**: ortadaki tek noktaya 2 sn bak → isabet düzelir (kayma olursa; tam kalibrasyon gerekmez) | Her pencerede |
| **`F6`** | **Büyüteçli tık**: baktığın yerin çevresi (ekranın %40'ı) 2,5× büyür → hedefe bak → Space = hassas tık (küçük düğme/link için) | Her pencerede |
| **`F5`** | **AAC cümle ızgarası**: tam ekran kutular; cümleye **BAK + Space** = konuşur. Kategori → cümle. Kutudan kutuya bakmak süreyi yeniler; son 5 sn geri sayım görünür; tekrar `F5` = kapat. Göz takibi zayıflarsa **tarama modu** (`aac_scan: true`): satırlar/kutular sırayla yanar, Space seçer — bakmak gerekmez | Her pencerede |
| **`F4`** | **Seslendir**: seçili metni **yerel Türkçe sesle** okur — önce metni gözle sürükleyip seç, sonra F4 | Her pencerede |
| **`F3`** | **Sesle yazma**: basın, konuşun, **susun** — kayıt kendi biter. Yazı odaktaki kutuya girer ve sesli geri okunur | Her pencerede |
| **`Scroll Lock` × 2** | **Windows ekran klavyesi** açılır (OptiKey'siz yazı). Ekran klavyesinin Space'i boşluk yazar (sürücünün tıklaması sayılmaz) | Her pencerede |
| **`Space`** veya gamepad **A** | **SOL TIK** | Sürücü artık **kendi tıklar** — OptiKey gerekmez |
| gamepad **B / Y / X** | **Sağ tık / çift tık / sürükle** | `config.yaml`'dan klavye tuşu da atayabilirsin |
| **OptiKey ekran klavyesi** | **Yazı + Türkçe konuşma** | Sadece yazı/iletişim için gerekir |

> ⚠️ Göz-sürücüsü **yazı yazmaz**, sadece imleci sürer. Yazı ve Türkçe konuşma **OptiKey'in** işidir. Fiziksel klavyenin herhangi bir tuşu, OptiKey'de **tıklama tuşu** olarak atanabilir.
>
> 🔇 Sürücünün tuşları (Space, F2–F12, Scroll Lock) **öndeki programa gitmez**: Space boşluk yazmaz, F5 tarayıcıyı yenilemez. **Bakım veren klavyeyle yazacaksa önce `F8`×2 ile duraklatsın** — duraklatınca klavye normale döner (F12 alarmı yine çalışır).
>
> 🔔 Sürücü kısa, kısık **uyarı sesleri** çalar: tık, kayıt başladı/bitti, duraklat/devam, kalibrasyon bitti, kamera koptu. Kapatmak: `feedback_sounds: false`.
>
> 🔑 Çıkış/kalibrasyon tuşları **her pencerede çalışır** (sürücü penceresi seçili olmasa da) ve **çift basış** ister — oyun/yazı sırasında tek basış kazara tetiklemesin diye. Tuşları `config.yaml`'dan değiştirebilirsin (`quit_key`, `recalibrate_key`; ör. `q`/`c`).

---

## 3. Ne neyle yapılır

| İş | Nasıl |
|---|---|
| İmleci hareket ettir | **Gözlerinle** (kaba nişan) |
| Düğme / bağlantı / klasör / sekmeye nişan al | **Kendiliğinden:** imleç baktığın yere en yakın tıklanabilir öğenin **üstüne oturur** (hedefe yapışma, `snap_to_targets: true`). Hedefe *yakın* bakman yeter; Space ona tıklar. Komşu öğeye geçmek için gözünü onun **içine** götür |
| Tıkla / seç | **Parmak düğmesi** (önerilen). Parmak yoksa: `config.yaml` → `dwell_click: true` (**bakarak bekle-tıkla**, 1 sn sabit bak = sol tık) veya `blink_click: true` (uzun göz kırpma = sol tık) |
| Küçük hedefe (ufak buton/sekme) bas | **`F6` büyüteç**: bak → F6 → hedefe bak → Space. Her büyüteç tıkı imlecin o bölgedeki hatasını da sessizce öğretir (isabet zamanla düzelir) |
| Sağ tık / çift tık / sürükle / kaydır (tek switch) | **`F2` tık menüsü** |
| Yardım çağır | **`F12`'yi 2 sn basılı tut** (tek switch kullanıyorsa: `alarm_switch_hold: 3` → switch 3 sn basılı = alarm) |
| Yazı + Türkçe konuşma | **OptiKey** klavye + hazır AAC cümle paketi |
| Dosya Gezgini'nde gezin | Görünüm → **Çok büyük simgeler** (hedefler büyür). Klasöre bak → **Space** seçer; açmak için **F2 → Çift tık** veya OptiKey'de **Enter**. Geri: sol üstteki ok (yapışma onu bulur) |
| Chrome'da yaz | Sayfayı **Ctrl +** ile %150-200 büyüt. Yazı alanına bak → Space → **OptiKey** ile yaz (kelime tahmini) ya da **F3** ile söyle |

---

## 4. Takip kötüyse — hızlı düzeltme (sırayla dene)
1. **Önce `F7`×2 dene** (2 saniyelik tek-nokta düzeltme — gün içinde kayma olduysa çoğu zaman yeter). Yetmezse **`F9`×2** ile tam kalibrasyon (konsolda "Kalibrasyon ortalama hatası: … px" kalite notu yazar; KOTU derse tekrar yap).
   - **Yeni kalibrasyon eskisinden kötü çıktıysa:** `F2` → **Kalibrasyonu geri al** → önceki kalibrasyona dönülür. Tekrar seçersen yenisine geri gelir (yanlışlıkla seçmek zarar vermez). KÖTÜ notlu bir kalibrasyon çalışan kalibrasyonun üstüne zaten yazılmaz; en iyi kalibrasyonun kopyası da (`calibration.json.11.en-iyi`) hiç silinmez.
   - Windows **ölçek/çözünürlüğü değişirse** yeniden kalibrasyon gerekmez: kalibrasyon yeni ekrana otomatik ölçeklenir.
2. **Kenarlara/köşelere ulaşamıyorsan:** yeniden kalibre et (noktalar artık kenarlara yakın); yetmezse `config.yaml` → `reach_margin: 0.35`.
3. Işığı **önden/sabit** yap; gözlüğe yansıma olmasın; ekran mesafeni sabitle.
4. Hâlâ **titrek** → `config.yaml`: `gaze_median_window: 9`, `mincutoff: 0.15`.
5. İmleç hâlâ **çok hızlı** → `max_cursor_speed: 800` (imleç bakılan yere kayar, ışınlanmaz). Çok **yavaş/geç** kalıyorsa → `max_cursor_speed: 2000` veya `0`, `mincutoff: 0.4`.
6. Baş-pozu beklenmedik kötüleştirirse → `config.yaml`: `use_head_pose: false`.
7. **Dwell kazara tıklıyorsa** → `dwell_seconds: 1.5` ve/veya `dwell_radius: 30`; titreklikten hiç dolmuyorsa `dwell_radius: 60`.
8. **Hedefe yapışma yanlış öğeye gidiyorsa** → `snap_radius: 80`; hedefe hiç ulaşmıyorsa `snap_radius: 160`; hiç istemiyorsan `snap_to_targets: false`. F6 büyüteçte hedef görüntünün dışında kalıyorsa → `zoom_scale: 0.5`.
9. **İkinci monitör** kullanıyorsan → `multi_monitor: true` (kalibrasyon ana ekranda yapılır; kenardan öteye bakınca imleç diğer ekrana taşar; F6 büyüteç imlecin bulunduğu monitörü büyütür).

> 👁️ Bakarak tıklama (`dwell_click: true`) açıkken imlecin çevresinde **dolan bir halka** görünür; dolunca tıklar, yeşil yanıp söner.
>
> 🛡️ Sürücü kendini kapatmaz: kamera koparsa kendisi yeniden bağlanır; çökerse `BASLAT.bat` 5 sn sonra yeniden açar. Bilgisayar açılınca kendiliğinden başlasın istiyorsan **bir kez** `gaze\OTOMATIK-BASLAT-KUR.bat` çalıştır (geri almak: `OTOMATIK-BASLAT-KUR.bat KALDIR`). **Paketli programda:** sürücü çökerse başlatıcı onu birkaç saniye sonra kendisi yeniden açar; otomatik başlatma Ayarlar → Gelişmiş → **"Bilgisayar açılınca başlat"**.
>
> ⏸️ Duraklatınca kamera penceresinde **kırmızı çerçeve + "DURAKLATILDI"** yazısı görünür — imleç neden gitmiyor diye şüphede kalma.

## 4b. Daha da iyi isabet: derin bakış modeli (NVIDIA GPU'lu bilgisayarlar)
> **Paketli programda** bu adımlar gerekmez: derin model pakette hazırdır (torch'suz ONNX sürümü); sihirbaz bilgisayarın hızını ölçüp açar ya da kapatır (`deep_gaze: auto`). Değiştirmek: Ayarlar → Gelişmiş → Derin model (değiştirince yeniden kalibrasyon gerekebilir). Aşağıdaki adımlar **kaynak kurulum** içindir.

1. `gaze\KUR-DERIN.bat` çift tıkla (bir kez; ~3 GB indirme).
2. `config.yaml` → `deep_gaze: true` yap, kaydet.
3. `BASLAT.bat` → **F9×2 ile yeniden kalibre et** (şart — özellik sayısı değişiyor).
- ~1 GB VRAM kullanır; 8 GB kartlarda rahat çalışır. GPU başka işle meşgulken `deep_gaze: false` yap.
- GPU'suz makinede `deep_gaze: auto` bırak: model kendiliğinden devre dışı kalır, hiçbir şey bozulmaz.

> `config.yaml`'ı düzenle → **kaydet** → `BASLAT.bat`'ı yeniden çalıştır.

---

## 5. Gözler yorulursa / pitozis (göz kapağı düşüklüğü)
Göz takibi yetmediğinde **OptiKey switch-tarama** profiline geç → **aynı parmak düğmesiyle, gözsüz** tam erişim.
Rehber: `faz1/03-switch-tarama-yedek-profili.md`

---

## 6. İlk kez tam sistem kurulumu (bir kez)
OptiKey kurulumu + AAC paketi + parmak düğmesi: `00-TUMUNU-DENE-birlesik-kurulum.md`
Göz-sürücüsü ayrıntıları: `../gaze/README.md`

---

## 6b. Ses ve dikte ayarları (`config.yaml`)

| Sorun | Ayar |
|---|---|
| **Konuşma hızlı / anlaşılmıyor** | `tts_speed: 1.4` (büyük = yavaş). Değiştirince AAC cümleleri ilk açılışta kendiliğinden yeniden seslendirilir (~10 sn, arka planda) |
| Sibilant sesler (`s`, `ş`) cızırdıyor | Bluetooth kulaklıkta olur: `tts_volume: 0.9` deneyin, ya da kablolu kulaklık |
| Ses robotik geliyor | `tts_engine: piper` olduğundan emin olun (`tts_voice: tr_TR-dfki-medium` — Piper'ın **tek** Türkçe sesi). Daha doğal isteniyorsa `tts_engine: xtts` (ağır, GPU) |
| "TTS motoru bulunamadi" yazıyor | Kaynak kurulumda: `gaze/KUR-SES.bat` çalıştırılmamış ya da `C:\Users\Public\piper-voices\espeak-ng-data` silinmiş; betiği yeniden çalıştırın. Paketli programda Türkçe ses pakettedir: `gaze.log`'a bakın, gerekirse programı yeniden kurun |
| Eski Windows sesi geri gelsin | `tts_engine: onecore` (Microsoft Tolga) |
| Dikte cümleyi erken kesiyor | `stt_silence_seconds: 2.5` |
| Dikte yavaş | `stt_model: small` (daha hızlı, biraz daha az doğru) |
| İlk F3 uzun sürüyor | Normal: model ilk kullanımda yüklenir (~10-30 sn), sonrakiler ~1 sn |
| GPU başka işle meşgul | `stt_device: cpu` |
| Geri okuma rahatsız ediyor | `stt_readback: false` |
| Eski Windows diktesi (Win+H) geri gelsin | `stt_engine: windows` |
| AAC ızgarası çok çabuk kapanıyor | `aac_timeout: 40` |
| AAC ızgarası açılmıyor | Konsolda "cumle dosyasi bulunamadi" yazıyorsa `aac_dir` yolunu kontrol edin |
| AAC cümlesi gecikmeli konuşuyor | `aac_cache: true` olmalı; ilk açılışta cümleler arka planda seslendirilir (~5 sn) |

> **Pano uyarısı:** F3 ile yazılan metin **pano üzerinden** yapıştırılır (Türkçe
> karakterler klavye düzeninden bağımsız gelsin diye). O sırada panodaki eski
> içerik değişir.

> **Ayar dosyası bozulursa:** program kapanmaz — bir önceki **çalışan** ayarlarla
> açılır (`config.son-iyi.yaml`) ve kamera penceresinde kırmızı
> **"ESKI AYARLAR"** uyarısı gösterir. O uyarıyı görürseniz son değişikliğiniz
> **yürürlükte değil** demektir; konsoldaki hata satırına bakıp `config.yaml`'ı
> düzeltin. Yanlış yazılmış bir ayar adı da bildirilir ("`left_click_ley` diye bir
> ayar YOK - `left_click_key` mi demek istediniz?").

---

## 7. Hatırlatma (gerçekçi beklenti)
Webcam göz-takibi **kaba nişan** içindir (~2.5°). Minik hedefler için **büyüteç + parmak** şart. Gözlük + istemsiz kafa hareketi en zor durum; güvenilirlik yetmezse bu profil için **IR göz izleyici** (donanım) en sağlam çözümdür.

---

## 8. Lisans
Göz Faresi **özgür yazılımdır** (GNU GPL 3.0 ya da sonraki sürümleri, `GPL-3.0-or-later`).
- Ücretsiz kullanabilir, paylaşabilir ve değiştirebilirsiniz.
- Değiştirilmiş bir sürümü başkalarına veren, onun **tam kaynak kodunu** da aynı lisansla vermek zorundadır; **kapalı kaynaklı** bir sürüm yapılamaz.
- GPL satışı yasaklamaz; ama satan da kaynak kodunu aynı özgürlüklerle vermek zorundadır.
- Pakete gömülü Türkçe ses (`tr_TR-dfki-medium`, CC BY-NC-SA 4.0) **ticari amaçla kullanılamaz**. Bu ses, verisinin şartları doğrulanamamış (Blizzard 2013 Lessac) bir İngilizce sesten türetilmiştir: açık bir soru; ticari olmayan kullanım projenin amacıyla uyumlu.
- Derin bakış modelinin ağırlıkları **ETH-XGaze** veri kümesiyle eğitilmiştir; veri kümesi ticari olmayan kullanım içindir (CC BY-NC-SA 4.0).
- Bu iki parça yüzünden **paket bu hâliyle satılamaz ve ticari amaçla kullanılamaz/dağıtılamaz**. Kodu satmak isteyen Türkçe sesi ve derin modeli çıkarmalı ya da ticari kullanıma izin verenlerle değiştirmelidir.
- Paketteki her GPL / LGPL / MPL bileşenin **tam kaynak kodu** paketlerin yanında ayrı dosyalar olarak yayınlanır (liste: `KAYNAKLAR-Windows.txt` / `KAYNAKLAR-Linux.txt`, paketin içinde `lisanslar/KAYNAKLAR.txt`). Windows paketinde derin model işlemcide çalışır; Microsoft DirectML pakete konmaz.
- Türkçe ses motoru (piper-tts, espeak-ng) paketlerde **ayrı bir küçük programda** çalışır (`GozFaresi-Ses.exe` / `gozfaresi-ses`); Göz Faresi onu kendisi açıp kapatır. O program kapanırsa cümle bilgisayarın kendi sesiyle söylenir ve ses programı bir sonraki cümlede yeniden açılır.
- Göz Faresi'nin kendi kodu için GPL'e **ek bir izin** (madde 7) eklendi: GPL ile uyumlu olmayan Intel oneMKL / OpenMP (dikte motoru CTranslate2'nin içinde), OpenSSL 1.1 ve NVIDIA CUDA kitaplıklarıyla birlikte değiştirilmemiş hâlleriyle dağıtılabilir. İzin piper ve espeak-ng'yi kapsamaz; onlar ayrı programdır. Tam metin: `UCUNCU-TARAF-LISANSLAR.txt` başı.
- Program **hiçbir garanti olmadan** verilir.

*English: Göz Faresi is free software under the GNU GPL v3 or (at your option) any later version. Anyone who distributes a modified version must publish its full source under the same licence. The bundled Turkish voice and the deep gaze model (trained on ETH-XGaze) are non-commercial (CC BY-NC-SA 4.0), so the package as shipped may not be sold or used commercially; the Turkish voice is fine-tuned from a voice whose Blizzard 2013 data terms are unconfirmed (open question). In the packages, piper-tts and espeak-ng run as a separate program (GozFaresi-Ses / gozfaresi-ses). An additional permission under GPLv3 section 7 lets Göz Faresi's own code be combined and conveyed with unmodified Intel oneMKL / OpenMP, OpenSSL 1.1.x and NVIDIA CUDA/cuDNN/cuBLAS; it does not extend to piper-tts or espeak-ng (full text: UCUNCU-TARAF-LISANSLAR.txt). No warranty.*

Ayrıntı: paketteki `LICENSE.txt` ve `UCUNCU-TARAF-LISANSLAR.txt`.

---
*Bu kılavuz bilgilendirme amaçlıdır; klinik karar için uzmana danışın.*
