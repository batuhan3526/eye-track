# Ergonomi / Işık / Webcam Kontrol Listesi

## Neden Önemli?

Standart webcam tabanlı göz takibi, kızılötesi sistemlere kıyasla yaklaşık 2.5° doğruluk hatasına sahiptir ve ışık koşulları, gözlük yansımaları, baş-kamera mesafesi ile kamera açısı bu hatayı birkaç katına çıkarabilir. Geç evre ALS/MND'de pitozis (göz kapağı sarkması) ve kuru göz ek doğruluk kaybına yol açar; bu nedenle tutarlı, sabit bir kurulum ve önden sabit aydınlatma, başarılı göz takibinin temel koşullarıdır. Kalibrasyon her zaman bu sabit koşullar sağlandıktan sonra yapılmalıdır; aksi hâlde kalibrasyon değerleri oturumdan oturuma geçersiz hâle gelir.

---

## Kontrol Listesi

### Kamera Konumu ve Görüş Alanı

- [ ] **Webcam yüzü önden ve net görüyor:** Kamera tam karşıya bakmalı; yatay veya dikey açı 15°'yi geçmemeli. Eğik açılarda göz kasırgası (gaze yönü) hesaplama hatası büyür.

- [ ] **Webcam monitöre sabit şekilde bağlı, sallanmıyor:** Kamera titremesi veya sürüklenme, her kareyi farklı referans noktasıyla işleterek tutarsız izlemeye yol açar.

- [ ] **Göz-kamera mesafesi tutarlı ve 50–75 cm arasında:** Çok yakın mesafe dar görüş alanı, çok uzak mesafe düşük piksel çözünürlüğü demektir; her oturumda aynı mesafe korunmalıdır.

- [ ] **Kamera, gözlerin hizasında ya da en fazla 5–10 cm altında konumlu:** Kamera çok aşağıda olursa göz kapakları kısmi kapanmış görünür; bu pitozis belirtilerini taklit eder ve doğruluğu düşürür.

### Ekran ve Baş Pozisyonu

- [ ] **Ekran üst kenarı göz hizasında ya da hafif altında:** Ekranı yukarıdan aşağı bakarak kullanmak pupilla görüşünü artırır; ekran çok yukarıda olursa göz kapakları göz bebeğini kısmen örter.

- [ ] **Baş desteği (koltuk başlığı, yastık, boyun desteği) mevcut ve sabitlenmiş:** Baş salınımı göz takibinin en büyük hata kaynaklarından biridir; başın sabit durması koordinat tutarlılığını doğrudan sağlar.

- [ ] **Pitozis varsa, kamera açısı ve ekran yüksekliği buna göre ayarlandı:** Düşük göz kapağı, kameranın üst açıdan baktığı konfigürasyonlarda iris görünürlüğünü ciddi ölçüde azaltır; kamera daha aşağıya alınarak iris açıklığı maksimize edilmeli.

### Işık Koşulları

- [ ] **Önden sabit, yayılmış aydınlatma var (pencereye veya lambaya karşı değil):** Arkadan veya yandan gelen ışık yüzde gölge ve yansıma yaratır; algoritma iris sınırlarını yanlış tespit eder.

- [ ] **Arka ışık (pencere, spot) yok ya da stor/perde ile engellendi:** Parlak arka plan, kameranın pozlamasını ayarlamasına neden olur ve yüz karanlıkta kalır.

- [ ] **Işık kaynağı gün içinde değişmiyor ya da yapay ışıkla sabitleniyor:** Güneş ışığı gün boyunca yer değiştirir; tutarsız ışık her saat farklı bir görüntü kalitesi üretir ve kalibrasyon değersiz hâle gelir.

- [ ] **Kuru göz için yapay göz damlası hazır (gerekliyse kullanıcı için işaret edildi):** Kuru kornea ışığı düzensiz yansıtır; kornea yansıması bozulunca göz takip algoritmaları merkez tespitinde yanılır.

### Gözlük ve Yansıma

- [ ] **Gözlük yansıması kontrol edildi; gerekiyorsa mat ekran filmi veya ışık açısı değiştirildi:** Gözlük camlarındaki yansıma, iris üzerinde sahte parlak nokta oluşturur ve algoritmanın pupil merkezini kaydırmasına neden olur.

### Kalibrasyon

- [ ] **Kalibrasyon, yukarıdaki tüm koşullar sağlandıktan sonra yapıldı:** Işık, mesafe veya baş pozisyonu değişmeden önce yapılan kalibrasyon oturumu geçersiz kabul edilmelidir.

- [ ] **Her yeni oturumda hızlı yeniden kalibrasyon protokolü uygulandı:** Sandalye pozisyonu ya da ışık koşulu bile küçük değişse, tam kalibrasyon yerine en az 5 noktalı hızlı kalibrasyon tekrarlanmalıdır.

---

## Tamlık Kontrolü (İç Denetim)

| Risk Faktörü | Kapsayan Madde |
|---|---|
| Işık koşulları | Önden sabit aydınlatma; arka ışık yok; gün içi değişkenlik |
| Pitozis (göz kapağı sarkması) | Kamera açısı ve ekran yüksekliği pitozise göre ayarlandı |
| Göz-kamera mesafesi | 50–75 cm tutarlı mesafe |
| Kamera açısı | Önden ve net; yatay/dikey 15° sınırı |
| Baş desteği | Baş desteği mevcut ve sabitlenmiş |
| Gözlük yansıması | Yansıma kontrol edildi |
| Kuru göz | Yapay göz damlası hazır |
| Kalibrasyon tutarlılığı | Sabit koşullarda kalibrasyon; her oturumda yenileme |

---

Bu rehber bilgilendirme amaçlıdır; klinik karar için uzmana danışın.
