# Twm — Uzun Vadeli Hafıza Sistemi

Telegram üzerinden çalışan, doğal dille görev yöneten kişisel asistan; artık
**aylar/yıllar boyunca kullanıcıyı hatırlayan** bir hafıza mimarisiyle.

Bu depo, `Memory Inspiration and Design Principles` tasarım dokümanının
**tamamını** (Faz 0 → 9) uygular.

---

## Temel ilke

> Hafıza sistemi konuşmaları saklamaz. Konuşmalardan **çıkarılan bilgiyi** saklar.

Her mesaj değerlendirilir, hatırlanmaya değer bilgi yapılandırılmış bir hafıza
nesnesine dönüştürülür, sınıflandırılır, indekslenir; sonraki mesajlarda yalnızca
**ilgili** olanlar bağlama enjekte edilir. Eskiyenler özetlenerek sıkıştırılır,
işe yaramayanlar unutulur.

---

## Mimari

```
app/
  bot/        Telegram Chat Engine (yalnızca Telegram'a özgü işler)
  core/       İş mantığı — sağlayıcıdan BAĞIMSIZ
    memory_manager.py      >>> TEK GİRİŞ NOKTASI <<<
    pipeline.py            mesaj işleme hattı
    intent.py              niyet tespiti
    evaluation.py          7 soruluk hafıza değerlendirmesi
    retrieval.py           semantik arama + metadata re-rank
    injection.py           öncelikli bağlam kurma
    dedup.py               çakışma çözümü
    reflection.py          konsolidasyon (günlük→çeyreklik)
    decay.py               unutma / arşivleme
    graph.py               knowledge graph
    persistent_context.py  kalıcı bağlam kaynakları
    transparency.py        export / import / inceleme
    guardrails.py          hız sınırı, token bütçesi
  domain/     ARAYÜZLER (ports): repository, vector_store, embeddings, llm, tasks
  infra/      IMPLEMENTASYONLAR (adapters): postgres, sqlite, pgvector, openai, notion
  workers/    zamanlanmış arka plan işleri
  factory.py  dependency injection
```

**Değişmez kural:** `core/` asla `infra/`'yı doğrudan import etmez; yalnızca
`domain/` arayüzlerini bilir. Somut sınıflar sadece `factory.py`'de bilinir.
Bu sayede DB, vektör DB, embedding ve LLM sağlayıcıları iş mantığına
dokunmadan değiştirilebilir.

### Altı hafıza katmanı

| Katman | Ne saklar | Getirme |
|---|---|---|
| 1 — Identity | Neredeyse hiç değişmeyen kimlik bilgisi | Her zaman yüklenir |
| 2 — Preference | Asistan nasıl davranmalı | Her zaman yüklenir |
| 3 — Episode | Önemli olaylar | Semantik |
| 4 — Knowledge | Kullanıcıya ait bilgi (RAG) | Semantik (+ kalıcı olanlar her zaman) |
| 5 — Task | Aktif görevler | **Notion** (Memory Manager üzerinden) |
| 6 — Reflection | Otomatik üretilen özetler | Semantik |

### Mesaj işleme hattı

```
Mesaj
 -> Hız sınırı / kırpma
 -> Komut mu? (evetse LLM'e hiç gitmez)
 -> Retrieval + Injection  (kimlik -> tercih -> görev -> bilgi -> olay -> özet -> bağlantılı)
 -> Intent Detection
 -> Görev işlemi (Notion)
 -> YANIT  ---------------------------> kullanıcıya
 -> [arka plan] Evaluation -> Classification -> Dedup -> Storage -> Embedding -> Index
```

Değerlendirme yanıt yolundan ayrıdır: her mesajda çalışır ama gecikmeyi artırmaz.

---

## Kurulum

### 1) Bağımlılıklar
```bash
pip install -r requirements.txt
```

### 2) Ortam değişkenleri
```bash
cp .env.example .env
```
`.env` içinde en kritik alan `DATABASE_URL` — **Nonplo'dan ayrı, yeni bir
Supabase projesinin** bağlantı adresi (Supabase -> Project Settings -> Database
-> Connection string / URI).

### 3) Şema
Supabase panelinde **SQL Editor**'ı açıp `migrations/001_init.sql` içeriğini
çalıştır. Alternatif:
```bash
psql "$DATABASE_URL" -f migrations/001_init.sql
```

### 4) Sağlık kontrolü
```bash
python main.py --health
```

### 5) Çalıştırma
```bash
python main.py     # bot
python worker.py   # arka plan bakım işleri (AYRI process)
```

Tek dyno ile çalışmak zorundaysan `.env` içine `RUN_SCHEDULER_IN_BOT=true`
ekleyerek zamanlayıcıyı bot process'i içinde de başlatabilirsin.

### Postgres olmadan denemek (yerel dev)
```bash
# .env içinde:
DB_PROVIDER=sqlite
VECTOR_PROVIDER=inmemory   # gerçek cosine ile semantik dev
```

---

## Kullanıcı komutları

**Kalıcı bağlam** (her sohbette geçerli)
- `/kural <metin>` · `/proje <metin>` · `/dokuman <metin>` · `/prompt <metin>`
- `/kalici` — listele · `/kalici_sil <no>` — kaldır

**Hafıza şeffaflığı**
- `/hafiza` — ne biliyorum? (istatistik)
- `/hafiza <kelime>` — ara ve numaralı listele
- `/duzenle <no> <metin>` — düzelt · `/hafiza_sil <no>` — unut
- `/disaktar` — JSON yedek · `/disaktar md` — okunur döküm

---

## Arka plan işleri

`worker.py` içinde APScheduler ile dönen 9 iş: embedding backfill, günlük /
haftalık / aylık / çeyreklik reflection, duplicate detection, decay + arşivleme,
graf bakımı, yedekleme.

```bash
python worker.py --once   # tüm bakım turunu elle bir kez çalıştır
```

---

## Testler

Hiçbiri dış servis gerektirmez (sahte LLM / Notion / embedder kullanılır):

```bash
python -m tests.test_skeleton   # Faz 0: iskelet + DI
python -m tests.test_phase1     # Faz 1: katmanlar + Notion görev akışı
python -m tests.test_phase2     # Faz 2: mesaj hattı
python -m tests.test_phase3     # Faz 3: getirme + re-rank + enjeksiyon
python -m tests.test_phase4     # Faz 4: kalıcı bağlam
python -m tests.test_phase5     # Faz 5: dedup + çakışma çözümü
python -m tests.test_phase6     # Faz 6: reflection + decay + worker'lar
python -m tests.test_phase7     # Faz 7: knowledge graph
python -m tests.test_phase8     # Faz 8: şeffaflık (export/import/taşıma)
python -m tests.test_phase9     # Faz 9: guardrail'ler + uçtan uca
```

---

## Sağlayıcı değiştirme

`.env` üzerinden; iş mantığına dokunmadan:

| Ne | Değişken | Seçenekler |
|---|---|---|
| Veritabanı | `DB_PROVIDER` | `postgres`, `sqlite` |
| Vektör | `VECTOR_PROVIDER` | `pgvector`, `inmemory`, `none` |
| Embedding | `EMBEDDING_MODEL` + `EMBEDDING_DIM` | OpenAI modelleri |
| LLM | `OPENAI_MODEL` | OpenAI modelleri |

**Embedding boyutu değişirse** vektör tabloları yeniden oluşturulmalı ve
embedding'ler yeniden üretilmelidir. Kolay yolu: `/disaktar` ile yedek al,
şemayı güncelle, `import_json` ile geri yükle — embedding'ler dışa
aktarılmadığı için otomatik olarak yeni sağlayıcıyla üretilir.

---

## Notlar

- Eski `memory_service.py` **emekliye ayrıldı** — bkz. `MIGRATION.md`.
- Sağlık bilgisi yalnızca kullanıcı açıkça paylaşmak isterse hafızaya alınır.
- Identity, Preference ve kalıcı bağlam **asla** otomatik silinmez.
- `PDF_COVERAGE.md` — tasarım dokümanındaki her maddenin kod karşılığı.
