# LLM-Insight bitirme raporu

- `LLM-Insight-Bitirme-Raporu.pdf`: sabit sayfa düzeninde teslim dosyası.
- `LLM-Insight-Bitirme-Raporu.docx`: düzenlenebilir Word dosyası.
- `rapor.md`: Türkçe metin, tablolar, kaynaklar ve görsel referansları.
- `Onizleme.png`: PDF sayfalarının toplu önizlemesi.
- `dogrulama.json`: başlık/sayfa eşleşmesi, şekil ve kod sayısı, görsel kaynak özetleri.
- `build_report.py`: yerel PDF/DOCX üreticisi; sağlayıcı çağrısı yapmaz.

Referans PPE raporunun altı ana bölümü ve 19 alt başlığı LLM-Insight bağlamına uyarlandı. A4, 2,5 cm kenar boşlukları, 12 punto Times New Roman gövde, 15/13 punto mavi başlıklar, gri kod kutuları ve sayfa numaraları korundu. PDF içindekiler sayfa numaraları otomatik üretilir; Word içindekiler başlık listesi olarak düzenlenebilir.

Kapaktaki grup/dönem bilgisi örnek rapordan alındığı belirtilerek aktarıldı; bu kullanıcıya ait ayrıca doğrulanmış bir kayıt değildir. Proje sahibi Murat Elber Kaya'dır. Uygulama kanıtı 13 Kasım 2026 çalışma ağacı ve mevcut raporlardır. Tarihsel test sonuçları yeni çalıştırma olarak sunulmamıştır.

PDF metni, Türkçe karakterler, sayfa sınırları, başlıklar ve kaynak bağlantıları kontrol edilir. DOCX yapısal olarak oluşturulur; Microsoft Word içinde ayrıca render edilmemiştir. Word sürümü ve yazı tipi ortamına göre sayfa sonları değişebilir; sabit görünüm için PDF kullanılmalıdır.

Yeniden üretim: depo kökünden `python docs/bitirme-raporu/build_report.py`. Yerelde ReportLab, python-docx, Pillow ve PyMuPDF ile Windows Times New Roman/Consolas fontları gerekir. Ekran görüntüleri mevcut `docs/presentation/assets` ve `docs/reports/phase-5b-screenshots` klasörlerinden okunur.
