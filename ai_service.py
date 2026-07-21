"""
Kullanıcının serbest metin mesajını OpenAI'a gönderip,
yapılması gereken işlemi (ekle / tamamla / iptal / not / listele)
yapısal bir şekilde geri alıyoruz.
"""

import json
from openai import OpenAI
import config

client = OpenAI(api_key=config.OPENAI_API_KEY)

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
                        "enum": ["ekle", "tamamla", "iptal", "not_ekle", "listele", "belirsiz"],
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
                        "description": "İşlemin uygulanacağı görevin kısa metni (ekle/tamamla/iptal/not_ekle için gerekli)",
                    },
                    "not_metni": {
                        "type": "string",
                        "description": "Sadece 'not_ekle' işleminde: eklenecek notun içeriği",
                    },
                    "tarih": {
                        "type": "string",
                        "enum": ["bugün", "yarın"],
                        "description": "Görevin hangi güne ait olduğu. Belirtilmemişse 'bugün' varsay.",
                    },
                },
                "required": ["islem"],
            },
        },
    }
]

SYSTEM_PROMPT = """Sen bir kişisel görev asistanısın. Kullanıcı Türkçe yazacak,
sen onun mesajını yapılacaklar listesi yönetimi için doğru işleme çevireceksin.

Örnekler:
"bugün markete gitmem lazım" -> ekle, gorev_metni="markete gitmek", tarih="bugün"
"yarın doktora gitmem lazım" -> ekle, gorev_metni="doktora gitmek", tarih="yarın"
"markete gitme işi iptal oldu" -> iptal, gorev_metni="markete gitmek"
"raporu yazdım" -> tamamla, gorev_metni="rapor yazmak" (mesajdaki fiile en yakın görev metnini bulmaya çalış)
"toplantı notu: saat 3'e çekildi" -> not_ekle, gorev_metni="toplantı", not_metni="saat 3'e çekildi"
"bugünkü listeyi göster" -> listele, tarih="bugün"

gorev_metni'ni her zaman kısa ve database'de aranabilir bir şekilde yaz (örn. sadece anahtar kelimeler)."""


def interpret_message(user_text: str) -> dict:
    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_text},
        ],
        tools=TOOLS,
        tool_choice={"type": "function", "function": {"name": "gorev_islemi"}},
    )

    tool_call = response.choices[0].message.tool_calls[0]
    args = json.loads(tool_call.function.arguments)
    args.setdefault("tarih", "bugün")
    return args
