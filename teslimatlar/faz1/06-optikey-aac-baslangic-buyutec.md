# 06 — OptiKey: AAC'yi Başlangıç Klavyesi Yapma + Büyüteç Ayarı

**Süre:** ~5 dakika · **Gereken:** OptiKey Pro kurulu, `C:\Users\Public\OptiKey-AAC\` dolu

> Not: Sürücünün kendi **F5 AAC ızgarası** artık aynı cümleleri daha iyi bir sesle
> konuşuyor. Bu ayar, OptiKey'i klavye/kelime tahmini için de kullanmaya devam
> edenler içindir; ikisi aynı XML dosyalarını okur.

## A. AAC'yi başlangıç klavyesi yapma

1. OptiKey simgesine sağ tık → **Management Console** (Yönetim Konsolu)
2. Sol menüden **Keyboards** (Klavyeler)
3. **Startup Keyboard** açılır listesinden **Dynamic** seçin
4. **Dynamic keyboards folder** alanına yazın: `C:\Users\Public\OptiKey-AAC`
5. **Startup dynamic keyboard file** alanına: `turkce-aac-paketi.xml`
6. **Save** → OptiKey'i kapatıp yeniden açın

**Doğrulama:** OptiKey açılışta doğrudan "Türkçe AAC - Ana Menü" ızgarasıyla gelmeli
(ACIL/SAGLIK, TEMEL IHTIYAC, KONFOR/BAKIM ... kutuları görünür).

## B. Büyüteç ayarı

1. Management Console → **Visuals** (Görseller)
2. **Magnify** / büyüteç bölümünde:
   - **Magnification source area %** → `10`
   - **Magnification destination area %** → `60`
3. **Save**

Bu, küçük hedeflere (tarayıcı bağlantıları, menü öğeleri) tıklarken ekranın %10'unu
%60'lık bir alana büyütür. Sürücünün **F6** büyüteci ile aynı işi yapar; hangisi daha
rahat geliyorsa o kullanılır.

## C. Geri alma

Başlangıç klavyesini eskiye döndürmek için: Keyboards → **Startup Keyboard** →
**Alpha** (veya daha önce kullanılan klavye) → Save.

## D. Bilinen sınırlar

**1. Ses.** OptiKey'in `Speak` eylemi Windows **SAPI** seslerini kullanır; sürücünün
yerel sinir-ağı sesine (piper) erişemez. Windows'ta çoğu zaman **Türkçe SAPI sesi yoktur** —
o zaman OptiKey'den okutulan Türkçe cümleler İngilizce aksanla çıkar. Daha doğal ve doğru ses
isteniyorsa cümleyi sürücünün **F5** ızgarasından seçin.

**2. İki ızgara aynı dosyaları okur, ama aynı şeyi göstermez.** Sürücünün F5 ızgarası
şu iki tuşu **göstermez**:

- `KLAVYE` (OptiKey'i alfabe klavyesine geçirir) ve `Ana Menü` (OptiKey menüsü) —
  sürücü OptiKey'in klavyesini değiştiremez, bu kutular seçilse yalnızca "KLAVYE"
  kelimesini sesli okurdu.
- Tahtaların kendi `Geri / Ana Menü` tuşu — sürücü her alt tahtaya kendi
  `<< GERI` kutusunu **hep aynı köşeye** koyar. Göz kontrolünde bir kutunun **yeri**
  onu bulma yöntemidir; iki farklı yerde iki geri yolu olması işe yaramaz.

Bu ayıklama yalnızca sürücünün ızgarasındadır; OptiKey'de tuşlar olduğu gibi durur.

**3. Cümle ekleme/değiştirme.** XML dosyalarını düzenlemek her ikisini birden
günceller. Sürücü tarafında değişikliğin görünmesi için sürücüyü yeniden başlatın
(cümleler açılışta okunur ve önceden seslendirilir).

Yeni cümle eklerken kalıbı koruyun:

```xml
<DynamicKey Row="0" Col="0" Width="1" Height="1"
            BackgroundColor="#1A5276" ForegroundColor="#FFFFFF">
  <Label>Kısa etiket</Label>
  <Action>ClearScratchpad</Action>
  <Text>Sesli okunacak tam cümle.</Text>
  <Action>Speak</Action>
</DynamicKey>
```

`<Text>` yoksa `<Label>` okunur. Köşeli parantezli bir metin (`[Aile Adı]` gibi)
**doldurulmamış şablon** sayılır: sürücü o kutuyu gri ve **seçilemez** gösterir, böylece
odadaki kişiye şablonun kendisi okunmaz. Gerçek adı yazınca kutu kendiliğinden açılır.
