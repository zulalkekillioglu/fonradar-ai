"""
FonRadar AI - Gün 3: Veri Temizleme, Parçalama (Chunking),
Vektör Veritabanı (ChromaDB) ve Semantik Arama Modülü
=====================================================
"""

import json
import os
import re
import time
import chromadb
from chromadb.utils import embedding_functions
from tubitak_Scraper.scraper import fonlari_getir


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "chroma_db_data")
SYNC_FILE = os.path.join(DB_PATH, ".sync_time")


def clean_text(text: str) -> str:
    """Gereksiz boşlukları ve alt satır kargaşalarını temizler."""
    if not text:
        return ""
    text = re.sub(r'\s+', ' ', text)
    return text.strip()


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    Metni semantik arama için küçük parçalara (chunk) böler.
    """
    text = clean_text(text)
    words = text.split(" ")
    chunks = []

    if len(words) <= chunk_size:
        return [text] if text else []

    start = 0
    while start < len(words):
        end = start + chunk_size
        chunk = " ".join(words[start:end])
        if chunk.strip():
            chunks.append(chunk)
        start += (chunk_size - overlap)

    return chunks



def _bul_json(dosya_adi: str = "fonlar.json") -> str:
    """
    Proje içinde fonlar.json dosyasını arar ve ilk bulduğu yolu döndürür.
    """
    kok = BASE_DIR
    for _ in range(4):
        for dizin, _alt, dosyalar in os.walk(kok):
            if dosya_adi in dosyalar:
                return os.path.join(dizin, dosya_adi)
        ust = os.path.dirname(kok)
        if ust == kok:
            break
        kok = ust
    return dosya_adi



def build_vector_db(force_reindex: bool = False):
    """
    Fon verilerini çeker, parçalar ve ChromaDB vektör veritabanına paketler halinde ekler.
    ONCE fonlari_getir() cagrilir (12 saat TTL burada uygulanir): veri eskiyse kazinir ve
    fonlar.json yenilenir. Ardindan JSON, ChromaDB'den yeniyse eski veritabani temizlenip
    otomatik senkronize edilir.
    """
    # --- 1. VERİ KAZIMA VE CACHE KONTROLÜ (TTL burada: eskiyse kazir, tazeyse okur) ---
    print("\n--- 1. VERİ KAZIMA VE CACHE KONTROLÜ ---")
    fonlar = fonlari_getir()

    if not fonlar:
        print("[HATA] İşlenecek fon verisi bulunamadı!")
        return None, None

    # --- 2. CHROMADB VE YEREL EMBEDDING MODELİ HAZIRLANIYOR ---
    print("\n--- 2. CHROMADB VE YEREL EMBEDDING MODELİ HAZIRLANIYOR ---")
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    os.makedirs(DB_PATH, exist_ok=True)
    chroma_client = chromadb.PersistentClient(path=DB_PATH)

    # --- AKILLI SENKRONİZASYON (JSON vs DB) ---
    # fonlari_getir kazidiysa json artik yeni; bu kontrol onu yakalayip Chroma'yi tazeler.
    json_yolu = _bul_json()
    json_mtime = os.path.getmtime(json_yolu) if os.path.exists(json_yolu) else 0

    db_mtime = 0
    if os.path.exists(SYNC_FILE):
        try:
            with open(SYNC_FILE, "r") as f:
                db_mtime = float(f.read().strip())
        except Exception:
            pass

    # Eğer JSON dosyası DB'den daha yeniyse, eski verileri sil ve zorla yeniden indeksle
    if json_mtime > db_mtime:
        print("[SENKRONİZASYON] 'fonlar.json' güncellenmiş! Eski ChromaDB temizleniyor...")
        force_reindex = True
        try:
            chroma_client.delete_collection(name="tubitak_fonlari")
        except Exception:
            pass

    collection = chroma_client.get_or_create_collection(
        name="tubitak_fonlari",
        embedding_function=sentence_transformer_ef
    )

    # Chroma zaten guncel ve doluysa (json degismemis) ingest'i atla; veri hazir.
    if not force_reindex and collection.count() > 0:
        print(
            f"[CHROMADB] Veritabanı zaten güncel ve dolu ({collection.count()} chunk mevcut). "
            "Yeniden indeksleme atlanıyor."
        )
        return chroma_client, collection

    print("\n--- 3. METİNLER TEMİZLENİYOR, CHUNK'LARA BÖLÜNÜYOR VE VEKTÖRLEŞTİRİLİYOR ---")

    documents = []
    metadatas = []
    ids = []

    chunk_counter = 0
    for idx, fon in enumerate(fonlar):
        baslik = fon.get("baslik", "Başlıksız Fon")
        url = fon.get("url", "")
        full_text = fon.get("full_text", "")

        chunks = chunk_text(full_text, chunk_size=200, overlap=30)

        for c_idx, chunk in enumerate(chunks):
            chunk_counter += 1
            documents.append(chunk)
            metadatas.append({
                "baslik": baslik,
                "url": url,
                "chunk_id": c_idx
            })
            ids.append(f"fon_{idx}_chunk_{c_idx}")

    # BATCHING (PAKETLEME) İŞLEMİ
    if documents:
        batch_size = 1000
        total_chunks = len(documents)
        print(f"[BİLGİ] Toplam {total_chunks} chunk {batch_size}'erli paketler halinde yükleniyor...")

        for i in range(0, total_chunks, batch_size):
            end_idx = min(i + batch_size, total_chunks)
            collection.upsert(
                documents=documents[i:end_idx],
                metadatas=metadatas[i:end_idx],
                ids=ids[i:end_idx]
            )
            print(f"  -> {end_idx}/{total_chunks} chunk yüklendi...")

        print(
            f"[BAŞARILI] Toplam {len(fonlar)} fon sayfasından {chunk_counter} adet vektör (chunk) ChromaDB'ye yüklendi.")

        # İşlem başarıyla bittiyse güncel zamanı .sync_time dosyasına yaz
        with open(SYNC_FILE, "w") as f:
            f.write(str(time.time()))

    return chroma_client, collection




def ara(query_text: str, k: int = 5):
    """
    Semantik arama yapar ve ChromaDB'nin HAM sonucunu döndürür.
    """
    sentence_transformer_ef = embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )

    chroma_client = chromadb.PersistentClient(path=DB_PATH)

    collection = chroma_client.get_or_create_collection(
        name="tubitak_fonlari",
        embedding_function=sentence_transformer_ef
    )

    return collection.query(query_texts=[query_text], n_results=k)


'''def semantic_search(collection, query_text: str, n_results: int = 3):
    """
    Kullanıcı sorgusuna en yakın sonuçları vektör benzerliğiyle getirir.
    """
    print(f"\n[SEMANTİK ARAMA SORGUSU]: '{query_text}'")

    results = collection.query(
        query_texts=[query_text],
        n_results=n_results
    )

    print("=" * 70)
    for i in range(len(results['documents'][0])):
        doc = results['documents'][0][i]
        meta = results['metadatas'][0][i]
        dist = results['distances'][0][i] if 'distances' in results else "N/A"

        print(f"SONUÇ #{i + 1} (Mesafe Skoru: {dist})")
        print(f"İlan Başlığı: {meta['baslik']}")
        print(f"Link: {meta['url']}")
        print(f"İlgili Metin Parçası (Chunk):\n{doc[:300]}...")
        print("-" * 70)


def main():
    print("=" * 70)
    print(" FonRadar AI - Gün 3: Vektör Veritabanı ve Semantik Arama Testi")
    print("=" * 70)

    client, collection = build_vector_db()

    if not collection:
        return

    print("\n--- CANLI İNTERAKTİF ARAMA MODU ---")
    print("(Çıkmak için 'q' veya 'cikis' yazıp Enter'a basabilirsiniz.)\n")

    while True:
        kullanici_sorgusu = input("Aramak istediğiniz konuyu yazın: ").strip()

        if kullanici_sorgusu.lower() in ['q', 'cikis', 'exit']:
            print("\nCanlı arama modundan çıkıldı.")
            break

        if not kullanici_sorgusu:
            continue

        semantic_search(collection, kullanici_sorgusu, n_results=2)


if __name__ == "__main__":
    main()'''