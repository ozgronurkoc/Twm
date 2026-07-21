"""ai_service.py
=============
Kullanıcının serbest metin mesajını bir dil modeline gönderip, yapılması gereken
görev işlemini (ekle / tamamla / iptal / not_ekle / listele) YAPISAL ve
DOĞRULANMIŞ bir biçimde geri döndürür.

Genel akış:
    interpret_message("bugün markete gitmem lazım")
    -> GorevKomutu(islem=Islem.EKLE, gorev_metni="markete gitmek", tarih=Tarih.BUGUN)

Öne çıkan özellikler:
    * Pydantic ile tip + iş kuralı doğrulaması.
    * Ağ / API hataları için otomatik yeniden deneme ve net istisnalar.
    * Model anlamsız/eksik çıktı ürettiğinde çökmek yerine 'belirsiz'e düşme.
    * 'bugün'/'yarın' -> gerçek `date` dönüşümü (veritabanı için hazır).
    * Test edilebilirlik için enjekte edilebilir client.
    * `history` parametresiyle önceki konuşma turlarını bağlam olarak alma.

Gereksinim: pydantic>=2 (güncel `openai` sürümleri zaten bunu kurar).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from enum import Enum
from typing import Optional

from openai import OpenAI, OpenAIError
from pydantic import BaseModel, ValidationError, field_validator, model_validator

import config
from prompt import SYSTEM_PROMPT

logger = logging.getLogger(__name__)

# --- Ayarlar (config'ten override edilebilir) ------------------------------
MODEL = getattr(config, "OPENAI_MODEL", "gpt-4o-mini")
REQUEST_TIMEOUT = getattr(config, "OPENAI_TIMEOUT", 30.0)
MAX_RETRIES = getattr(config, "OPENAI_MAX_RETRIES", 3)


# --- İstisnalar ------------------------------------------------------------
class AIServiceError(RuntimeError):
    """Dil modeli çağrısı kurtarılamaz şekilde başarısız olduğunda fırlatılır."""


# --- Alan tipleri ------------------------------------------------------------
class Islem(str, Enum):
    EKLE = "ekle"
    TAMAMLA = "tamamla"
    IPTAL = "iptal"
    NOT_EKLE = "not_ekle"
    LISTELE = "listele"
    BELIRSIZ = "belirsiz"


class Tarih(str, Enum):
    BUGUN = "bugün"
    YARIN = "yarın"

    def to_date(self, *, bugun: Optional[date] = None) -> date:
        """Göreceli tarihi gerçek bir `date` nesnesine çevirir."""
        base = bugun or date.today()
        return base + timedelta(days=1) if self == Tarih.YARIN else base


# Bu işlemler için `gorev_metni` mutlaka bulunmalı.
_GOREV_METNI_GEREKLI = {Islem.EKLE, Islem.TAMAMLA, Islem.IPTAL, Islem.NOT_EKLE}


class GorevKomutu(BaseModel):
    """Modelin çıktısının doğrulanmış, tip güvenli hali."""

    islem: Islem
    gorev_metni: Optional[str] = None
    not_metni: Optional[str] = None
    tarih: Tarih = Tarih.BUGUN
    yanit: Optional[str] = None

    @field_validator("gorev_metni", "not_metni", "yanit", mode="before")
    @classmethod
    def _bosluklari_temizle(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        # Baştaki/sondaki ve tekrarlanan boşlukları sadeleştir; boşsa None yap.
        v = " ".join(str(v).split())
        return v or None

    @model_validator(mode="after")
    def _is_kurallarini_dogrula(self) -> GorevKomutu:
        if self.islem in _GOREV_METNI_GEREKLI and not self.gorev_metni:
            raise ValueError(f"'{self.islem.value}' işlemi için 'gorev_metni' gerekli.")
        if self.islem == Islem.NOT_EKLE and not self.not_metni:
            raise ValueError("'not_ekle' işlemi için 'not_metni' gerekli.")
        return self

    @property
    def hedef_tarih(self) -> date:
        """Görevin ait olduğu gerçek takvim tarihi."""
        return self.tarih.to_date()


# --- Model / tool tanımı ---------------------------------------------------
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "gorev_islemi",
            "description": "Kullanıcının mesajından çıkarılan görev yönetimi işlemi",
            "parameters": {
                "type": "object",
                "properties": {
                    "islem": {
                        "type": "string",
                        "enum": [e.value for e in Islem],
                        "description": (
                            "ekle: yeni görev eklenecek. "
                            "tamamla: bir görev yapıldı olarak işaretlenecek. "
                            "iptal: bir görev iptal edildi olarak işaretlenecek. "
                            "not_ekle: bir göreve serbest not eklenecek. "
                            "listele: kullanıcı listeyi görmek istiyor. "
                            "belirsiz: hiçbiri net değilse."
                        ),
                    },
                    "gorev_metni": {
                        "type": "string",
                        "description": (
                            "İşlemin uygulanacağı görevin kısa metni "
                            "(ekle/tamamla/iptal/not_ekle için gerekli)"
                        ),
                    },
                    "not_metni": {
                        "type": "string",
                        "description": "Sadece 'not_ekle' işleminde: eklenecek notun içeriği",
                    },
                    "tarih": {
                        "type": "string",
                        "enum": [t.value for t in Tarih],
                        "description": "Görevin hangi güne ait olduğu. Belirtilmemişse 'bugün' varsay.",
                    },
                    "yanit": {
                        "type": "string",
                        "description": (
                            "Kullanıcıya gösterilecek, samimi ve doğal Türkçe bir sohbet cevabı. "
                            "Asla robotik veya parametre diliyle yazılmaz."
                        ),
                    },
                },
                "required": ["islem", "yanit"],
            },
        },
    }
]


# --- Client (tembel oluşturulur, testte enjekte edilebilir) ----------------
_client: Optional[OpenAI] = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            timeout=REQUEST_TIMEOUT,
            max_retries=MAX_RETRIES,
        )
    return _client


# --- Ana giriş noktası ------------------------------------------------------
def interpret_message(
    user_text: str,
    *,
    history: Optional[list[dict]] = None,
    client: Optional[OpenAI] = None,
) -> GorevKomutu:
    """Serbest metni doğrulanmış bir `GorevKomutu`'na çevirir.

    `history` verilirse (OpenAI mesaj formatında, [{"role": ..., "content": ...}])
    önceki konuşma turları da modele bağlam olarak gönderilir; böylece agent
    önceki mesajları hatırlıyormuş gibi doğal bir sohbet akışı kurabilir.

    Fırlatabileceği hatalar:
        ValueError      -- `user_text` boşsa.
        AIServiceError  -- API'ye ulaşılamazsa veya model yapısal yanıt vermezse.

    Model geçerli ama anlamsız/eksik bir çıktı üretirse (ör. metinsiz "ekle"),
    çökmek yerine `islem=belirsiz` döndürülür; böylece bot kullanıcıdan
    netleştirme isteyebilir.
    """
    if not user_text or not user_text.strip():
        raise ValueError("user_text boş olamaz.")

    client = client or _get_client()

    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    if history:
        messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    try:
        response = client.chat.completions.create(
            model=MODEL,
            temperature=0,  # çıkarım işi -> deterministik olsun
            messages=messages,
            tools=TOOLS,
            tool_choice={"type": "function", "function": {"name": "gorev_islemi"}},
        )
    except OpenAIError as exc:
        logger.exception("OpenAI çağrısı başarısız oldu.")
        raise AIServiceError("Dil modeli servisine ulaşılamadı.") from exc

    message = response.choices[0].message
    tool_calls = message.tool_calls
    if not tool_calls:
        logger.error("Beklenen tool_call dönmedi. content=%r", message.content)
        raise AIServiceError("Model yapısal bir yanıt döndürmedi.")

    raw_arguments = tool_calls[0].function.arguments
    try:
        komut = GorevKomutu.model_validate_json(raw_arguments)
    except (ValidationError, ValueError, TypeError) as exc:
        logger.warning("Model çıktısı doğrulanamadı (%s). Ham veri: %s", exc, raw_arguments)
        return GorevKomutu(
            islem=Islem.BELIRSIZ,
            yanit="Tam anlayamadım, biraz daha açar mısın?",
        )

    logger.debug("Yorumlanan komut: %s", komut)
    return komut


# --- Hızlı manuel test ------------------------------------------------------
# NOT: Bu blok gerçek API çağrısı yapar (ücret/kota harcar).
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ornekler = [
        "bugün markete gitmem lazım",
        "raporu yazdım",
        "toplantı notu: saat 3'e çekildi",
        "yarınki listeyi göster",
        "hava bugün çok güzel",  # belirsiz beklenir
    ]
    for metin in ornekler:
        komut = interpret_message(metin)
        print(
            f"{metin!r:45} -> {komut.islem.value:9} | "
            f"gorev={komut.gorev_metni!r} | tarih={komut.hedef_tarih} | yanit={komut.yanit!r}"
)
