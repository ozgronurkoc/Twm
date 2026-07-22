# Tasarım Dokümanı → Kod Kapsam Matrisi

`Memory Inspiration and Design Principles` dokümanındaki **her madde** ve kod
karşılığı. Doğrulama sütunu, o davranışı sınayan testi gösterir.

---

## İlham kaynakları

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| ChatGPT: otomatik hafıza çıkarımı, elle kaydetmeye güvenme | `core/evaluation.py`, `core/pipeline.py` | `test_phase2` |
| ChatGPT: her gelen mesaj analiz edilir | `pipeline.handle()` → `evaluate_and_store` | `test_phase2` |
| ChatGPT: ham konuşma değil, yapısal hafıza nesnesi | `evaluation.py` (üçüncü-tekil dönüşüm) | `test_phase2` (ham mesaj saklanmadı) |
| ChatGPT: tüm DB'yi değil, semantik getirmeyle ilgili olanları yükle | `core/retrieval.py` | `test_phase3`, `test_phase4` |
| ChatGPT: bağlamı yanıt üretiminden önce enjekte et | `core/injection.py` | `test_phase3` |
| OpenClaw: insan-okunur, gizli olmayan hafıza | `core/transparency.py` (Markdown) | `test_phase8` |
| OpenClaw: incele / dışa aktar / yedekle / düzenle / taşı | `transparency.py`, `commands.py`, `jobs.job_backup` | `test_phase8` |
| OpenClaw: uzun vadeli konsolidasyon | `core/reflection.py` | `test_phase6` |
| OpenClaw: anlamı koru, boyutu küçült | `reflection.py` + `decay.py` | `test_phase6` |
| Claude Projects: kalıcı bağlamsal bilgi | `core/persistent_context.py` | `test_phase4` |
| Claude Projects: kalıcı bilgi ≠ deneyim | `is_persistent` + ayrı enjeksiyon bloğu | `test_phase4` (alakadan bağımsız yükleme) |

---

## Hafıza katmanları

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| Katmanlar bağımsız, tek vektör DB'ye karıştırma yasak | Katman başına ayrı tablo + type-scoped arama | `migrations/001_init.sql`, `test_phase1` |
| Layer 1 Identity — en yüksek önem, hiç sonlanmaz | `mem_identity`, `ALWAYS_LOADED_TYPES` | `test_phase6` (koruma) |
| Identity: sağlık bilgisi yalnızca açık rızayla | `prompts.SYSTEM_PROMPT_EVAL` güvenlik bölümü | — |
| Layer 2 Preference — yeni eskiyi ezer, tekrar olmaz | `dedup.py` SUPERSEDE dalı | `test_phase5` |
| Layer 3 Episode — olaylar, sonra özetlenir | `mem_episode` → `reflection.py` | `test_phase6` |
| Layer 4 Knowledge — RAG | `mem_knowledge` + semantik getirme | `test_phase3` |
| Layer 5 Task — operasyonel | `infra/notion/notion_repo.py` | `test_phase1` |
| Layer 6 Reflection — otomatik üretilir | `reflection.py` + worker | `test_phase6` |

---

## Pipeline ve değerlendirme

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| 7 adımlı hat (intent → … → response) | `core/pipeline.py` | `test_phase2`, `test_phase9` |
| Ham mesaj asla doğrudan yazılmaz | `evaluate_and_store` yalnızca `MemoryDraft` yazar | `test_phase2` |
| 7 içsel değerlendirme sorusu | `evaluation.EvaluationResult` alanları | `test_phase2` |
| Hafıza yaratımı bir AI muhakemesidir, DB insert değil | `evaluation.evaluate()` | `test_phase2` |
| Her hafıza TAM 1 kategori; kategorisiz yasak | `MemoryType` + DB CHECK kısıtı | `migrations/001_init.sql` |

---

## Şema ve metadata

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| 17 zorunlu alan | `core/models.Memory` | `test_phase8` (export alan kontrolü) |
| importance / confidence 0.0–1.0 | Pydantic + DB CHECK | `test_phase8` |
| accessCount her getirmede artar | `Memory.touch_accessed()` | `test_phase3` |
| lastAccessed enjeksiyonda güncellenir | `build_context()` | `test_phase3` |
| relatedMemoryIds ilişki kurar | `core/graph.py` | `test_phase7` |

---

## Getirme ve enjeksiyon

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| embedding → ara → sırala → metadata re-rank | `retrieval.rerank()` | `test_phase3` |
| Re-rank sinyalleri: importance, recency, frequency, confidence | `RetrievalConfig` ağırlıkları | `test_phase3` |
| Yalnızca en iyileri enjekte et, tüm DB'yi asla | `min_similarity` + `min_final_score` eşikleri | `test_phase4` |
| Enjeksiyon öncelik sırası | `injection.build_context_string()` | `test_phase3` |
| Minimum gerekli bağlam | katman kotaları + `guardrails.truncate_context` | `test_phase9` |

---

## Konsolidasyon, unutma, graf

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| Günlük → haftalık → aylık → çeyreklik | `reflection.ReflectionLevel` zinciri | `test_phase6` |
| Üst seviye alt seviyeden beslenir | `level.source_level` | `test_phase6` |
| Unutma sinyalleri (importance/access/lastAccessed) | `decay.DecayConfig` | `test_phase6` |
| Önce arşivle, sonra sil | `decay_and_archive()` iki aşama | `test_phase6` |
| Identity neredeyse hiç silinmez | `protected_types` + hard-delete koruması | `test_phase6`, `test_skeleton` |
| Çakışmada confidence/timestamp/benzerlik karşılaştır | `dedup.decide()` | `test_phase5` |
| update / merge / supersede | `resolve_conflict()` | `test_phase5` |
| Çakışan kalıcı hafızalar bir arada tutulmaz | SUPERSEDE dalı | `test_phase5` |
| Hafızalar birbirine referans versin | `graph.link()` (çift yönlü) | `test_phase7` |
| Getirme graf gezinmesinden de yararlansın | `expand_by_graph()` | `test_phase7` |

---

## Altyapı

| Doküman maddesi | Kod | Doğrulama |
|---|---|---|
| Memory Manager tek giriş noktası | `core/memory_manager.py` | tüm testler |
| Hiçbir bileşen DB'ye doğrudan erişmez | `bot/`, `workers/` yalnızca manager çağırır | kod incelemesi |
| Dependency inversion | `domain/` ports + `factory.py` | `test_skeleton` |
| DB / vektör / embedding / LLM değiştirilebilir | `config` + `infra/` adapters | `test_phase8` (taşıma) |
| Vendor lock-in yok | export'ta embedding taşınmaz | `test_phase8` |
| Zamanlanmış worker'lar, isteklerden bağımsız | `workers/scheduler.py`, `worker.py` | `test_phase6` |
| 6 arka plan işi | `workers/jobs.py` (+ yedekleme = 7) | `test_phase6` |

---

## Tasarım hedefi

> "Hafıza zamanla küçülsün, zenginleşsin ve akıllansın — sonsuza dek büyümesin."

- **Küçülme:** `decay.py` (arşivle → sil) + `reflection.py` (N kayıt → 1 özet)
- **Zenginleşme:** `graph.py` (bağlantılar), `dedup.py` (birleştirme)
- **Akıllanma:** re-rank sinyalleri kullanım verisiyle beslenir (`access_count`,
  `last_accessed`), böylece sık kullanılan hafızalar öne çıkar.
