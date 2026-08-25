"""
FonRadar AI - TUBITAK Fon Kazima Modulu
========================================
Tek dosya, ayri ayri fonksiyonlar:

  1) get_fund_links(list_url)  -> Liste sayfasindan fon (ilan) detay linklerini toplar.
  2) extract_pdf_text(pdf_url) -> Bir PDF linkini indirip icindeki metni cikarir.
  3) scrape_detail(url)        -> Detay sayfasina girer; metni + ek PDF'leri okur,
                                  hepsini tek "full_text" alaninda birlestirir.
  4) fonlari_getir()           -> Cache kontrolu + kazima. API'den DE bu cagrilir.
  5) main()                    -> Komut satirindan elle calistirmak icin (thin wrapper).

Kullanilan (hepsi ucretsiz/acik kaynak): requests, beautifulsoup4, pdfplumber
Kurulum:  pip install requests beautifulsoup4 pdfplumber

NOT (robots.txt): /tr/destekler yollari serbest, Crawl-delay yok. Yine de sunucuyu
yormamak icin istekler arasi POLITE_DELAY saniye bekliyoruz.
"""

import io
import json
import os
import re
import time
import urllib.robotparser
from datetime import datetime, timedelta
from urllib.parse import urljoin, urlparse

import pdfplumber
import requests
from bs4 import BeautifulSoup


BASE = "https://tubitak.gov.tr"
LIST_URL = "https://tubitak.gov.tr/tr/destekler/sanayi/ulusal-destek-programlari"
POLITE_DELAY = 1           # istekler arasi bekleme banlanmamak icin
TIMEOUT = 30               # istek zaman asimi
HEADERS = {
    "User-Agent": "FonRadar-AI-StajProjesi/1.0 (egitim amacli; iletisim: ekip@ornek.com)"
}

# CACHE_FILE artik bu dosyanin konumuna sabit (cwd'ye gore kaymaz)
_BU_DIZIN = os.path.dirname(os.path.abspath(__file__))
CACHE_FILE = os.path.join(_BU_DIZIN, "fonlar.json")
CACHE_TTL_HOURS = 12      # Verinin gecerlilik suresi (12 saat)

session = requests.Session()
session.headers.update(HEADERS)


_robot_parser = None
_robot_denendi = False   # "okuma denendi mi" sorusu, "parser hazir mi"dan AYRI tutuluyor


def robots_izin_var_mi(url: str) -> bool:
    """
    robots.txt kontrolu.
    DUZELTME: Eskiden read() SSL/ag hatasi verince _robot_parser 'yarim' (bos)
    bir nesne olarak kalip sonraki TUM URL'lere False donuyordu (ilk hata
    dogru islense de, 2. ve sonraki URL'ler yanlislikla yasaklaniyordu).
    Simdi: okuma SADECE bir kez denenir (_robot_denendi), _robot_parser ise
    yalnizca BASARILI olursa atanir. Basarisizsa None kalir ve fonksiyon
    HER ZAMAN True doner (izin verilir) - internete tekrar gidilmeden.
    """
    global _robot_parser, _robot_denendi
    if not _robot_denendi:
        _robot_denendi = True
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(urljoin(BASE, "/robots.txt"))
        try:
            parser.read()
            _robot_parser = parser   # SADECE basarili olursa ata
        except Exception as e:
            print(f"[ROBOTS] robots.txt okunamadi, izin veriliyor: {e}")
            # _robot_parser None kalir

    if _robot_parser is None:
        return True
    return _robot_parser.can_fetch("*", url)


def nazik_get(url: str) -> requests.Response | None:
    """Bekleme + hata yonetimi ile GET. robots yasakliysa atlar."""
    if not robots_izin_var_mi(url):
        print(f"[ROBOTS] Yasak, atlaniyor: {url}")
        return None
    time.sleep(POLITE_DELAY)
    try:
        r = session.get(url, timeout=TIMEOUT)
        r.raise_for_status()
        return r
    except requests.RequestException as e:
        print(f"[HATA] Alinamadi: {url} -> {e}")
        return None


def get_fund_links(list_url: str = LIST_URL) -> list[str]:
    """
    Liste sayfasindaki fon/ilan detay sayfalarinin linklerini dondurur.
    Site yapisi degisirse asagidaki 'desen' satirini gozden gecir.
    """
    resp = nazik_get(list_url)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    links = set()
    desen = re.compile(r"/destekler/.+/ulusal-destek-programlari/.+")

    for a in soup.find_all("a", href=True):
        href = a["href"]
        tam = urljoin(BASE, href)
        if urlparse(tam).netloc.endswith("tubitak.gov.tr") and desen.search(tam):
            if not tam.lower().endswith(".pdf"):
                links.add(tam.split("#")[0])

    print(f"[LINK] {len(links)} adet fon linki bulundu.")
    return sorted(links)


def extract_pdf_text(pdf_url: str) -> str:
    """
    PDF'i indirir ve metnini cikarir. Taranmis (goruntu) PDF ise pdfplumber
    bos dondurur -> OCR kapsam disi oldugu icin bos gecilir.
    """
    resp = nazik_get(pdf_url)
    if resp is None:
        return ""

    parcalar = []
    try:
        with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
            for sayfa in pdf.pages:
                metin = sayfa.extract_text() or ""
                if metin.strip():
                    parcalar.append(metin)
    except Exception as e:
        print(f"[PDF HATA] Okunamadi: {pdf_url} -> {e}")
        return ""

    tam = "\n".join(parcalar).strip()
    if not tam:
        print(f"[PDF] Metin cikmadi (muhtemelen taranmis/goruntu): {pdf_url}")
    return tam


def scrape_detail(url: str) -> dict:
    """
    Detay sayfasindan basligi, sayfa metnini ve ekli PDF'lerin metnini toplar,
    hepsini birlestirip tek bir sozluk (record) olarak dondurur.
    """
    resp = nazik_get(url)
    if resp is None:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    # --- Baslik ---
    h1 = soup.find("h1")
    baslik = h1.get_text(strip=True) if h1 else ""

    # --- Ana metin --- (Drupal: ana icerik <main> / article icinde)
    ana = soup.find("main") or soup.find("article") or soup.body
    if ana:
        for etiket in ana.find_all(["nav", "header", "footer", "script", "style"]):
            etiket.decompose()
        sayfa_metni = ana.get_text(separator="\n", strip=True)
    else:
        sayfa_metni = ""

    # --- Ekli PDF linkleri ---
    # DUZELTME: eskiden 'soup' (TUM sayfa: nav/header/footer dahil) taraniyordu,
    # oysa sayfa_metni 'ana'ya (temizlenmis ana icerik) gore aliniyordu -> tutarsizdi.
    # Simdi PDF linkleri de 'ana' kapsaminda toplaniyor, sayfa_metni ile tutarli.
    pdf_linkleri = []
    if ana:
        for a in ana.find_all("a", href=True):
            tam = urljoin(BASE, a["href"])
            if tam.lower().endswith(".pdf") and tam not in pdf_linkleri:
                pdf_linkleri.append(tam)

    # --- PDF metinlerini cek ve birlestir ---
    pdf_metinleri = []
    for pdf_url in pdf_linkleri:
        print(f"    -> PDF isleniyor: {pdf_url}")
        metin = extract_pdf_text(pdf_url)
        if metin:
            pdf_metinleri.append(f"\n\n[PDF: {pdf_url}]\n{metin}")

    full_text = sayfa_metni + "".join(pdf_metinleri)

    return {
        "url": url,
        "baslik": baslik,
        "sayfa_metni": sayfa_metni,
        "pdf_linkleri": pdf_linkleri,
        "full_text": full_text,   # ileride AI temizleme / kunye cikarimi icin
    }



def onbellek_taze_mi() -> bool:
    """CACHE_FILE var mi ve CACHE_TTL_HOURS icinde mi?"""
    if not os.path.exists(CACHE_FILE):
        return False
    dosya_zamani = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))
    gecen = datetime.now() - dosya_zamani
    return gecen < timedelta(hours=CACHE_TTL_HOURS)


def _cache_oku() -> list[dict]:
    with open(CACHE_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def fonlari_getir(force_refresh: bool = False) -> list[dict]:
    """
    Fonlari dondurur.
      - Onbellek tazeyse: dosyadan okur, siteyi hic yormaz.
      - Degilse: siteyi kazir, onbellegi gunceller.
    API'den de bu fonksiyon cagrilir (main degil).
    force_refresh=True dersen cache'i yok sayip zorla yeniden kazir.
    """
    # CACHE KONTROLU
    if not force_refresh and onbellek_taze_mi():
        dosya_zamani = datetime.fromtimestamp(os.path.getmtime(CACHE_FILE))

        gecen_saniye = int((datetime.now() - dosya_zamani).total_seconds())
        saat = gecen_saniye // 3600
        dakika = (gecen_saniye % 3600) // 60

        print("[CACHE HIT] Onbellek TAZE -> siteye gidilmiyor, dosyadan okunuyor.")
        print(f"[CACHE] Onbellek gecerli ({saat} saat {dakika} dk once guncellendi)... "
              f"Scraping atlaniyor, '{CACHE_FILE}' okunuyor...")
        kayitlar = _cache_oku()
        print(f"[BASARILI] Yerel dosyadan {len(kayitlar)} kayit yuklendi.")
        return kayitlar

    # CACHE YOK/ESKI -> WEB'DEN KAZI
    print("[CACHE MISS] Onbellek yok/eski (>12 saat) -> siteden yeniden cekilecek.")
    print("[SCRAPING] Onbellek yok veya suresi dolmus. Siteden yeni veri cekiliyor...")
    kayitlar = []
    linkler = get_fund_links(LIST_URL)

    for i, link in enumerate(linkler, 1):
        print(f"[{i}/{len(linkler)}] {link}")
        try:
            kayit = scrape_detail(link)
            if kayit:
                kayitlar.append(kayit)
        except Exception as e:
            # Bir ilan bozuksa tumunu cokertme, sadece onu atla
            print(f"[HATA] Atlandi: {link} -> {e}")

    # BOS SONUC ESKI VERIYI EZMESIN
    if kayitlar:
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(kayitlar, f, ensure_ascii=False, indent=2)
        print(f"\n[BASARILI] {len(kayitlar)} kayit cekildi ve '{CACHE_FILE}' guncellendi.")
    else:
        print("[UYARI] Hic kayit cekilemedi. Eski onbellek korunuyor (varsa).")
        if os.path.exists(CACHE_FILE):
            kayitlar = _cache_oku()

    return kayitlar


def main():
    fonlari_getir()
