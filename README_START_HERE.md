# LLM Insight — Başlangıç

Bu paket, önceki sohbetin son **Architecture v1.1** kararlarını, `birlesmis_transkript.txt` ve Perplexity derin araştırma dosyasını temel alan **revize MVP uygulama planıdır**. v1.1 özgün Markdown içeriği bu sohbette bulunmadığı için birebir yeniden üretildiği iddia edilmemektedir.

## Projeye kopyalanacak yapı

```text
llm-insight/
  CLAUDE.md                          ← bu paketteki CLAUDE.md
  docs/
    MVP_MASTER_PLAN.md              ← bu paketteki MVP_MASTER_PLAN.md
    CLAUDE_PHASE_PROMPTS.md         ← bu paketteki CLAUDE_PHASE_PROMPTS.md
```

## Sıra

1. VS Code'da `llm-insight` proje klasörünü aç.
2. Üç dosyayı gösterilen yerlere kopyala. Mevcut repo varsa dosyaları körü körüne ezme; farkları incele.
3. Claude Code içinde `CLAUDE_PHASE_PROMPTS.md` dosyasının **Bootstrap** ve **Phase 0** talimatlarını sırayla kullan. Birinci koşuda sadece Phase 0 gerçekleştirilmelidir.
4. Phase 0 raporunu, gerçek repo dosyalarını ve test çıktılarını gözden geçirip onayla.
5. Ardından Phase 1, 2, 3, 4, 5 talimatlarını **birer birer** gönder; sonraki faza başlamadan önce çıkış kriterlerini kontrol et.

İlk hedef: gerçek protobuf trace alımı → Postgres → bağımsız Python SDK → Next.js dashboard → Failure Review → insan onaylı test seti → prompt sürümü kıyası.

**Dikkat:** Burada bir ürün veya kod tabanı üretilmedi. Bunlar uygulanmaya hazır plan, mimari ve prompt dosyalarıdır. Belirtilen tarihler hedef, 'PASS' iddiası değildir.
