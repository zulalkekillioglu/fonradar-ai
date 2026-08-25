"""
FonRadar AI - Skorlama Modulu (tek asamali)
===========================================
Semantik aramadan gelen (dedup edilmis) fonlarin TAM metnini alir, kullanicinin
serbest metin sorgusuyla karsilastirir ve TEK LLM cagrisinda hem alanlari cikarir
hem skoru verir. Iki ayri cagri (once ozetle, sonra skorla) YAPMAZ.

LangChain'in with_structured_output'u sayesinde cikti dogrudan Pydantic modeline
dogrulanir; elle json.loads / try-except gerekmez.

Kullanim:
    from llm.skorlama import fonlari_skorla
    sonuclar = fonlari_skorla(skorlanacak, sorgu)   # skora gore sirali liste
"""
from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field
from llm.llm import get_llm
import time
from datetime import date
from tenacity import retry, stop_after_attempt, wait_random_exponential, RetryError

MAKS_KARAKTER = 15000


class FonSkoru(BaseModel):
    """Bir fonun kullanici sorgusuna gore skoru ve cikarilan alanlar."""
    skor: int = Field(
        ge=0,
        le=100,
        description="Fonun kullanici ihtiyacina uygunlugu (0-100 arasi hassas tam sayi)",
    )
    aciklama: str = Field(
        description="Fonun kimligi, sehir ve konu uyumunu kapsayan detayli gerekce ve aciklama metni"
    )
    fon_id: Optional[str] = Field(
        default=None,
        description="Fon başlığından veya metinden çıkarılan fon kodu/id'si (Örn: '1832', '1501', '1507'). Bulunamazsa null bırak."
    )
    sehir_durumu: Literal["uyuyor", "ulusal", "uymuyor", "bilgi_yok"] = Field(
        description="Sorgudaki sehir fona uyuyor mu (ozel sehirse 'uyuyor', tum Turkiye ise 'ulusal', uymuyorsa 'uymuyor', veri yoksa 'bilgi_yok')"
    )
    konu: str = Field(
        description="Fonun metinden cikarilan ana odagi veya konusu (kisa ve oz)"
    )
    son_basvuru: Optional[str] = Field(
        default=None,
        description="Fonun son basvuru tarihi (metinde acikca varsa tarih yazilir, yoksa null)",
    )
    hibe_orani: Optional[str] = Field(
        default=None,
        description="Fonun hibe/destek orani veya bütce limiti (metinde acikca varsa yazilir, yoksa null)",
    )
    suresi_gecti: bool = Field(
        default=False,
        description="Sana verilen BUGUNUN_TARIHI, son_basvuru tarihinden SONRAYSA true; son_basvuru yoksa veya gecmemisse false"
    )

PROMPT_SABLONU = """Sen uzman bir hibe ve fon analistisin. Kullanıcının ihtiyacı ile sana sağlanan fon metnini kıyaslayıp dinamik, esnek ve son derece hassas bir uygunluk değerlendirmesi yapacaksın.

BUGUNUN_TARIHI: {bugun}

KULLANICI SORGUSU:
{sorgu}

FON BAŞLIĞI: {baslik}
FON METNİ:
{metin}

DİNAMİK VE HASSAS SKORLAMA METODOLOJİSİ:

[Adım 1: Taban Puanı Belirle (0 - 100)]
- 90 - 100: Fon metninde kullanıcının konusu "özel çağrı", "öncelikli alan" veya "hedef sektör" olarak BİREBİR/AÇIKÇA geçiyorsa.
- 70 - 89: Fon genel bir Ar-Ge/teknoloji şemsiye desteğiyse ve kullanıcının konusunu dolaylı da olsa kapsıyorsa.
- 40 - 69: Fon kısmen alakalıysa veya ağır katılım/ortaklık şartları barındırıyorsa.
- 1 - 39: Fon kullanıcının konusuyla çok az veya zayıf bir şekilde ilişkiliyse.
- 0: Kullanıcı konusuyla, sektörüyle veya ölçeğiyle tamamen alakasızsa.

[Adım 2: Makro/Mikro Düzeltme Puanları (ZORUNLU UYGULA)]
Aşağıdaki durumları tespit et ve taban puana ekle/çıkar. Kesinlikle 70, 80, 85 gibi standart yuvarlak sayılarda takılı kalma (Örn: 67, 73, 81, 92 gibi hassas puanlar üret):
- Çıkarılacak Puanlar (Kararlılıkla KES):
  * Fon genel bir destek ama kullanıcının konusuna ÖZEL BİR ÖNCELİK tanımıyorsa: (-5 ile -12 puan)
  * Müşteri/ortak bulma, ağır eş-finansman veya zorlu ön şartlar varsa: (-3 ile -10 puan)
  * Kullanıcının ölçeği veya aşaması (örn. fikir aşaması vs. seri üretim) tam örtüşmüyorsa: (-4 ile -8 puan)
- Eklenen Puanlar (Avantajlar):
  * Yüksek hibe oranı (%70 ve üzeri), KOBİ dostu şartlar, esnek takvim veya başvuru kolaylığı: (+2 ile +5 puan)

DETAYLI ALAN KURALLARI:
1. aciklama: Açıklamayı mutlaka şu akışla 2-4 cümle olarak yaz:
   - Önce fonsal kimliği ve kapsamı belirt (Örn: "Bu fon [Fon Başlığı], Türkiye genelinde / [Şehir] bölgesinde geçerli bir destektir.").
   - Ardından kullanıcının şehri ve konusuyla uyumunu/uyumsuzluğunu nedenleriyle açıkla.
   - METİNDE KESİNLİKLE "puan kestim", "taban puan", "bonus ekledim" gibi teknik değerlendirme terimleri KULLANMA.
2. fon_id: Metne veya başlığa gömülü olan fon ID'sini çıkar (Örn: "1832 - Sanayide Yeşil Dönüşüm" -> "1832"). Yoksa null bırak.
3. sehir_durumu:
   - Kullanıcının şehri fon hedeflerinde/metninde açıkça varsa -> 'uyuyor'
   - Fon ulusal/genel bir kapsama sahipse (tüm Türkiye) -> 'ulusal'
   - Fon doğrudan farklı bir coğrafyaya/şehre özelse -> 'uymuyor'
   - Metinde şehir bilgisi yoksa veya sorguda şehir belirtilmediyse -> 'bilgi_yok'
4. konu: Fonun metinden çıkarılan ana odağı/konusu (kısa ve öz, maks 4-5 kelime).
5. son_basvuru: Metinde GERÇEKTEN geçen son başvuru tarihi (Format varsa: DD.MM.YYYY veya metindeki halı), yoksa null.
6. hibe_orani: Metinde GERÇEKTEN geçen hibe oranı veya bütçe limiti, yoksa null.
7. suresi_gecti: BUGUNUN_TARIHI ile son_basvuru tarihini KARŞILAŞTIR.
   - son_basvuru metinde varsa VE bugünün tarihi son_basvuru tarihinden SONRAYSA (yıl-ay-gün bazlı) -> true
   - son_basvuru yoksa (null) VEYA henüz geçmemişse -> false
   - Sadece YIL ve AY bazlı kaba karşılaştırma yeterli, gün hassasiyeti gerekmez.
ÇIKTI KISITLAMALARI (ÇOK KRİTİK):
- Yanıtlarında yalnızca sağlanan metindeki gerçek verilere dayan, asla dışarıdan varsayımda bulunma.
- Markdown bloğu (```json ... ``` veya ```), açıklama, giriş veya sonuç yazısı KESİNLİKLE KULLANMA.
- Cevabın istisnasız doğrudan {{ sembolü ile başlayan ve }} sembolü ile biten geçerli (valid) bir JSON objesi olmalıdır.

JSON Formatı:
{{
  "skor": 73,
  "aciklama": "Bu fon...",
  "fon_id": "1832",
  "sehir_durumu": "ulusal",
  "konu": "Yapay Zeka ve Yazılım",
  "son_basvuru": null,
  "hibe_orani": "%75",
  "suresi_gecti": false
}}

"""

_yapili_llm = None


def _get_yapili_llm():
    global _yapili_llm
    if _yapili_llm is None:
        _yapili_llm = get_llm().with_structured_output(FonSkoru, method="json_mode")
    return _yapili_llm


def _metni_hazirla(fon: dict) -> str:
    metin = fon.get("full_text", "") or ""
    if len(metin) > MAKS_KARAKTER:
        metin = metin[:MAKS_KARAKTER] + "\n...[metin kisaltildi]"
    return metin


# Rate Limit yediğinde pes etmeyip 2sn, 4sn, 8sn bekleyerek 3 kez tekrar dener
@retry(wait=wait_random_exponential(min=2, max=10), stop=stop_after_attempt(3))
def fonu_skorla(fon: dict, sorgu: str) -> FonSkoru:
    """Tek fonu tek LLM cagrisinda skorlar, FonSkoru doner."""
    prompt = PROMPT_SABLONU.format(
        sorgu=sorgu.strip(),
        baslik=fon.get("baslik", ""),
        metin=_metni_hazirla(fon),
        bugun=date.today().strftime("%d.%m.%Y"),   # YENİ: sunucu tarihi HER cagrida taze hesaplanir
    )
    return _get_yapili_llm().invoke(prompt)



def fonlari_skorla(fonlar: list[dict], sorgu: str) -> list[dict]:
    sonuclar = []
    for i, fon in enumerate(fonlar, 1):
        baslik = fon.get("baslik", "?")
        print(f"[SKOR] ({i}/{len(fonlar)}) {baslik}")

        try:
            skor = fonu_skorla(fon, sorgu)

            # YENİ: son basvuru tarihi gecmis fonlar sonuca HIC eklenmiyor
            if skor.suresi_gecti:
                print(f"  ⏰ [SÜRESİ GEÇMİŞ] Atlandi: {baslik} (son_basvuru: {skor.son_basvuru})")
            else:
                sonuclar.append({
                    **skor.model_dump(),
                    "baslik": baslik,
                    "url": fon.get("url"),
                })

            if i < len(fonlar):
                print("⏳ Token limitini aşmamak için 10 saniye bekleniyor...")
                time.sleep(10)

        except RetryError as e:
            gercek_hata = e.last_attempt.exception()
            print(f"\n🚨 [API HATASI YAKALANDI] - {baslik} : {e}\n ")
            raise

        except Exception as e:
            print(f"\n🚨 [BEKLENMEYEN HATA] {baslik}: {e}\n")
            raise

    sonuclar.sort(key=lambda x: x["skor"], reverse=True)
    return sonuclar