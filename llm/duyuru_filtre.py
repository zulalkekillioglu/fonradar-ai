"""
FonRadar AI - Duyuru Filtreleme Modulu
=======================================
esnafkoop.ticaret.gov.tr/duyurular listesindeki TUM basliklari TEK (ya da
az sayida) LLM cagrisinda "fon/destek ilani mi, degil mi" diye siniflandirir.
Boylece fon olmayan haberler (bulten, kutlama, yarisma, mevzuat vs.)
fonlar.json'a hic girmez, gereksiz detay sayfasi + PDF kazimasi yapilmaz.
"""
from __future__ import annotations

from typing import List
from pydantic import BaseModel, Field
from llm.llm import get_llm
from tenacity import retry, stop_after_attempt, wait_random_exponential, RetryError
import time

PROMPT_SABLONU = """Sen bir kamu kurumu duyuru sayfasini siniflandiran uzman bir analistsin.
Asagida ID:Baslik formatinda bir duyuru listesi var. Her biri icin, bu duyurunun
isletmelere/kooperatiflere/esnafa yonelik bir HİBE, DESTEK, FON veya BAŞVURU
(finansal destek programi) ilani olup olmadigina karar ver.

KURALLAR:
- Hibe/destek dagitan, "KOOP-DES", "hibe", "destek programi", "basvuru duyurusu"
  gibi finansal destek icerigi olan basliklar -> fon_mu: true
- Bulten yayimi, kutlama, yarisma sonucu, egitim/yonetmelik/mevzuat/tebliğ
  duyurulari, genel bilgilendirme gibi finansal destek icermeyen basliklar
  -> fon_mu: false
- Sadece basliktan anlasilan acik bilgiye dayan, spekulasyon yapma.

DUYURU BAŞLIKLARI:
{liste}

Cevabin SADECE asagidaki JSON formatinda olsun, baska hicbir aciklama ekleme:
{{"kararlar": [{{"id": 0, "fon_mu": true}}, {{"id": 1, "fon_mu": false}}]}}
"""


class DuyuruKarari(BaseModel):
    id: int
    fon_mu: bool = Field(description="Bu duyuru bir hibe/destek/fon basvuru ilani mi")


class SiniflandirmaSonucu(BaseModel):
    kararlar: List[DuyuruKarari]


_yapili_llm = None


def _get_yapili_llm():
    global _yapili_llm
    if _yapili_llm is None:
        _yapili_llm = get_llm(temperature=0).with_structured_output(
            SiniflandirmaSonucu, method="json_mode"
        )
    return _yapili_llm

@retry(wait=wait_random_exponential(min=2, max=10), stop=stop_after_attempt(3))
def _siniflandir_toplu(basliklar: list[str]) -> list[bool]:
    liste_metni = "\n".join(f"{i}: {b}" for i, b in enumerate(basliklar))
    prompt = PROMPT_SABLONU.format(liste=liste_metni)

    print(f"    [LLM] {len(basliklar)} baslik gonderiliyor, yanit bekleniyor...")
    baslangic = time.time()

    sonuc: SiniflandirmaSonucu = _get_yapili_llm().invoke(prompt)

    gecen = round(time.time() - baslangic, 1)
    print(f"    [LLM] Yanit geldi ({gecen} sn).")

    karar_map = {k.id: k.fon_mu for k in sonuc.kararlar}
    # LLM bir ID'yi atlarsa guvenli tarafta kal: False (fon degil) varsay
    return [karar_map.get(i, False) for i in range(len(basliklar))]

MAKS_BASLIK_BATCH = 40  # tek cagrida cok fazla baslik hassasiyeti dusurur


def fon_olanlari_ayikla(duyurular: list[dict]) -> list[dict]:
    """
    duyurular: [{"baslik": ..., "url": ...}, ...]
    Sadece fon/destek ilani olanlari, orijinal sirayla dondurur.
    LLM rate limit/kota hatasi verirse o BATCH atlanir, uygulama COKMEZ
    (hicbir duyuru fon olarak isaretlenmeden devam eder).
    """
    if not duyurular:
        return []

    sonuc = []
    toplam_batch = (len(duyurular) + MAKS_BASLIK_BATCH - 1) // MAKS_BASLIK_BATCH

    for batch_no, i in enumerate(range(0, len(duyurular), MAKS_BASLIK_BATCH), 1):
        parca = duyurular[i:i + MAKS_BASLIK_BATCH]
        basliklar = [d["baslik"] for d in parca]

        print(f"[FİLTRE] Batch {batch_no}/{toplam_batch} ({len(parca)} baslik) siniflandiriliyor...")

        try:
            kararlar = _siniflandir_toplu(basliklar)
        except RetryError as e:
            gercek_hata = str(e.last_attempt.exception())
            if "429" in gercek_hata or "rate" in gercek_hata.lower() or "quota" in gercek_hata.lower():
                print(f"[FİLTRE] ⚠️ RATE LIMIT/KOTA doldu, bu batch ({len(parca)} baslik) ATLANDI: {gercek_hata}")
            else:
                print(f"[FİLTRE] ⚠️ LLM 3 denemede de basarisiz oldu, bu batch ATLANDI: {gercek_hata}")
            continue
        except Exception as e:
            print(f"[FİLTRE] ⚠️ Beklenmeyen hata, bu batch ATLANDI: {e}")
            continue

        for d, fon_mu in zip(parca, kararlar):
            print(f"  [{'FON' if fon_mu else 'duyuru'}] {d['baslik']}")
            if fon_mu:
                sonuc.append(d)

    return sonuc