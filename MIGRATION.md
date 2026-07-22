# Eski `memory_service.py` — Emekliye Ayrıldı

## Ne değişti

Projenin ilk halindeki `memory_service.py`, her sohbetin **ham mesajlarını**
SQLite'a yazıp son 12 tanesini modele bağlam olarak veriyordu.

Bu dosya artık **kullanılmıyor**. Yerini `app/core/` altındaki yapılandırılmış
hafıza sistemi aldı.

## Neden

Tasarım dokümanının en temel kuralı şuydu:

> Hafıza sistemi konuşmaları saklamamalı. Konuşmalardan **çıkarılan bilgiyi**
> saklamalı.

Eski yaklaşımın üç sorunu vardı:

1. **Ham konuşma saklıyordu.** Bilgi çıkarımı yoktu; model her seferinde aynı
   sohbet metnini yeniden okumak zorundaydı.
2. **Kalıcı değildi.** Ephemeral disk üzerinde çalıştığı için her yeniden
   başlatmada hafıza sıfırlanıyordu.
3. **Ölçeklenmiyordu.** Pencere sabit 12 mesajdı; daha eskisi tamamen
   kayboluyordu ve alaka gözetilmiyordu.

## Yerine ne geçti

| Eski davranış | Yeni karşılığı |
|---|---|
| Ham mesajları sakla | `evaluation.py` — 7 soruluk değerlendirme, yapılandırılmış bilgi çıkarımı |
| Son 12 mesajı ver | `retrieval.py` — semantik arama + metadata re-rank |
| Sabit pencere | `injection.py` — öncelik sırasına göre dinamik bağlam |
| Sınırsız büyüme | `reflection.py` + `decay.py` — konsolidasyon ve unutma |
| Ephemeral SQLite | Supabase Postgres + pgvector (kalıcı) |

## Mevcut veriyi taşımak isterseniz

Eski SQLite dosyasındaki sohbet logu **otomatik taşınmaz** — bu kasıtlıdır, çünkü
ham konuşma saklama felsefesine aykırıdır. İsterseniz eski logu tek seferlik
okuyup `MemoryManager.evaluate_and_store()` üzerinden geçirebilirsiniz; böylece
konuşmalar ham haliyle değil, çıkarılmış bilgi olarak sisteme girer.

## Dosyayı silmeli miyim?

Evet. Yeni mimaride hiçbir yerden import edilmiyor. Geçmiş referansı için
tutmak isterseniz bir sorun çıkarmaz, ancak `main.py` artık onu kullanmıyor.
