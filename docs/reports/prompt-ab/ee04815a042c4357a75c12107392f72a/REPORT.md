# Canlı prompt A/B — gerçek sağlayıcı yanıtları

6 sentetik vaka × 2 gerçek prompt × 2 model. Sonuçlar önceden yazılmış değildir.
V2, V1'e kategori/öncelik iş kurallarını ekler. Aynı girdiler ve aynı değerlendirici kullanılır.
Küçük örneklem; tek tekrar; sağlayıcı varsayılan sıcaklığı. Genel model sıralaması yapılmaz.
Maliyet bilinmiyor; real etiketi SDK beyanıdır. Ham cevaplar saklanmaz.

| Model | Prompt | Geçti / değerlendirilen | Değerlendirilmeyen | CLIENT p50 |
|---|---|---:|---:|---:|
| openai/gpt-oss-20b | v1 | 3/6 | 0/6 | 444.2 ms |
| openai/gpt-oss-20b | v2 | 5/6 | 0/6 | 452.9 ms |
| gemini-3.1-flash-lite | v1 | 3/6 | 0/6 | 2744.4 ms |
| gemini-3.1-flash-lite | v2 | 5/6 | 0/6 | 2835.3 ms |

## Vaka bazında izlenebilirlik

| Model | Vaka | Prompt | Sonuç | Başarısız kurallar | Trace |
|---|---|---|---|---|---|
| openai/gpt-oss-20b | t01 | v1 | passed | — | [394f73bb](http://127.0.0.1:3001/traces/394f73bb199eef7e85505d5dd5d4f191?project=1) |
| openai/gpt-oss-20b | t01 | v2 | passed | — | [70186c3f](http://127.0.0.1:3001/traces/70186c3ff50c9afd5e60cc6c6216303a?project=1) |
| openai/gpt-oss-20b | t02 | v2 | failed | expected_category | [ef12ef93](http://127.0.0.1:3001/traces/ef12ef939968ca00ced6a1a6bab29be6?project=1) |
| openai/gpt-oss-20b | t02 | v1 | failed | expected_category, expected_priority | [271965bc](http://127.0.0.1:3001/traces/271965bcc96b48c3fe3f0f10978a0b13?project=1) |
| openai/gpt-oss-20b | t03 | v1 | failed | expected_priority | [bcf56175](http://127.0.0.1:3001/traces/bcf56175e53be90de1050260451915ab?project=1) |
| openai/gpt-oss-20b | t03 | v2 | passed | — | [2746e87a](http://127.0.0.1:3001/traces/2746e87a66f6fe58f17e40e68ab2cf0e?project=1) |
| openai/gpt-oss-20b | t04 | v2 | passed | — | [1b850630](http://127.0.0.1:3001/traces/1b850630d23322f8e2824ee3e370b3b1?project=1) |
| openai/gpt-oss-20b | t04 | v1 | passed | — | [9468eed8](http://127.0.0.1:3001/traces/9468eed85fa2eb7aa93205e5d3b3b1ad?project=1) |
| openai/gpt-oss-20b | t05 | v1 | passed | — | [541dbd30](http://127.0.0.1:3001/traces/541dbd30787f61fc8ac5a81906bacd32?project=1) |
| openai/gpt-oss-20b | t05 | v2 | passed | — | [38b45f03](http://127.0.0.1:3001/traces/38b45f0393c2bdb1d6b4f569e2bc96f2?project=1) |
| openai/gpt-oss-20b | t06 | v2 | passed | — | [25c00366](http://127.0.0.1:3001/traces/25c003668326ab6777806bcb5ddb8fff?project=1) |
| openai/gpt-oss-20b | t06 | v1 | failed | expected_priority | [556fd442](http://127.0.0.1:3001/traces/556fd442d6a8328ee9675eeb207241e5?project=1) |
| gemini-3.1-flash-lite | t01 | v1 | passed | — | [56194630](http://127.0.0.1:3001/traces/56194630487be60ddbd1e79d764dc74a?project=1) |
| gemini-3.1-flash-lite | t01 | v2 | passed | — | [efb3cd92](http://127.0.0.1:3001/traces/efb3cd925c11babac4cb246df7634422?project=1) |
| gemini-3.1-flash-lite | t02 | v2 | failed | expected_category | [de9485d4](http://127.0.0.1:3001/traces/de9485d432fec85806bcd6e6af15fc90?project=1) |
| gemini-3.1-flash-lite | t02 | v1 | failed | expected_category, no_markdown | [9a526e9f](http://127.0.0.1:3001/traces/9a526e9f85ce3d2a7abe7b566826be1e?project=1) |
| gemini-3.1-flash-lite | t03 | v1 | failed | no_markdown | [46ad411a](http://127.0.0.1:3001/traces/46ad411ad063f8994f89f0f5df036b02?project=1) |
| gemini-3.1-flash-lite | t03 | v2 | passed | — | [62e3a1c8](http://127.0.0.1:3001/traces/62e3a1c88f6f5d748e2bd038d765b4d8?project=1) |
| gemini-3.1-flash-lite | t04 | v2 | passed | — | [10bcb0f4](http://127.0.0.1:3001/traces/10bcb0f4674dd4c8b468c03194068b79?project=1) |
| gemini-3.1-flash-lite | t04 | v1 | passed | — | [0453e9f6](http://127.0.0.1:3001/traces/0453e9f653875dae52e57e1e89fbd9b1?project=1) |
| gemini-3.1-flash-lite | t05 | v1 | failed | no_markdown | [b9679f0f](http://127.0.0.1:3001/traces/b9679f0f86a6bed1408056b109513428?project=1) |
| gemini-3.1-flash-lite | t05 | v2 | passed | — | [20b8c0a4](http://127.0.0.1:3001/traces/20b8c0a47f1dc195cb3a26ab47a3d5da?project=1) |
| gemini-3.1-flash-lite | t06 | v2 | passed | — | [94ba83a9](http://127.0.0.1:3001/traces/94ba83a936845608b2dfad58fe7f7b85?project=1) |
| gemini-3.1-flash-lite | t06 | v1 | passed | — | [36134720](http://127.0.0.1:3001/traces/361347207064b6576c7b5bd013a49f3f?project=1) |

Tam eşleşmiş kanıt: 24/24

Promptlar ve referans etiketler: `demo_app/prompt_ab_scenarios.py`.
Eski golden mock sonuçları bu rapora dahil değildir.
