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

# YENİ: esnafkoop.ticaret.gov.tr duyurular kaynagi
ESNAF_BASE = "https://esnafkoop.ticaret.gov.tr"
ESNAF_LIST_URL = f"{ESNAF_BASE}/duyurular"

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


_robot_parcalari: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_robot_denenenler: set[str] = set()


'''def robots_izin_var_mi(url: str) -> bool:
    """Domain bazli robots.txt kontrolu (birden fazla siteyi destekler)."""
    domain = urlparse(url).netloc
    if domain not in _robot_denenenler:
        _robot_denenenler.add(domain)
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(f"https://{domain}/robots.txt")
        try:
            parser.read()
            _robot_parcalari[domain] = parser   # SADECE basarili olursa ata
        except Exception as e:
            print(f"[ROBOTS] {domain} robots.txt okunamadi, izin veriliyor: {e}")
            _robot_parcalari[domain] = None

    parser = _robot_parcalari.get(domain)
    if parser is None:
        return True
    return parser.can_fetch("*", url)'''

def robots_izin_var_mi(url: str) -> bool:
    """Domain bazli robots.txt kontrolu (birden fazla siteyi destekler)."""
    domain = urlparse(url).netloc
    if domain not in _robot_denenenler:
        _robot_denenenler.add(domain)
        parser = urllib.robotparser.RobotFileParser()
        parser.set_url(f"https://{domain}/robots.txt")

        try:
            resp = requests.get(
                f"https://{domain}/robots.txt",
                headers=HEADERS,
                timeout=10,
            )
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
                _robot_parcalari[domain] = parser
            else:
                print(f"[ROBOTS] {domain} robots.txt {resp.status_code} dondu, izin veriliyor.")
                _robot_parcalari[domain] = None
        except Exception as e:
            print(f"[ROBOTS] {domain} robots.txt okunamadi, izin veriliyor: {e}")
            _robot_parcalari[domain] = None

    parser = _robot_parcalari.get(domain)
    if parser is None:
        return True
    return parser.can_fetch("*", url)

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
    TUBITAK detay sayfasindan basligi, sayfa metnini ve ekli PDF'lerin metnini
    toplar, hepsini birlestirip tek bir sozluk (record) olarak dondurur.
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
    # PDF linkleri de 'ana' kapsaminda toplaniyor, sayfa_metni ile tutarli.
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
        "kaynak": "tubitak",
    }




def get_esnaf_duyuru_items(list_url: str = ESNAF_LIST_URL) -> list[dict]:
    """
    esnafkoop duyurular listesinden {"baslik", "url"} ciftlerini toplar.
    Baslik ZATEN liste sayfasinda oldugu icin, LLM siniflandirmasi icin
    detay sayfasina gitmeye gerek yok (gereksiz istek atilmiyor).
    """
    resp = nazik_get(list_url)
    if resp is None:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")
    ana = soup.find("main") or soup.find("article") or soup.body
    items: list[dict] = []
    gorulen: set[str] = set()

    if ana:
        for a in ana.find_all("a", href=True):
            tam = urljoin(ESNAF_BASE, a["href"]).split("#")[0]
            if "/duyurular/" in tam and tam != list_url and tam not in gorulen:
                baslik = a.get_text(strip=True)
                if baslik:
                    gorulen.add(tam)
                    items.append({"baslik": baslik, "url": tam})

    print(f"[ESNAFKOOP] {len(items)} adet duyuru basligi bulundu.")
    return items


def scrape_esnaf_detail(url: str) -> dict:
    """
    esnafkoop detay sayfasini kazir; TUBITAK scrape_detail ile AYNI semayi
    ('url','baslik','sayfa_metni','pdf_linkleri','full_text') dondurur ki
    downstream (vector_db, skorlama) hicbir degisiklik gerektirmesin.
    """
    resp = nazik_get(url)
    if resp is None:
        return {}

    soup = BeautifulSoup(resp.text, "html.parser")

    h1 = soup.find("h1") or soup.find("h2")
    baslik = h1.get_text(strip=True) if h1 else ""

    ana = soup.find("main") or soup.find("article") or soup.body
    if ana:
        for etiket in ana.find_all(["nav", "header", "footer", "script", "style"]):
            etiket.decompose()
        sayfa_metni = ana.get_text(separator="\n", strip=True)
    else:
        sayfa_metni = ""

    pdf_linkleri = []
    if ana:
        for a in ana.find_all("a", href=True):
            tam = urljoin(ESNAF_BASE, a["href"])
            if tam.lower().endswith(".pdf") and tam not in pdf_linkleri:
                pdf_linkleri.append(tam)

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
        "full_text": full_text,
        "kaynak": "esnafkoop",
    }


def esnafkoop_fonlarini_getir() -> list[dict]:
    """
    esnafkoop duyurularini ceker, ONCE LLM ile fon/degil diye ayiklar,
    SADECE fon/hibe/destek olanlarin detay+PDF metnini kazir.
    """
    from llm.duyuru_filtre import fon_olanlari_ayikla  # dongusel importu onlemek icin lazy import

    duyurular = get_esnaf_duyuru_items()
    if not duyurular:
        return []

    print(f"[ESNAFKOOP] {len(duyurular)} duyuru LLM'e siniflandirma icin gonderiliyor...")
    fon_duyurulari = fon_olanlari_ayikla(duyurular)
    print(f"[ESNAFKOOP] {len(fon_duyurulari)}/{len(duyurular)} duyuru FON olarak isaretlendi, detaylari kaziniyor.")

    kayitlar = []
    for i, d in enumerate(fon_duyurulari, 1):
        print(f"[ESNAFKOOP {i}/{len(fon_duyurulari)}] {d['url']}")
        try:
            kayit = scrape_esnaf_detail(d["url"])
            if kayit:
                kayitlar.append(kayit)
        except Exception as e:
            print(f"[HATA] Atlandi: {d['url']} -> {e}")

    return kayitlar


# ==========================================
# CACHE + BIRLESTIRME
# ==========================================

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
      - Degilse: HER IKI kaynagi (TUBITAK + esnafkoop) kazir, onbellegi gunceller.
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

    # CACHE YOK/ESKI -> WEB'DEN KAZI (IKI KAYNAK)
    print("[CACHE MISS] Onbellek yok/eski (>12 saat) -> siteden yeniden cekilecek.")
    print("[SCRAPING] Onbellek yok veya suresi dolmus. Siteden yeni veri cekiliyor...")

    kayitlar = []

    # --- A) TUBITAK ---
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

    # --- B) ESNAFKOOP (YENİ) --- LLM ile fon olmayanlar elenir
    try:
        esnaf_kayitlari = esnafkoop_fonlarini_getir()
        kayitlar.extend(esnaf_kayitlari)
    except Exception as e:
        print(f"[UYARI] esnafkoop kazima basarisiz, TUBITAK verisiyle devam ediliyor: {e}")

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