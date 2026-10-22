# Switch-Tarama Yedek Profili

**Hedef kullanıcı:** Kas erimesi olan, birincil giriş yöntemi olarak OptiKey + webcam göz-takibi + parmak düğmesi kullanan birey.

**Bu belgenin amacı:** Gözler yeterince güvenilir çalışmadığında (pitozis, göz yorgunluğu, yetersiz ışık) devreye giren ikinci bir OptiKey profilini — tek parmak düğmesiyle anahtar-tarama (switch scanning) — adım adım açıklamak.

---

## 1. Bu Yedek Ne Zaman Devreye Girer?

Birincil sistem (göz-bakışı) şu koşullarda yetersiz kalabilir:

| Durum | Belirti |
|-------|---------|
| **Pitozis** (göz kapağı düşüklüğü) | Takip penceresi daralır, hedef kaçırma artar |
| **Göz yorgunluğu** | Uzun oturumların sonunda fikasyon süresi uzar, seçim hataları artar |
| **Yetersiz ışık / arka ışık** | Webcam kızılötesi izi bozulur, imleç titrer veya kaybolur |
| **Göz kuruluğu / sık kırpma** | Fikasyon pencereleri sürekli sıfırlanır |

Bu koşullardan herhangi biri gündeme geldiğinde kullanıcı veya bakım veren switch-tarama profiline geçer. Göz kurtarıldıktan, ışık düzeltildikten ya da dinlendikten sonra göz profiline geri dönülür.

---

## 2. Switch-Tarama Profili: Kurulum

Switch-tarama profili, OptiKey'in **tek bir atanmış tuşla** tuş ızgarasını otomatik taramasını ve kullanıcının aynı tuşa (veya başka bir tuşa) basarak seçim yapmasını sağlar. Gözün hiçbir işlevi yoktur.

### 2.1 Yönetim Konsolunu Açma

OptiKey penceresine odaklanın (tıklayın), ardından:

```
ALT + M
```

Yönetim Konsolu (Management Console) açılır.

### 2.2 "Pointing & Selecting" Sekmesi

Sol sütundaki sekme listesinden **"Pointing & Selecting"** sekmesini seçin.

### 2.3 Kaynak (Source) Ayarı — Göz Takibini Devre Dışı Bırakma

**"Source"** alanını şu şekilde değiştirin:

| Alan | Göz Profili | Switch-Tarama Profili |
|------|-------------|----------------------|
| Source | Webcam / göz-takip cihazı | **Mouse** |

> Neden Mouse? Switch-tarama sırasında konum girişi OptiKey'in iç tarama döngüsü tarafından yönetilir; bir fare konumu kaynağı gereklidir ancak fareyi fiziksel olarak kullanmak zorunda değilsiniz.

### 2.4 Tuş Seçim Kaynağı (Key Selection Source)

**"Key selection source"** alanını şu değere getirin:

```
Keyboard keys
```

Bu seçim yapıldıktan sonra hemen altında **hangi klavye tuşunun seçim tetikleyicisi olacağını** belirten yeni bir alan belirir. Kullanıcının parmak düğmesinin hangi tuşu simüle ettiğini buraya girin (yaygın örnekler: `Space`, `Return`, `F1`).

> Parmak düğmesi bir USB veya Bluetooth erişilebilirlik anahtarıdır ve sisteme bir klavye tuşu olarak görünür. Kutudaki etikette veya eşlik eden yazılımda hangi tuşu gönderdiği yazar.

### 2.5 Fikasyon/Bekleme Süresini Kapatma

Switch-tarama modunda **dwell (bekleme) seçimi** gereksiz ve kafa karıştırıcıdır; kapatın:

- **"Key fixation time to complete (ms)"** → Bu alan switch modunda aktif değildir; ancak emin olmak için **"Key selection source"** "Keyboard keys" seçiliyken bu alan grileşir (devre dışı kalır).

### 2.6 Tarama Hızı Ayarı

Switch-tarama modunu etkinleştirmek için **"Key selection source"**'u "Keyboard keys" yapmak yeterlidir (bkz. 2.4). "Point selection source" alanının ayrıca değiştirilmesi gerekmez; bu adım switch-tarama için gerekli değildir.

Tarama döngüsü hızı **"Pointing & Selecting"** sekmesindeki ilgili aralık alanıyla kontrol edilir. Bu alanın adı OptiKey sürümüne ve cihaz yapılandırmasına göre farklılık gösterebilir (örneğin "Mouse position sample interval (ms)" ya da benzer bir etiketle görünebilir). Bakım veren bu sekmedeki ms cinsinden aralık değerini aşağıdaki önerilere göre ayarlasın.

**Önerilen başlangıç değerleri:**

| Parametre | Önerilen Değer | Açıklama |
|-----------|---------------|----------|
| Tarama aralığı (ms) | **1500 – 2000** | Parmak yorgunluğunu azaltır, acele gerektirmez |
| Key fixation lock-on (ms) | **0** (keyboard keys modunda etkisiz) | — |
| Tarama vurgulaması | Açık (varsayılan) | Hangi tuşun tarandığını görsel olarak gösterir |

> Kas erimesi olan kullanıcı için 1500 ms genellikle iyi bir başlangıç noktasıdır. Hız çok yüksekse (yani ms değeri düşükse) parmak yetişemez; hız çok düşükse (ms çok yüksekse) seans uzar. Kullanıcıyla birlikte ayarlayın.

### 2.7 Ayarları Kaydetme ve Yeniden Başlatma

"OK" düğmesine basın. OptiKey yeniden başlatmayı önerebilir — onaylayın. Yeniden başladığında tarama modu etkindir.

---

## 3. İki Profil Arası Hızlı Geçiş

OptiKey, yerleşik çok-profil yöneticisine sahip değildir. Profil geçişi Yönetim Konsolu üzerinden yapılır. Bu işlemi kolaylaştırmak için aşağıdaki yöntem önerilir.

### 3.1 Ayar Sayfasını Hazır Tutma

Bakım veren, sık gerekebilecek olan iki yapılandırmayı kağıda ya da ekranda açık tutar:

**Göz Profili Ayarları:**
- Source: `[Webcam / göz takip cihazı adı]`
- Key selection source: `Fixation`
- Key fixation time to complete: `[kullanıcının tercih ettiği ms]`

**Switch-Tarama Profili Ayarları:**
- Source: `Mouse`
- Key selection source: `Keyboard keys`
- Seçim tuşu: `[parmak düğmesinin tuşu, ör. Space]`

### 3.2 Geçiş Adımları (Göz → Switch)

1. `ALT + M` → Yönetim Konsolu aç
2. **Pointing & Selecting** sekmesine geç
3. **Source** → `Mouse` yap
4. **Key selection source** → `Keyboard keys` yap
5. Seçim tuşunu doğrula (değişmediyse dokunmaya gerek yok)
6. **OK** → Yeniden başlat
7. Tarama döngüsünün başladığını tuş vurgularından doğrula

**Süre hedefi:** Deneyimli bakım veren için bu geçiş 60–90 saniyedir.

### 3.3 Geçiş Adımları (Switch → Göz)

1. `ALT + M` → Yönetim Konsolu aç
2. **Pointing & Selecting** sekmesine geç
3. **Source** → Webcam / göz-takip cihazını seç
4. **Key selection source** → `Fixation` yap
5. **OK** → Yeniden başlat
6. Webcam ışığının yandığını ve göz takibinin başladığını doğrula

> Göz profiline geçmeden önce ışık koşullarını, kullanıcının yüz pozisyonunu ve pitozis durumunu kontrol edin. Yetersiz koşullarda geçiş yapılırsa sorun çözülmemiş olur.

---

## 4. Parmak Yorulduğunda: Dwell'i Geçici Açma

Switch-tarama oturumu uzadığında ya da parmak kasılma/yorgunluk belirtisi gösterdiğinde, tarama modundan çıkmak yerine **dwell (bekleme) geçici olarak etkinleştirilebilir.** Bu durumda kullanıcı yalnızca bakışını hedefe odaklar ve düğmeye basmak gerekmez.

### 4.1 Geçici Dwell Etkinleştirme

1. `ALT + M` → Yönetim Konsolu
2. **Source** → `[Webcam / göz-takip cihazı]` (eğer göz hâlâ takip edebiliyorsa)
3. **Key selection source** → `Fixation`
4. **"Key fixation time to complete (ms)"** → `2000–3000` (parmak baskısı olmadan seçim için yeterli süre)
5. **OK** → Yeniden başlat

Bu mod parmağı tamamen dinlendirirken göz takibinin kısmi çalışmasından yararlanır. Gözler de çok yorgunsa bu seçenek uygun değildir; dinlenme molası tercih edilmelidir.

### 4.2 Hibrit Kullanım Notu

Bazı durumlarda kullanıcı hem göz takibini hem de parmak düğmesini birlikte kullanabilir:
- Göz bakışı konumu belirler (imleç)
- Parmak düğmesi seçimi onaylar (dwell kapalı)

Bu zaten ana sistemin çalışma biçimidir. Switch-tarama yalnızca göz takibi tamamen yetersiz kaldığında devreye girer.

---

## 5. Bakım Veren İçin: Duraklat, Devam ve Güvenli Çıkış

### 5.1 Taramayı Duraklatma

OptiKey çalışırken tarama döngüsünü geçici olarak durdurmak için:

- **OptiKey penceresini küçültün** (görev çubuğuna): Tarama döngüsü durur, odak kaybedilir.
- **Alternatif:** Fareyi OptiKey penceresinin dışına taşıyın — fare kaynağı aktifken pencere odağı önemlidir.

Taramayı yeniden başlatmak için OptiKey penceresine tıklayın.

### 5.2 Oturum Sonu — Güvenli Çıkış

Kullanıcı seansı bitirdiğinde veya cihazı bırakmak istediğinde:

1. Tarama döngüsü **"Close"** veya **"Quit"** tuşuna gelene kadar bekleyin (OptiKey klavyesinde vardır)
2. Kullanıcı parmak düğmesiyle seçim yapar → OptiKey kapanır
3. **Alternatif (bakım veren):** ALT + F4 ile OptiKey'i doğrudan kapatın

> Tarama sırasında yanlışlıkla istenmeyen tuşlara basılmasını engellemek için bakım veren `ALT + M` ile konsoldan **Source**'u geçici olarak Mouse dışında bir değere alabilir — bu taramayı dondurur.

### 5.3 Sıkışma / Kurtarma Senaryosu

| Durum | Çözüm |
|-------|-------|
| Tarama döngüsü yanıt vermiyor | OptiKey penceresine `ALT + F4` → Yeniden başlat |
| Parmak düğmesi artık çalışmıyor | Yönetim Konsolunu fareyle aç, Source'u Mouse → Fixation'a al, geçici dwell ile devam et |
| Hem göz hem parmak çalışmıyor | Bakım veren fareyle OptiKey'i kontrol eder; kullanıcı dinlenme molasına geçer |
| Ekran dondu / OptiKey yanıt vermiyor | `CTRL + ALT + DEL` → Görev Yöneticisi → OptiKey sürecini sonlandır → Yeniden başlat |

---

## 6. Hızlı Başvuru Kartı

Bakım veren bu kartı yazdırıp ekranın yanına koyabilir.

```
SWITCH-TARAMA MODİ AÇ:
  ALT+M → Pointing & Selecting
  Source: Mouse
  Key selection source: Keyboard keys
  Seçim tuşu: [___________]
  Tarama hızı: 1500–2000 ms
  OK → Yeniden Başlat

GÖZ MODİNA GEÇ:
  ALT+M → Pointing & Selecting
  Source: [webcam/göz-takip cihazı]
  Key selection source: Fixation
  OK → Yeniden Başlat

PARMAK DİNLENİYOR (GEÇİCİ DWELL):
  Key selection source: Fixation
  Fixation time to complete: 2000–3000 ms

DONMA / KURTARMA:
  ALT+F4 → OptiKey yeniden başlat
```

---

## Kaynaklar

- OptiKey Wiki — Change selection method: https://github.com/OptiKey/OptiKey/wiki/Change-selection-method
- OptiKey Wiki — Pointing & selecting settings: https://github.com/OptiKey/OptiKey/wiki/Pointing-&-selecting-settings
- OptiKey Wiki — Using eye trackers: https://github.com/OptiKey/OptiKey/wiki/Using-eye-trackers

---

Bu rehber bilgilendirme amaçlıdır; klinik karar için uzmana danışın.
