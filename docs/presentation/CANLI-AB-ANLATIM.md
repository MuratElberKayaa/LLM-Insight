# Slayt 7 için kısa anlatım

“Prompt değişikliğini gerçekten ölçmek için aynı altı sentetik destek talebini iki farklı
talimatla Groq GPT-OSS 20B ve Gemini Flash Lite'a gönderdik. V1 kısa bir sınıflandırma
talimatıydı. V2'de kategori tanımlarını, işin etkisine göre öncelik kurallarını ve çıktı
biçimini netleştirdik. Toplam 24 gerçek çağrı yaptık; hepsini aynı sekiz kuralla değerlendirdik.

İki modelde de tüm kuralları geçen vaka sayısı 3'ten 5'e çıktı. Ancak artışın nedeni aynı
değildi: Groq'ta öncelik kurallarına uyum, Gemini'de JSON dışı biçimlendirmeden kaçınma
iyileşti. Bu, aynı puanın arkasında farklı nedenler olabileceğini gösteriyor.

Altı vaka ve tek tekrar genel bir üstünlük kanıtlamaz. Ayrıca fatura vakasında kategori
tanımları örtüşebiliyor. LLM Insight ile göstermek istediğimiz, yalnızca puan vermek değil;
kararın hangi kayda ve kurala dayandığını, nerede belirsizlik kaldığını görünür kılmak.”

## Sorulursa

**Neden eski golden-v1/v2 kullanılmadı?** Eski `golden-prompt-v1/v2`, mock deneyde önceden
tanımlı yanıtları seçen kimliklerdir. Sağlayıcıya giden iki ayrı prompt metni değildir.
Bu deney için gerçek metinler `live-ticket-v1/v2` olarak tanımlandı; isimleri de mock
sonuçlarla karışmayacak şekilde ayrı tutuldu.

**5/6 ne demek?** Altı vakadan beşi; çıktı biçimi, alanlar, izinli değerler, beklenen
kategori ve önceliği kapsayan sekiz kontrolün tamamından geçti. Genel doğruluk oranı değildir.
Gemini'nin yalnızca kategori+öncelik eşleşmesi iki sürümde de 5/6'dır.

**Çağrılar hata verdi mi?** Bu 24 çağrıda teknik hata yok. Kural kontrolünden kalmak,
API çağrısının başarısız olmasıyla aynı şey değildir. Genel dashboard'daki üç teknik hata
bu deneyden öncedir. Deneyin 24 çağrısı, gün toplamı 84 çağrıya zaten dahildir.

**Maliyet ne kadar?** Hesapların ücretsiz planı kullanıcı tarafından doğrulandı; fakat
telemetride bu çağrılara uygulanabilir doğrulanmış fiyat yok. Bu nedenle arayüz maliyeti
“bilinmiyor” gösterir; sıfır tutarlı fatura iddiası yapmıyoruz.

## Kısa canlı gösterim

1. [Experiments ekranını aç](http://127.0.0.1:3001/experiments?project=1).
   Üstteki **Completed live comparisons** bölümünde iki modelin V1/V2 puanlarını göster.
2. [Groq](http://127.0.0.1:3001/experiments/live/2?project=1) veya
   [Gemini](http://127.0.0.1:3001/experiments/live/1?project=1) ayrıntısını aç.
   Bir geçen/kalan vakadaki kural sonucunu, token/süre tablosunu ve trace bağlantısını göster.
3. Trace sayfasında model, prompt sürümü, zaman çizelgesi ve kullanım kaydını göster.
   Ham yanıt telemetride saklanmaz; kalite kontrolleri ayrı rapordadır.

Yerel backend ve dashboard açık olmalıdır. Canlı sonuçlar tamamlanmış kayıtlardan okunur;
**New mock experiment** düğmesi eski mock akışına aittir. Gösterim için deneyi tekrar
çalıştırmaya gerek yoktur. Ham yanıtlar tutulmadığı için kural sonuçları kayıtlı değerlendiriciden
aktarılmıştır; sunucu trace ve ölçümleri bağımsız olarak doğrulamıştır.
