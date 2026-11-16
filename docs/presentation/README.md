# LLM Insight — 10 slaytlık proje sunumu

**13 Kasım güncellemesi:** mevcut 24 çağrı artık [Experiments](http://127.0.0.1:3001/experiments?project=1)
altında iki tamamlanmış canlı karşılaştırma olarak görüntülenebilir. V4 PDF/PPTX bu arayüz
entegrasyonundan önce üretildiği için “ayrı rapor / web mock” ve canlı arayüz yol haritası
ifadeleri o sürümün tarihsel durumudur. Güncel gösterim adımları `CANLI-AB-ANLATIM.md` içindedir.

- **[LLM-Insight-Sunum-v4.pdf](LLM-Insight-Sunum-v4.pdf)**: güncel sürüm; 16:9, paylaşım ve sunum için hazır, 10 sayfa.
- **[LLM-Insight-Sunum-v4.pptx](LLM-Insight-Sunum-v4.pptx)**: güncel sürüm; düzenlenebilir metin, tablo ve mimari şekilleri; 10 slayt. Ekran görüntüleri ve logolar raster görseldir.
- **LLM-Insight-Onizleme.png**: tüm slaytların önizlemesi.
- **KONUSMACI-NOTLARI.md**: anlatım notları, kaynaklar ve kapsam açıklamaları. Aynı notlar PowerPoint konuşmacı notlarına da eklenmiştir.
- **[CANLI-AB-ANLATIM.md](CANLI-AB-ANLATIM.md)**: slayt 7 için kısa konuşma metni, olası sorular ve mevcut kayıtlarla canlı gösterim bağlantıları.

## Kapsam

PagePilot'un kısa teknik akışı ve EchoNote'un problem–çözüm–mimari–model–arayüz yaklaşımı incelenerek LLM Insight'a uyarlandı. Referans dokümanların içeriği, yeni işlem talimatı olarak uygulanmadı. Özgün İTO (sol üst) ve Softito (sağ üst) logoları EchoNote PDF'inden çıkarıldı; her slaytta kullanıldı.

Ana tasarım beyaz, mavi ve turuncudur; yüksek kontrastlı metin ve sakin vurgu renkleri kullanılmıştır. Logoların ve özgün arayüz görüntülerinin renkleri korunmuştur. Sunum yaklaşık 7–9 dakikalık anlatıma uygundur.

## Güncel kanıt

V4, kullanıcının onayladığı 24 gerçek sağlayıcı çağrısı tamamlandıktan sonra güncellendi.
Ekranlar 13 Kasım 2026 Türkiye saatiyle çalışan yerel dashboard'dan alındı; ekran alma işlemi yalnızca okumadır.
Ana ölçüm penceresi Project 1, 12 Kasım 2026 UTC günü, `provenance=real` filtresidir.
Bu etiket, istemci beyanıdır; bağımsız sağlayıcı kanıtı değildir.

- 84 çağrı / 84 trace; 3 teknik hata (%3,57).
- GPT-OSS 35, Qwen 21, Gemini 28 çağrı; sayılar birden fazla senaryoyu kapsar.
- 84 çağrının maliyeti bilinmiyor. Bilinmeyen maliyet sıfır veya ücretsiz olarak gösterilmedi.
- Canlı A/B: iki model × altı sentetik vaka × iki gerçek prompt = 24 çağrı. Bu 24 çağrı, 84'ün içindedir.
- Her iki modelde V1 **3/6**, V2 **5/6** geçti; sekiz sabit kural, 24/24 doğrulanmış trace, teknik hata yok.
- Groq'ta öncelik kuralları; Gemini'de biçim kuralları iyileşti. Gemini'nin kategori/öncelik eşleşmesi iki sürümde de 5/6; toplam puan artışı aynı tür iyileşme anlamına gelmez.
- Fatura vakasında billing/bug tanımları örtüşebilir. Referans etikete uymama, tek başına model kusurunu kanıtlamaz; puanlar deneyden sonra değiştirilmedi.
- Altı vaka ve tek tekrar genellenemez. Canlı A/B ayrı CLI/rapor akışıdır; web Experiments ve eski golden sonuçları hâlâ mock'tur.

[Canlı deney raporu ve vaka başına trace bağlantıları](../reports/prompt-ab/ee04815a042c4357a75c12107392f72a/REPORT.md).
[Uygulama ve doğrulama raporu](../reports/live-prompt-ab-20261113.md).
Model karşılaştırma ekranı yalnızca deneyin zaman aralığıyla açıldığında dört adet altışar çağrılık grup gösterir:
adresler `live-capture-urls.json` içinde. Kalite puanları ayrı deney raporundadır.

Önceki v3 dosyaları korunmuştur; 60 çağrılık gözlem ve mock Experiment #58 o sürümün tarihsel içeriğidir.
“Farkımız:” ve kaldırılması istenen teşekkür cümlesi v4'te de yoktur.

Güncel kaynak sayfaların metni `live-*-browser.txt`, görüntüleri `assets/live-*-full.png`, adres ve yerleşim bilgileri `live-capture-manifest.json`, kırpım bilgileri `live-crop-manifest.json` içindedir. Sunumdaki görüntüler yalnızca kırpılmıştır; sayısal içerik değiştirilmemiştir. Slayt 6'daki tarihsel hata kaydının özgün ekranı korunmuştur.

Rakip konumlandırması için resmî kaynaklar:

- https://langfuse.com/docs
- https://docs.langchain.com/langsmith/evaluation
- https://arize.com/docs/phoenix

Bu karşılaştırma, diğer platformlarda belirli bir özellik bulunmadığı iddiası taşımaz; LLM Insight'ın uyguladığı tasarım tercihlerini ve sınırlarını açıklar.

## Çıktı kontrolü

- PDF 10 sayfa, PPTX 10 slayt; her slaytta iki köşe logosu ve konuşmacı notu.
- PDF sayfaları görsel olarak incelendi; görsel–dipnot çakışmaları giderildi.
- Metin kutularında ölçülen taşma yok; PPTX'te sayfa dışına çıkan nesne yok.
- Türkçe karakterleri destekleyen Segoe UI fontları PDF'e gömülü.
- PowerPoint dosyası yapısal olarak doğrulandı. PowerPoint uygulaması içinde ayrıca render edilmedi; başka bilgisayarlarda font farklılığına karşı PDF sabit görünüm sunar.

`build_deck.py`, aynı yerleşim tanımından PDF ve PPTX üretir; `capture_ui.py` yerel çalışan dashboard'u ayrı, başlıksız Edge profili ile okur. Yeniden ekran almak için açık yerel sunucu ve tarayıcı başlatma yetkisi gerekir. `inspect_assets.py`, referans PDF'in özgün yerel yolunu kullanır.

V4 dosyalarını mevcut kanıttan yeniden üretmek için (sağlayıcı çağrısı yapmaz):

```powershell
python docs/presentation/build_deck.py --output-stem LLM-Insight-Sunum-v4 --ab-report docs/reports/prompt-ab/ee04815a042c4357a75c12107392f72a/report.json --observation docs/presentation/live-observation.json
```

V4 kontrol kaydı: `v4-verification.json`. 10 sayfa/slayt, slayt başına iki özgün logo ve
konuşmacı notu; sıfır ölçülen metin taşması ve sayfa dışı nesne. PDF'in on sayfası görsel olarak incelendi.
