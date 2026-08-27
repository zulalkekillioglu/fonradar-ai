import threading
import time

from fastapi import BackgroundTasks, FastAPI

from fon.fon import skorlanacak_fonlar
from llm.skorlama import fonlari_skorla
from tubitak_Scraper.scraper import onbellek_taze_mi
from tubitak_Scraper.vector_db import build_vector_db

app = FastAPI(title="FonRadar AI Backend")

""" CORS hatası alıyordum, mainAPI.py'ye CORSMiddleware ekledim.
Frontend'in backend'e istek atabilmesi için gerekliydi.
Gün 18'de Vercel'e deploy edince oraya da canlı adresi eklememiz gerekecek.
"""

from fastapi.middleware.cors import CORSMiddleware

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Canlıda her yerden gelen isteklere izin ver
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Ayni anda birden fazla arka plan yenilemesi baslamasin diye bayrak + kilit.
_yenileme_aktif = False
_kilit = threading.Lock()


def _yenileme_baslat_gerekli_mi() -> bool:
    """
    Atomik: yenileme calismiyorsa bayragi kaldirir ve True doner ('sen baslat');
    zaten calisiyorsa False doner. Iki istek ayni anda gelse de yalnizca biri baslatir.
    """
    global _yenileme_aktif
    with _kilit:
        if _yenileme_aktif:
            return False
        _yenileme_aktif = True
        return True


def _arka_plan_yenileme():
    """Arka planda veri tazeler (kazima + Chroma reindex). Bitince bayragi indirir."""
    global _yenileme_aktif
    try:
        print("\n[ARKA PLAN] Veri yenileme basladi (kazima + reindex olabilir)...")
        build_vector_db()   # TTL/senkron durumuna gore kendi karar verir
        print("[ARKA PLAN] Veri yenileme tamamlandi.")
    except Exception as e:
        print(f"[ARKA PLAN HATA] Yenileme basarisiz: {e}")
    finally:
        with _kilit:
            _yenileme_aktif = False


@app.get("/")
def home():
    return {"status": "online", "message": "FonRadar AI calisiyor"}


@app.get("/match-score")
def ara_endpoint(sorgu: str, background_tasks: BackgroundTasks):
    baslangic = time.time()

    user_prompt = f"""Aşağıdaki kurum bilgilerine göre mevcut fonları değerlendir ve kurumun şehir talebi varsa bunu da göz önünde bulundur:
    {sorgu}
    """

    # 1) Veri eski mi? (ucuz kontrol)
    veri_eski = not onbellek_taze_mi()
    guncelleniyor = False
    if veri_eski:
        if _yenileme_baslat_gerekli_mi():
            background_tasks.add_task(_arka_plan_yenileme)
        guncelleniyor = True

    # 2) MEVCUT Chroma ile hemen ara
    fonlar = skorlanacak_fonlar(sorgu, limit=10)

    if not fonlar:
        if not guncelleniyor and _yenileme_baslat_gerekli_mi():
            background_tasks.add_task(_arka_plan_yenileme)
            guncelleniyor = True
        cevap = {
            "durum": "hazirlaniyor" if guncelleniyor else "veri_yok",
            "guncelleniyor": guncelleniyor,
            "mesaj": ("Henuz veri yok, arka planda hazirlaniyor. "
                      "Lutfen birkac dakika sonra tekrar deneyin.")
            if guncelleniyor else "Uygun fon bulunamadi.",
            "sonuclar": [],
        }
    else:
        try:
            # LLM ile skorlama yapmayı dene
            sonuclar = fonlari_skorla(fonlar, user_prompt)
            cevap = {
                "durum": "basarili",
                "guncelleniyor": guncelleniyor,
                "mesaj": ("Sonuclar mevcut veriden dondu. Yeni veri arka planda "
                          "hazirlaniyor; birazdan guncellenecek.")
                if guncelleniyor else "Sonuclar guncel veriden.",
                "sonuclar": sonuclar,
            }
        except Exception as e:
            # LLM Kota / Rate Limit veya herhangi bir skorlama hatasında ÇÖKMEYECEK, bu JSON'ı dönecek.
            # Hata mesaji, sebebi netlestirsin diye GERCEK hatayi icerir (sabit "kota doldu" degil).
            print(f"[/match-score HATA] LLM Skorlama Başarısız: {e}")
            hata_metni = str(e)
            if "429" in hata_metni or "rate" in hata_metni.lower() or "quota" in hata_metni.lower():
                kullanici_mesaji = "API kotası/rate limit doldu. Lütfen birkaç dakika sonra tekrar deneyin."
            else:
                kullanici_mesaji = f"Skorlama sırasında bir hata oluştu: {hata_metni}"
            cevap = {
                "durum": "basarısız",
                "guncelleniyor": guncelleniyor,
                "mesaj": kullanici_mesaji,
                "sonuclar": []
            }

    gecen = round(time.time() - baslangic, 2)
    print(f"[/ara] '{sorgu}' -> {len(cevap['sonuclar'])} sonuc, {gecen} sn "
          f"(guncelleniyor={guncelleniyor})")

    return cevap

@app.get("/fetch-grants")
def fetch_grants():
    """fonlar.json'daki tüm fonların başlık ve url'sini döndürür."""
    import json
    from tubitak_Scraper.vector_db import _bul_json
    try:
        with open(_bul_json(), encoding="utf-8") as f:
            tum_fonlar = json.load(f)
    except FileNotFoundError:
        return {"adet": 0, "fonlar": [], "mesaj": "fonlar.json bulunamadı."}
    except json.JSONDecodeError as e:
        return {"adet": 0, "fonlar": [], "mesaj": f"fonlar.json bozuk/okunamadı: {e}"}
    except Exception as e:
        return {"adet": 0, "fonlar": [], "mesaj": f"Fonlar getirilirken hata oluştu: {e}"}

    fonlar = [
        {"baslik": fon.get("baslik", ""), "url": fon.get("url", "")}
        for fon in tum_fonlar
    ]
    return {"adet": len(fonlar), "fonlar": fonlar}



@app.get("/generate-report")
def generate_report():
    """Şimdilik stub: endpoint'in çalıştığını doğrular (girdi almaz)."""
    return {"durum": "başarılı", "mesaj": "generate-report endpoint çalışıyor."}


