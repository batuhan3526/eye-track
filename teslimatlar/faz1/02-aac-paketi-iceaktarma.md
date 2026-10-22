# OptiKey Türkçe AAC Paketi - İçe Aktarma Rehberi

## Genel Bakış

Bu rehber, `turkce-aac-paketi.xml` ve bağlantılı kategori klavyelerini OptiKey'e Dynamic Keyboard (Dinamik Klavye) olarak nasıl yükleyeceğinizi adım adım açıklar. Göz izleme cihazı kullanan ALS/MND tanılı bireyler için tek bakış + onay (parmak basışı) ile tam Türkçe cümle yazma ve seslendirme amacıyla tasarlanmıştır.

---

## Dosyalar

Pakette şu XML dosyaları yer almaktadır; hepsi **aynı klasörde** bulunmalıdır:

| Dosya | İçerik |
|---|---|
| `turkce-aac-paketi.xml` | Ana menü (kategori seçimi + hızlı erişim) |
| `turkce-aac-acil-saglik.xml` | Acil durum ve sağlık ifadeleri |
| `turkce-aac-temel-ihtiyac.xml` | Temel ihtiyaç ifadeleri |
| `turkce-aac-konfor-bakim.xml` | Konfor ve bakım ifadeleri |
| `turkce-aac-sosyal-duygu.xml` | Sosyal iletişim ve duygu ifadeleri |
| `turkce-aac-kisisel.xml` | Kişiye özel yer tutucu ifadeler |

---

## Kurulum Adımları

### 1. Dosyaları Kopyalayın

Tüm `.xml` dosyalarını OptiKey'in Dynamic Keyboards klasörüne kopyalayın.

Varsayılan konum (OptiKey sürümüne göre değişebilir):

```
C:\Program Files (x86)\OptiKey\Resources\DynamicKeyboards\
```

veya kullanıcı belgelerindeki özel klasör:

```
C:\Users\[KullaniciAdi]\Documents\OptiKey\DynamicKeyboards\
```

> **Önemli:** Tüm dosyalar aynı klasörde olmalıdır. Farklı alt klasörlere koyulursa klavyeler arası geçiş çalışmaz.

### 2. OptiKey'i Açın

OptiKey'i normalde başlatın. Göz izleme cihazınızın bağlı ve kalibre edilmiş olduğundan emin olun.

### 3. Management Console'u Açın

- Klavye simgesine bakın veya tıklayın → **Management Console** (Yönetim Konsolu) seçeneğini açın
- Ya da `ALT + M` kısayolunu kullanın (mouse/klavye erişimi varsa)

### 4. Startup Keyboard'u Ayarlayın

Management Console içinde (sekme adları: Pointing & Selecting / Visuals / Sounds / Words / Application):

1. **Application** sekmesine gidin
2. **Startup keyboard** (Başlangıç klavyesi) açılır menüsünü bulun
3. Şu seçeneklerden birini seçin:
   - `Custom` (Özel) → ardından `turkce-aac-paketi.xml` dosyasının tam yolunu girin
   - veya `Dynamic Keyboards` → dosyaları koyduğunuz klasörü belirtin; açılışta tüm dinamik klavyeler listede görünür

### 5. Türkçe Sesi Etkinleştirin

Konuşma özelliğinin Türkçe çalışması için:

1. Management Console → **Sounds** sekmesine gidin (Konuşma/Speech bölümü bu sekme altındadır)
2. **Voice** (Ses) açılır listesinden Türkçe bir ses seçin
   - Windows yerleşik sesi: `Microsoft Tolga` (Türkçe erkek) veya `Microsoft Filiz` (Türkçe kadın)
   - Eğer listede yoksa: Windows Ayarlar → Zaman ve Dil → Konuşma → Sesleri Yönet → Türkçe ses ekleyin
3. **Rate** (Hız) ve **Volume** (Ses seviyesi) ayarlarını kullanıcıya göre düzenleyin
4. **Apply** (Uygula) → **OK**

### 6. Göz İzleme Kalibrasyonunu Kontrol Edin

- Büyük hedef alanlı tuşlarda bile doğru seçim yapılabilmesi için göz izleme cihazını kalibre edin
- Bu paket **dwell (bekleme) KAPALI, seçim = PARMAK DÜĞMESİ** ilkesine dayanır; seçim ve işaretleme ayarları için `01-optikey-kurulum-ayar-rehberi.md`'deki **Pointing & Selecting** profiline bakın (dwell KAPALI, seçim parmak düğmesiyle)
- Dwell Time veya Fixation ayarlarını etkinleştirmeyin

### 7. İlk Kullanım Testi

- Ana menü açıldığında **"Evet."** veya **"Teşekkür ederim."** tuşuna bakıp seçin
- Cümle hem çıktı panelinde görünmeli hem Türkçe olarak seslendirilmelidir
- Her kategori tuşuna bakarak ilgili sayfaya geçildiğini doğrulayın
- **"Geri / Ana Menü"** tuşuyla ana menüye dönüldüğünü kontrol edin

---

## Kişisel Özelleştirme

`turkce-aac-kisisel.xml` dosyasını bir metin editörüyle (Notepad, Notepad++ önerilir) açın. `KURULUMDA DOLDURULACAK` yorumlarını bulun ve şu yer tutucuları gerçek değerlerle değiştirin:

| Yer Tutucu | Örnek Değer |
|---|---|
| `[Aile Adı 1]` | `Ayşe` |
| `[Aile Adı 2]` | `Mehmet` |
| `[İlaç Adı 1]` | `Baclofen` |
| `[İlaç Adı 2]` | `Riluzol` |
| `[Ziyaretçi Adı]` | `Hemşire Fatma` |
| `[Özel Cümle]` | `Elimi tutar mısınız?` |
| `[Özel Cümle 2]` | `Ailemle konuşmak istiyorum.` |

Hem `<Label>` hem `<Text>` etiketlerini aynı şekilde güncelleyin.

---

## Sık Sorulan Sorular

**S: Türkçe karakterler (ş, ğ, ü, ö, ı, ç) doğru görünmüyor.**
C: XML dosyaları UTF-8 kodlamasıyla kaydedilmiştir. Düzenleme sonrası Notepad++'da "Farklı Kaydet" → "UTF-8 (BOM'suz)" seçerek kaydedin.

**S: Klavye açılmıyor / dosya bulunamıyor hatası.**
C: Tüm dosyaların aynı klasörde olduğunu ve dosya adlarının değiştirilmediğini kontrol edin. Klasör yolunda Türkçe karakter olmamasına dikkat edin.

**S: Ses çıkmıyor.**
C: Management Console → Sounds sekmesinde (Konuşma/Speech bölümü) Türkçe ses yüklü ve seçili mi kontrol edin. Windows'ta Türkçe TTS sesi kurulu değilse önce onu yükleyin.

**S: Yanlış tuş seçimi yapılıyor.**
C: Hedefler zaten büyük ve büyüteç açık olduğundan, seçim dwell ile değil PARMAK DÜĞMESİYLE yapılır — yani salt bakışla yanlışlıkla seçilmez. Kalibrasyon sorunuysa göz izleme cihazını yeniden kalibre edin. Parmak düğmesi ayarı için `01-optikey-kurulum-ayar-rehberi.md`'deki Pointing & Selecting profilini gözden geçirin.

---

## Notlar

- Tüm XML dosyaları OptiKey Dynamic Keyboard şemasına uygundur
- Her tuş `<Text>` ile cümleyi scratchpad'e yazar, `<Action>Speak</Action>` ile TTS seslendirir
- Klavyeler arası geçiş `<ChangeKeyboard>` ile dosya adı (uzantısız) referansı kullanılarak yapılır
- Ana menüde 4x3 grid, kategori sayfalarında 4x2 grid kullanılmıştır (büyük hedef alanlar)

---

Bu rehber bilgilendirme amaçlıdır; klinik karar için uzmana danışın.
