# 🎯 Tümünü Dene — Birleşik Kurulum (Faz 1 + Faz 2)

Kas erimesi olan, yalnızca **göz + parmak** kullanan kullanıcı için, **sadece webcam** ile çalışan tam sistemin uçtan-uca kurulumu. İlke: **"Gözle nişan al, parmakla ateşle."**

> Sıra önemli: önce OptiKey (klavye/konuşma), sonra webcam göz-sürücüsü (imleç), en son birlikte deneme.

## Adımlar

**1) OptiKey'i kur ve Türkçe ayarla.**
→ `faz1/01-optikey-kurulum-ayar-rehberi.md`
Özellikle: **dwell KAPALI**, **Key selection source = parmak düğmesi** (gamepad/klavye tuşu), **büyüteç AÇIK**, Türkçe arayüz/klavye/ses.

**2) Türkçe AAC hazır-cümle paketini içe aktar.**
→ `faz1/02-aac-paketi-iceaktarma.md` (XML'ler: `faz1/turkce-aac-*.xml`)
Tek bakış + parmak basışıyla tam cümle yazar ve **Türkçe seslendirir**.

**3) Parmak düğmesini bağla ve test et.**
Ucuz USB gamepad düğmesi veya klavye tuşu. OptiKey'de `TestControllers.exe` ile tanıt. (Bu pakette **dwell yok** — seçim yalnız parmakla.)

**4) Python + sürücü bağımlılıklarını kur.**
```bash
cd gaze
pip install -r requirements.txt
```

**5) Webcam göz-sürücüsünü çalıştır ve kalibre et.**
```bash
python gaze_driver.py
```
Ekrandaki 9 yeşil noktaya sırayla bak. → `gaze/README.md`

**6) OptiKey'i sürücüye bağla.**
OptiKey → ALT+M → Pointing & Selecting → `Source = "Mouse Position"`. Artık OptiKey, sürücünün sürdüğü imleci okur.

**7) DENE (tümü birlikte).**
Gözünle bak → **parmak düğmesiyle seç** → OptiKey yazar/konuşur. Küçük hedefte: bak → parmak (büyüteç) → bak → parmak (tıkla).

**8) Gözler yorulursa: switch-tarama yedeği.**
→ `faz1/03-switch-tarama-yedek-profili.md` — aynı parmak düğmesiyle, **gözsüz** tam erişim.

**9) Konfor/doğruluk için ergonomi.**
→ `faz1/04-ergonomi-isik-webcam-kontrol-listesi.md` — ışık, mesafe, pitozis, baş desteği.

## Çalışmazsa (hızlı kontrol)

| Belirti | Bak |
|---|---|
| İmleç hiç oynamıyor | `gaze/README.md` → webcam/`camera_index`; sürücü çalışıyor mu |
| İmleç var ama seçmiyor | OptiKey Key selection source = parmak düğmesi mi (dwell kapalı) |
| İmleç titriyor/geç | Ayarlar → İmleç → **İmleç hassasiyeti** (ince ayar: `config.yaml` → `mincutoff` / `beta`) |
| Cümle yazılmıyor/konuşmuyor | `faz1/02` içe aktarma + Türkçe TTS sesi kurulu mu |
| Gözler yetmiyor | `faz1/03` switch-tarama yedeğine geç |

## Mimari (hatırlatma)

```
webcam → gaze_driver.py (imleci sürer) → OptiKey "Mouse Position" okur
                                              ↑ parmak düğmesi = seç/tıkla
                                         OptiKey: klavye + AAC + Türkçe TTS + büyüteç
```
Sürücü **tıklamaz**; tıklama parmağa aittir. Bu yüzden webcam'in düşük doğruluğu yorgunluk yaratmaz.

---
*Bu rehber bilgilendirme amaçlıdır; klinik karar için uzmana danışın.*
