# OptiKey Kurulum ve Ayar Rehberi
## Gözle Konumlandır, Parmakla Seç — Windows + Webcam + Parmak Düğmesi

**Hedef kullanıcı:** ALS/MND nedeniyle yalnızca göz hareketi ve parmak düğmesi kullananlar  
**Etkileşim ilkesi:** Bakış imleci tuşu konumlandırır; parmak düğmesi seçer. Dwell (bekleme) KAPALI.  
**Platform:** Windows 10/11, harici webcam (eViacam (baş-pozu) veya GazePointer (göz-bakışı) ile sürülen imleç; Camera Mouse isteğe bağlı baş-pozu alternatifi), parmak düğmesi  
**OptiKey sürümü:** v4.x (GPLv3, ücretsiz)

---

## İçindekiler

1. [İndirme ve Kurulum](#1-indirme-ve-kurulum)
2. [İlk Açılış ve Türkçe Arayüz](#2-ilk-açılış-ve-türkçe-arayüz)
3. [Management Console (Yönetim Konsolu)](#3-management-console-yönetim-konsolu)
4. [Pointing & Selecting: İmleç Kaynağı ve Parmak Düğmesi Seçimi](#4-pointing--selecting-imleç-kaynağı-ve-parmak-düğmesi-seçimi)
5. [Büyüteç (Magnifier) Açma ve Yakınlaş-Tıkla Akışı](#5-büyüteç-magnifier-açma-ve-yakınlaş-tıkla-akışı)
6. [Kelime Tahmini ve Türkçe Klavye](#6-kelime-tahmini-ve-türkçe-klavye)
7. [Konuşma (TTS): Türkçe SAPI Sesi Seçimi](#7-konuşma-tts-türkçe-sapi-sesi-seçimi)
8. [Hızlı Doğrulama](#8-hızlı-doğrulama)

---

## 1. İndirme ve Kurulum

### 1.1 Sistem Gereksinimleri

| Bileşen | Minimum |
|---------|---------|
| İşletim Sistemi | Windows 10 64-bit veya Windows 11 |
| .NET Framework | 4.6 veya üzeri (Windows 10/11'de genellikle yüklü) |
| Webcam | Standart USB veya dahili kamera |
| Parmak Düğmesi | USB HID switch, gamepad (XInput/DirectInput) veya klavye tuşuna atanmış switch |

> **Not — .NET 4.6 kontrolü:** Başlat > Çalıştır > `appwiz.cpl` > Yüklü güncellemeler sekmesini açın, "Microsoft .NET Framework 4.6" arayın. Yoksa Microsoft'un resmi sayfasından indirin.

### 1.2 OptiKey İndirme

1. Tarayıcıda şu adrese gidin:  
   `https://github.com/OptiKey/OptiKey/releases/latest`
2. **Assets** bölümünü genişletin.
3. `OptiKey-vX.Y.Z-Setup.exe` dosyasını indirin (örnek: `OptiKey-v4.1.1-Setup.exe`).

### 1.3 Kurulum

1. İndirilen `.exe` dosyasına çift tıklayın.
2. Kullanıcı Hesabı Denetimi (UAC) onayı verin.
3. Kurulum sihirbazını varsayılan seçeneklerle tamamlayın; kurulum yolu genellikle:  
   `C:\Program Files (x86)\OptiKey\`
4. Kurulum tamamlandığında **OptiKey'i Başlat** onay kutusunu işaretli bırakın.

---

## 2. İlk Açılış ve Türkçe Arayüz

### 2.1 İlk Çalıştırma

- OptiKey, ekranda saydam bir klavye olarak belirir (varsayılan olarak üst kısımda).
- İlk açılışta **dil seçimi** iletişim kutusu gelebilir; bu adımda Türkçe'yi seçebilirsiniz.

### 2.2 Türkçe Arayüz ve Klavye Düzeni Seçimi

OptiKey, Türkçe arayüz ve Türkçe klavye düzenini (Q veya F) destekler.

**Adımlar:**

1. OptiKey çalışırken **ALT + M** tuşlarına basın. (OptiKey penceresinin önde ve odakta olması gerekir.)  
   Yönetim Konsolu (Management Console) açılır.
2. Üst sekmelerde **"Application"** (Uygulama) sekmesine tıklayın.
3. **"Language"** (Dil) açılır listesini bulun.
4. Listeden **"Turkish"** veya **"Türkçe"** seçeneğini seçin.
5. **"Keyboard"** (Klavye) veya **"Keyboard layout"** alanında **"Turkish Q"** ya da **"Turkish F"** seçin.
6. **OK** tuşuna tıklayın. OptiKey yeniden başlar; arayüz Türkçe olur.

> **Not:** Dil değişikliği hem menü dilini hem de ekran klavyesinin tuş düzenini etkiler.

---

## 3. Management Console (Yönetim Konsolu)

Management Console, OptiKey'in tüm ayarlarını yönettiğiniz merkezi paneldir.

### 3.1 Açma Yöntemleri

| Yöntem | Nasıl |
|--------|-------|
| Klavye kısayolu | **ALT + M** (OptiKey odaktayken) |
| Sağ tık menüsü | OptiKey klavye üzerinde (görev çubuğu simgesinde DEĞİL) sağ tıkla → **"Management Console"** |

### 3.2 Ana Sekmeler

| Sekme Adı | İçerik |
|-----------|--------|
| **Pointing & Selecting** | İmleç kaynağı, dwell, tuş/düğme seçimi |
| **Visuals** | Büyüteç, tema, şeffaflık |
| **Sounds** | TTS sesi, hız, ses düzeyi |
| **Words** | Kelime tahmini, sözlük |
| **Application** | Dil, başlangıç ayarları |

---

## 4. Pointing & Selecting: İmleç Kaynağı ve Parmak Düğmesi Seçimi

Bu bölüm, sistemin çekirdeğidir. Dört kritik ayar yapılandırılacaktır:

1. **Source = Mouse Position** (webcam imlecini oku)
2. **Dwell = KAPALI** (bekleme ile otomatik seçim yok)
3. **Key Selection source = Parmak düğmesi** (XInput / DirectInput / klavye tuşu)
4. **Point Selection source** (isteğe bağlı, fare simülasyonu için)

### 4.1 Management Console'u Açın

**ALT + M** → **"Pointing & Selecting"** sekmesine tıklayın.

---

### 4.2 Adım 1 — Source = Mouse Position (Webcam İmleç Kaynağı)

> OptiKey, webcam'i doğrudan kullanamaz. Bunun yerine **eViacam (baş-pozu)** veya **GazePointer (göz-bakışı)** gibi bir uygulama, bakılan yere göre Windows fare imlecini hareket ettirir. Baş-pozu için alternatif olarak Camera Mouse da kullanılabilir. OptiKey bu imleci "Mouse Position" olarak okur.

**Management Console → Pointing & Selecting sekmesi:**

1. **"Source"** (Kaynak) açılır listesini bulun.
2. **"Mouse position"** seçeneğini seçin.

Bu seçim, OptiKey'in tüm tuş konumlandırma işlemini Windows fare imlecinden okumasını sağlar.

> **eViacam / GazePointer / Camera Mouse kurulumu bu rehberin kapsamı dışındadır.** Bu yazılımların, bakılan yere göre fareyi doğru biçimde konumlandırdığından emin olun; ardından OptiKey'e dönün.

---

### 4.3 Adım 2 — Dwell'i (Bekleme Seçimi) KAPATIN

Dwell açıkken OptiKey, bir tuşa belirli süre bakıldığında otomatik seçer. Bu sistemi devre dışı bırakın.

**Management Console → Pointing & Selecting sekmesi:**

1. **"Key selection source"** (Tuş seçim kaynağı) açılır listesini bulun.
2. Mevcut değer **"Fixation"** (veya "Dwell/Bekleme") ise BUNU DEĞİŞTİRİN — bir sonraki adımda parmak düğmesini seçeceksiniz.
3. Dwell'e ait zamanlayıcı alanları ("Key fixation time to lock-on", "Key fixation time to complete" vb.) otomatik gizlenecektir.

---

### 4.4 Adım 3 — Parmak Düğmesini Tespit Etme (TestControllers.exe)

Parmak düğmenizin **XInput mi yoksa DirectInput mi** olduğunu ve **hangi düğme numarasını** gönderdiğini öğrenmek için:

1. Windows Gezgini'nde OptiKey kurulum klasörüne gidin. Kurduğunuz sürüme göre klasör adı değişebilir; iki olası yol:  
   - `C:\Program Files (x86)\OptiKey\`  
   - `C:\Program Files (x86)\OptiKey Pro\`  
   > **Not:** Klasör adı kurduğunuz sürüme göre değişebilir; OptiKey kurulum klasöründe `TestControllers.exe` arayın.
2. **`TestControllers.exe`** dosyasına çift tıklayın. Siyah bir konsol penceresi açılır.
3. Parmak düğmenize basın.
4. Konsolda görünen çıktıyı okuyun:
   - **"XInput"** yazıyorsa: XInput protokolünü kullanıyorsunuz.
   - **"DirectInput"** yazıyorsa: DirectInput protokolünü kullanıyorsunuz.
   - **Düğme adı veya numarası** (örn. `Button 0`, `A Button`) görüntülenecektir.
5. Bu bilgileri not alın; bir sonraki adımda kullanacaksınız.

> **Alternatif — Windows Oyun Denetleyicisi:** Başlat'ta "USB oyun denetleyicileri kur" aratın → denetleyicinizi seçin → Özellikler → düğmeye basarak ışığın yandığını doğrulayın.

---

### 4.5 Adım 4 — Key Selection Source = Parmak Düğmesi

**Management Console → Pointing & Selecting sekmesi:**

**XInput düğmesi kullanıyorsanız:**

| Alan | Değer |
|------|-------|
| Key selection source | `Gamepad button (XInput)` |
| Key selection gamepad controller | `All XInput controllers` (tek cihaz varsa) veya cihaz adını seçin |
| Key selection gamepad button | TestControllers.exe'den gördüğünüz düğme adı/numarası |

**DirectInput düğmesi kullanıyorsanız:**

| Alan | Değer |
|------|-------|
| Key selection source | `Gamepad button (DirectInput)` |
| Key selection gamepad controller | `All DirectInput controllers` veya cihaz adını seçin |
| Key selection gamepad button | TestControllers.exe'den gördüğünüz düğme adı/numarası |

**Klavye tuşuna atanmış switch kullanıyorsanız:**

| Alan | Değer |
|------|-------|
| Key selection source | `Keyboard key` |
| Key selection keyboard key | Parmak düğmenizin gönderdiği tuş (örn. `Space`, `Enter`, `F1`) |

---

### 4.6 Adım 5 — Ayarları Kaydetme

1. Tüm alanları doldurduktan sonra **"OK"** butonuna tıklayın.
2. OptiKey yeniden başlar.
3. Artık: gözle tuşu nişan alın → parmak düğmesine basın → tuş seçilir.

---

## 5. Büyüteç (Magnifier) Açma ve Yakınlaş-Tıkla Akışı

Büyüteç, fare simülasyonu sırasında hassas tıklama için ekranın bir bölümünü büyütür. Küçük hedeflere (bağlantı, düğme) tıklarken kullanılır.

### 5.1 Büyüteci Yapılandırma

**Management Console → Visuals (Görseller) sekmesi:**

1. **ALT + M** ile konsolu açın.
2. **"Visuals"** sekmesine tıklayın.
3. Aşağıdaki alanları bulun ve değerleri ayarlayın:

| Alan | Açıklama | Önerilen Değer |
|------|----------|----------------|
| **Magnify source (% of screen)** | Büyütülecek kaynak alanın boyutu (imleç çevresindeki alan) | `%10` |
| **Magnify destination (% of screen)** | Ekran ortasında görüntülenecek büyütülmüş alanın boyutu | `%60` |

> **Büyütme oranı hesabı:** Kaynak %10, hedef %60 ise büyütme = 60 ÷ 10 = **6x**.  
> Daha fazla büyütme için kaynak değerini küçültün veya hedef değerini büyütün.

4. **OK** ile kaydedin.

### 5.2 Büyüteci Açma/Kapama

OptiKey klavyesinde **"Magnifier"** (Büyüteç) tuşunu bulun. Bu tuş **3 durum** arasında geçiş yapar:

| Durum | Anlamı |
|-------|--------|
| **Kapalı (UP)** | Büyüteç devre dışı |
| **Bir sonraki eylem için açık (DOWN)** | Yalnızca bir sonraki fare tıklaması için büyüteç açılır, sonra kapanır |
| **Sürekli açık (LOCKED DOWN)** | Her fare simülasyonu işleminde büyüteç etkin kalır |

### 5.3 Yakınlaş-Tıkla Akışı (Gözle + Parmak Düğmesiyle)

1. OptiKey klavyesinde **Magnifier tuşuna bakın** → parmak düğmesine basın (büyüteç AÇIK duruma geçer).
2. Fare simülasyonu moduna girin (OptiKey klavyesinde fare simgeli tuşa bakıp seçin).
3. Gözünüzü hedef konuma getirin — ekran ortasında büyütülmüş önizleme belirir.
4. Hedef doğru konumdayken **parmak düğmesine** basın → tıklama gerçekleşir.
5. Büyüteç "bir sonraki eylem" modundaysa otomatik kapanır; "sürekli açık" modundaysa aynı süreç tekrarlanır.

---

## 6. Kelime Tahmini ve Türkçe Klavye

### 6.1 Kelime Tahmini — Presage Kurulumu

OptiKey'in kelime tahmini, **Presage** adlı ayrı bir motor kullanır.

**Adım 1 — Presage İndirme:**

1. `https://github.com/OptiKey/OptiKey/releases` adresine gidin.
2. Kullandığınız OptiKey sürümüne uygun Presage paketini indirin:
   - OptiKey v4 ve üzeri → **64-bit Presage**
   - OptiKey v3 ve altı → **32-bit Presage**
3. `presage-X.X.X-setup.exe` dosyasını çalıştırın.

> **ÖNEMLİ:** Kurulum sırasında HİÇBİR varsayılan seçeneği değiştirmeyin. Presage, belirli varsayılan yollar kullanır; farklı bir klasöre kurarsanız çalışmaz.

**Adım 2 — Presage Servisinin Çalıştığını Doğrulama:**

- Kurulum sonrası Presage WCF Service otomatik olarak başlar; görev çubuğu sistem tepsisinde simgesini arayın.
- Yalnızca simge görünmüyorsa: Başlat menüsünde "Presage" aratın → servisi manuel başlatın.

**Adım 3 — OptiKey'i Yönetici Olarak Çalıştırma:**

Presage entegrasyonu için OptiKey'in yönetici haklarıyla çalışması gerekir:

1. Masaüstündeki OptiKey kısayoluna sağ tıklayın → **"Yönetici olarak çalıştır"**.
2. Kalıcı yapmak için: Kısayol → Özellikler → Uyumluluk → "Bu programı yönetici olarak çalıştır" onay kutusunu işaretleyin.

**Adım 4 — Management Console'da Kelime Tahminini Etkinleştirme:**

1. **ALT + M** → **"Words"** (Kelimeler) sekmesi.
2. **"Suggestion mode"** (Öneri modu) alanını **"Presage"** olarak değiştirin.
3. **"Location of Presage database .db file"** alanında, varsayılan İngilizce veritabanı zaten seçili olacaktır (`database_en.db`).
4. **OK** ile kaydedin.

### 6.2 Türkçe Kelime Tahmini Notu

Presage'in varsayılan dağıtımında Türkçe `.db` dosyası **bulunmamaktadır**; yalnızca İngilizce, İspanyolca ve İtalyanca mevcuttur. Türkçe tahmin için seçenekler:

- Presage'in GitHub sayfasından topluluk tarafından oluşturulmuş Türkçe veritabanı dosyası arayın.
- Kendi Türkçe metin korpora'nızdan Presage belgelerine göre özel `.db` dosyası oluşturun.
- Geçici çözüm: OptiKey'in yerleşik sözlük öğrenme özelliği ("Words" sekmesi → "Learn from what I type") zamanla sık kullanılan Türkçe sözcükleri öğrenir.

### 6.3 Türkçe Klavye Düzenini Onaylama

1. **ALT + M** → **"Application"** sekmesi.
2. **"Language"** = **Turkish**, **"Keyboard"** = **Turkish Q** veya **Turkish F** olduğunu doğrulayın.
3. OptiKey ekran klavyesinde Türkçe karakterler (ş, ğ, ü, ö, ç, ı) görünüyorsa ayar doğrudur.

---

## 7. Konuşma (TTS): Türkçe SAPI Sesi Seçimi

OptiKey, Microsoft SAPI uyumlu sesleri ve MaryTTS motorunu destekler.

### 7.1 Management Console'da Ses Ayarları

1. **ALT + M** → **"Sounds"** (Sesler) sekmesi.
2. **"Speech"** (Konuşma) bölümünü bulun.
3. **"Voice"** (Ses) açılır listesinde mevcut SAPI seslerini görürsünüz.

### 7.2 Windows Türkçe SAPI Sesi Kurma

Windows'ta varsayılan olarak Türkçe SAPI sesi bulunmayabilir. Kurmak için:

**Windows 10/11 Dil Paketi Yöntemi:**

1. **Başlat → Ayarlar → Zaman ve Dil → Konuşma** bölümüne gidin.
2. **"Bir ses ekle"** (veya "Metin okuma sesi") bölümünde "Türkçe" arayın.
3. Türkçe sesi (örneğin **"Microsoft Süha"** (erkek), **"Microsoft Yelda"** (kadın) veya **"Microsoft Tolga"** gibi) yükleyin.  
   > **Uyarı:** Ses adları Windows sürümüne/bölgeye göre değişebilir; Konuşma ayarındaki Voice listesinden TÜRKÇE olan sesi seçin (adı sisteminizde farklı görünebilir). Yukarıdaki adlar yalnızca örnektir.
4. Yükleme tamamlandıktan sonra OptiKey'i kapatıp yeniden açın.
5. **ALT + M → Sounds → Voice** listesinde Türkçe sesler görünecektir.

**Alternatif — MaryTTS (Açık Kaynak):**

MaryTTS, Türkçe'yi listeleyen açık kaynaklı bir TTS motorudur; ancak Türkçe ses kalitesi deneysel/sınırlıdır ve aktif geliştirilmemektedir. Öncelikli tercih Windows SAPI Türkçe sesi olmalıdır. Yine de denemek isterseniz:

1. `https://github.com/marytts/marytts/releases` adresinden `marytts-5.2.zip` indirin.
2. `C:\MaryTTS\marytts-5.2\` gibi bir klasöre çıkartın.
3. **ALT + M → Sounds** sekmesinde:
   - **"Use MaryTTS speech synthesizer"** onay kutusunu işaretleyin.
   - `marytts-server.bat` dosyasının yolunu gösterin.
4. OptiKey'i yeniden başlatın; **"Voice"** listesinde Türkçe MaryTTS sesleri görünür.

### 7.3 Ses Hızı ve Ses Düzeyi Ayarı

| Alan | Açıklama | Önerilen Başlangıç |
|------|----------|-------------------|
| **Speech rate** (Konuşma hızı) | Seslendirme hızı | Orta (50-60) |
| **Volume** (Ses düzeyi) | Çıkış ses seviyesi | 80-100 |

Ayarları **OK** ile kaydedin.

---

## 8. Hızlı Doğrulama

Bu adımlar, sistemin doğru çalışıp çalışmadığını test eder.

### 8.1 Ön Kontrol Listesi

- [ ] eViacam (baş-pozu) veya GazePointer (göz-bakışı) çalışıyor ve bakışınızla/baş hareketinizle fareyi kontrol edebiliyorsunuz.
- [ ] OptiKey açık ve Türkçe klavye düzeni görünüyor.
- [ ] Parmak düğmeniz USB ile bağlı ve sistem tarafından tanınıyor.
- [ ] Presage servisi çalışıyor (sistem tepsisinde simge var).

### 8.2 Temel İşlev Testi

**Test 1 — Tuş Seçimi:**

1. Gözünüzle OptiKey klavyesinde **"A"** tuşuna bakın.
2. Parmak düğmesine basın.
3. Beklenen: "A" harfi seçilir ve metin alanına yazılır.
4. Dwell (bekleme) ile seçim olmamalı — yalnızca parmak düğmesi seçim yapmalı.

**Test 2 — Seslendirme:**

1. Metin alanına birkaç kelime yazın (gözle nişan + parmak seçimi ile).
2. OptiKey klavyesinde konuşma/seslendirme tuşuna (hoparlör simgesi) bakıp seçin.
3. Beklenen: Yazdığınız metin Türkçe sesle seslendirilir.

**Test 3 — Kelime Tahmini:**

1. "mer" harflerini yazın.
2. Beklenen: OptiKey'in üst kısmında "merhaba", "merkez" gibi öneriler belirir (Türkçe sözlük veya öğrenme aktifse; yoksa İngilizce öneriler görünebilir veya öneri gelmeyebilir).
3. Gözle öneriye bakıp parmak düğmesiyle seçin — kelime tamamlanmalı.

**Test 4 — Büyüteç:**

1. OptiKey klavyesinde Magnifier tuşuna bakıp seçin (büyüteç etkinleşir).
2. Fare simülasyonu moduna geçin.
3. Gözünüzü masaüstündeki küçük bir simgeye götürün — ekranda büyütülmüş önizleme belirmeli.
4. Parmak düğmesiyle seçin — simge tıklanmalı.

### 8.3 Sorun Giderme Özeti

| Belirti | Olası Neden | Çözüm |
|---------|-------------|-------|
| Parmak düğmesi seçim yapmıyor | "Key selection source" yanlış | ALT+M → Pointing & Selecting → Key selection source'u kontrol et |
| Düğmeye basmadan beklemeyle seçim oluyor | Dwell hâlâ açık | Key selection source'un "Fixation" olmadığını doğrula |
| Türkçe ses yok | SAPI ses yüklü değil | Windows Ayarlar → Konuşma → Türkçe ses ekle |
| Kelime tahmini çalışmıyor | Presage servisi durmuş | Görev yöneticisinde Presage servisini başlat; OptiKey'i yönetici olarak çalıştır |
| İmleç tutarsız hareket ediyor | eViacam / GazePointer kalibrasyonu gerekiyor | Kullandığınız yazılımı (eViacam veya GazePointer) yeniden kalibre et |
| "Source = Mouse Position" fare takip etmiyor | eViacam / GazePointer / Camera Mouse çalışmıyor | Bu yazılımların açık ve kalibreli olduğunu doğrula |

---

## Hedef Profil Özeti

| Ayar | Değer |
|------|-------|
| Source | Mouse Position |
| Dwell (Key Selection via Fixation) | KAPALI |
| Key Selection source | Gamepad button (XInput veya DirectInput) veya Keyboard key |
| Büyüteç (Magnifier) | AÇIK (ihtiyaca göre) |
| Kelime tahmini | AÇIK (Presage) |
| Arayüz dili | Türkçe |
| Klavye düzeni | Turkish Q veya Turkish F |
| TTS sesi | Türkçe SAPI (Microsoft Süha/Yelda) veya MaryTTS Türkçe |

---

## Referanslar

- OptiKey GitHub: https://github.com/OptiKey/OptiKey
- OptiKey Wiki — Pointing & Selecting: https://github.com/OptiKey/OptiKey/wiki/Pointing-&-selecting-settings
- OptiKey Wiki — Joystick/Gamepad: https://github.com/OptiKey/OptiKey/wiki/Using-a-Joystick-or-Gamepad-as-selection-source
- OptiKey Wiki — Desteklenen Diller: https://github.com/OptiKey/OptiKey/wiki/Supported-languages
- OptiKey Wiki — Konuşma: https://github.com/OptiKey/OptiKey/wiki/Speech
- OptiKey Wiki — Kelime Tahmini: https://github.com/OptiKey/OptiKey/wiki/Next-word-prediction
- OptiKey Wiki — Görseller: https://github.com/OptiKey/OptiKey/wiki/Visual-settings
- MaryTTS: https://github.com/marytts/marytts/releases

---

Bu rehber bilgilendirme amaçlıdır; klinik karar için uzmana danışın.
