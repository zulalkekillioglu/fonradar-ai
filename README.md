# FonRadar AI

**Kuzey Anadolu Kalkınma Ajansı (KUZKA)** bünyesinde geliştirilen **FonRadar AI**, kurumların ve KOBİ'lerin ihtiyaç duyduğu hibe/fon programlarını otomatik olarak tarayan, anlamsal (semantik) olarak eşleştiren ve büyük dil modeli (LLM) destekli hassas uygunluk skorlaması sunan bir arka uç (backend) modülüdür.

## İçindekiler

- [Proje Özeti](#proje-özeti-hakkında)
- [Temel Özellikler](#temel-özellikler)
- [Kullanılan Teknolojiler](#kullanılan-teknolojiler-tech-stack)
- [Kurulum](#kurulum-installation)
- [Kullanım](#kullanım-usage)
- [API Uç Noktaları](#api-uç-noktaları-endpoints)
- [Proje Yapısı](#proje-yapısı-directory-structure)

## Proje Özeti (Hakkında)

FonRadar AI, kullanıcıdan (kurum/işletme) serbest metin biçiminde alınan ihtiyaç tanımını (sektör, konu, şehir, ölçek vb.), TÜBİTAK ulusal destek programları gibi kaynaklardan toplanan güncel fon/hibe verileriyle karşılaştırarak 0-100 arası hassas bir uygunluk skoru üretir. Amaç, işletmelerin kendileri için en alakalı hibe fırsatını bulmak için saatler harcamasının önüne geçmek ve bu süreci saniyeler içinde, gerekçeli ve yapılandırılmış bir çıktıya dönüştürmektir.

Sistem, `.env` dosyasındaki `LLM_PROVIDER` değişkeniyle anında değiştirilebilen bir LLM sağlayıcı mimarisi üzerine kuruludur: **Groq** (varsayılan) ve **Gemini**, aynı `get_llm()` fonksiyonu üzerinden dinamik olarak seçilir, sağlayıcı paketleri yalnızca gerektiğinde (lazy import) yüklenir. Skorlama katmanı ise LangChain'in yapılandırılmış çıktı (`with_structured_output`) mekanizmasıyla çalışır. Bu sayede model çıktısı doğrudan bir Pydantic şemasına (skor, gerekçe, fon kodu, şehir uyumu, son başvuru tarihi, hibe oranı vb.) doğrulanarak alınır; elle JSON ayrıştırma veya kırılgan `try/except` bloklarına ihtiyaç duyulmaz.

Verinin arkasındaki mimari üç ana bileşenden oluşur:

- **Veri Kazıma (Web Scraping):** `tubitak_Scraper/scraper.py`, TÜBİTAK'ın ulusal destek programları listesini `requests` + `BeautifulSoup` ile tarar, her fon ilanının detay sayfasını ve varsa ekli PDF dosyalarını (`pdfplumber` ile) işleyerek tek bir birleşik metin (`full_text`) hâline getirir. Süreç, `robots.txt` kurallarına saygı gösterir ve istekler arasına nazik bir gecikme (`POLITE_DELAY`) koyarak hedef sunucuyu yormaz.
- **Önbellekleme (Cache) Mantığı:** Kazınan veri `fonlar.json` dosyasına yazılır ve bir **TTL (Time-To-Live)** politikasıyla korunur (`CACHE_TTL_HOURS`). Önbellek süresi dolmadığı sürece siteye tekrar istek atılmaz, veri doğrudan diskten okunur — bu da yanıt sürelerini ciddi ölçüde kısaltır ve hedef siteye gereksiz yük bindirilmesini engeller. Ayrıca `fonlar.json` ile ChromaDB vektör veritabanı arasında dosya zaman damgalarına (`mtime`) dayalı bir senkronizasyon kontrolü vardır: JSON güncellenmişse vektör veritabanı otomatik olarak yeniden indekslenir, güncellenmemişse gereksiz yeniden vektörleştirme atlanır.
- **Kilit (Lock) Mekanizması:** Hem API katmanında (`mainAPI.py`, `backend/app/main.py`) hem de kazıma modülünde, `threading.Lock` ile korunan atomik bir bayrak (`_yenileme_aktif` / `is_scraping_active`) kullanılır. Bu sayede birden fazla istemci aynı anda veri isteğinde bulunsa bile yalnızca **bir tane** arka plan yenileme/kazıma görevi başlatılır; kullanıcı asla kazıma süresince beklemez, mevcut veriyle anında yanıt alır ve tazeleme arka planda (FastAPI `BackgroundTasks`) sessizce tamamlanır.

## Temel Özellikler

- **Semantik Fon Arama:** ChromaDB + çok dilli `sentence-transformers` embedding modeli (`paraphrase-multilingual-MiniLM-L12-v2`) ile anlam tabanlı arama; anahtar kelime eşleşmesine bağımlı değildir.
- **LLM Destekli Hassas Skorlama:** Her fon için tek bir LLM çağrısında (skor + gerekçe + alan çıkarımı bir arada) 0-100 arası hassas, dinamik (yuvarlak sayılara takılı kalmayan) uygunluk puanı üretir.
- **Yapılandırılmış Çıktı Garantisi:** LangChain `with_structured_output` + Pydantic modeli (`FonSkoru`) sayesinde skor, fon kodu, şehir uyumu, konu, son başvuru tarihi ve hibe oranı gibi alanlar güvenilir biçimde ayrıştırılır.
- **Otomatik Web Kazıma:** TÜBİTAK ulusal destek programları sayfasından ilan detaylarını ve ekli PDF'lerin içeriğini otomatik toplar.
- **Akıllı Önbellekleme (TTL Cache):** Belirlenen süre içinde tekrar kazıma yapılmaz; veri diskten (`fonlar.json`) hızlıca sunulur.
- **Kilitli Arka Plan Yenileme:** Veri bayatladığında kullanıcıyı bekletmeden, arka planda ve yalnızca tek seferde tetiklenen otomatik yenileme.
- **Chroma-JSON Senkronizasyonu:** Vektör veritabanı, kaynak JSON dosyasıyla otomatik senkronize kalır; gereksiz yeniden indeksleme yapılmaz.
- **Hata Toleranslı Skorlama:** Rate limit / kota hatalarında üstel geri çekilme (`tenacity` ile otomatik yeniden deneme) ve kullanıcı dostu hata mesajları.
- **Dedup (Tekilleştirme):** Aynı fona ait birden fazla metin parçası (chunk) benzerlik sırası korunarak tek kayda indirgenir.

## Kullanılan Teknolojiler (Tech Stack)

| Katman                   | Teknoloji                                                                                                                                                                                      |
| ------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Web Çatısı (API)         | **FastAPI**, Uvicorn                                                                                                                                                                           |
| LLM Entegrasyonu         | **LangChain**, `langchain-groq` (**Groq** — varsayılan sağlayıcı) ve `langchain-google-genai` (**Gemini**) — `LLM_PROVIDER` ile `.env` üzerinden anında değiştirilebilir, ikisi de lazy-import |
| Yapılandırılmış Çıktı    | **Pydantic** (`with_structured_output`, `json_mode`)                                                                                                                                           |
| Vektör Veritabanı        | **ChromaDB** (kalıcı/`PersistentClient`)                                                                                                                                                       |
| Embedding Modeli         | `sentence-transformers` — `paraphrase-multilingual-MiniLM-L12-v2` (çok dilli, yerel)                                                                                                           |
| Web Kazıma               | `requests`, `BeautifulSoup4`                                                                                                                                                                   |
| PDF Metin Çıkarımı       | `pdfplumber`                                                                                                                                                                                   |
| Yeniden Deneme / Direnç  | `tenacity` (üstel geri çekilme ile retry)                                                                                                                                                      |
| Ortam Değişkeni Yönetimi | `python-dotenv`                                                                                                                                                                                |
| Eşzamanlılık Kontrolü    | `threading` (Lock tabanlı kilit mekanizması)                                                                                                                                                   |

## Kurulum (Installation)

1. **Depoyu klonlayın ve dizine girin:**

   ```powershell
   git clone <repo-url>
   cd kuzka-FonRadarAI
   ```

2. **Sanal ortam (virtual environment) oluşturun ve etkinleştirin:**

   ```powershell
   python -m venv .venv
   .venv\Scripts\Activate.ps1
   ```

3. **Bağımlılıkları yükleyin:**

   ```powershell
   pip install -r requirements.txt
   ```

4. **Ortam değişkenlerini ayarlayın:**
   Proje kök dizininde `.env.example` dosyasını temel alarak bir `.env` dosyası oluşturun:

   ```env
   LLM_PROVIDER=groq
   LLM_API_KEY=gsk_...           # Groq API anahtarınız (https://console.groq.com üzerinden ücretsiz alınabilir)
   LLM_MODEL=openai/gpt-oss-safeguard-20b
   # LLM_MODEL=llama-3.3-70b-versatile   # alternatif model örneği
   ```

   > **Not:** `.env.example` içinde anahtar `LLM_API_KEY_` (sonunda alt çizgi ile) yazılıdır; kendi `.env` dosyanızda bunu `LLM_API_KEY` olarak düzeltmeyi unutmayın, aksi hâlde `llm/config.py` boş anahtar okur.

   **Sağlayıcı değiştirmek** için `LLM_PROVIDER` değerini `gemini` yapmanız yeterlidir; kod tarafında başka hiçbir değişiklik gerekmez:

   ```env
   LLM_PROVIDER=gemini
   LLM_API_KEY=AIza...           # Gemini API anahtarınız
   # LLM_MODEL belirtilmezse sağlayıcıya göre otomatik varsayılan seçilir (groq -> openai/gpt-oss-safeguard-20b, gemini -> gemini-2.5-flash)
   ```

## Kullanım (Usage)

FastAPI sunucusunu, proje kök dizinindeki `mainAPI.py` üzerinden Uvicorn ile başlatın:

```powershell
uvicorn mainAPI:app --reload
```

Sunucu varsayılan olarak `http://127.0.0.1:8000` adresinde ayağa kalkar. Otomatik oluşturulan API dokümantasyonuna aşağıdaki adreslerden erişebilirsiniz:

- **Swagger UI:** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- **ReDoc:** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

İlk çalıştırmada `fonlar.json` ve ChromaDB koleksiyonu henüz mevcut değilse, `/match-score` uç noktasına yapılan ilk istek arka planda otomatik kazıma ve vektörleştirme sürecini tetikler; bu süreç birkaç dakika sürebilir.

## API Uç Noktaları (Endpoints)

### `GET /`

Servisin ayakta olup olmadığını doğrulayan basit sağlık kontrolü (health check) uç noktasıdır.

**Örnek Yanıt:**

```json
{ "status": "online", "message": "FonRadar AI calisiyor" }
```

---

### `GET /match-score`

Kullanıcının serbest metin sorgusunu, ChromaDB'de semantik olarak arar, en alakalı fonları LLM ile tek tek skorlar ve skora göre sıralanmış sonuç listesini döner. Önbellek bayatsa yanıtı **beklemeden** arka planda yenileme başlatır.

**Query Parametreleri:**

| Parametre | Tip                | Açıklama                                                                   |
| --------- | ------------------ | -------------------------------------------------------------------------- |
| `sorgu`   | `string` (zorunlu) | Kurum bilgileri, sektör, konu ve varsa şehir talebini içeren serbest metin |

**Örnek İstek:**

```
GET /match-score?sorgu=İstanbul'da yapay zeka tabanlı bir yazılım girişimiyiz, Ar-Ge desteği arıyoruz
```

**Örnek Yanıt:**

```json
{
  "durum": "basarili",
  "guncelleniyor": false,
  "mesaj": "Sonuclar guncel veriden.",
  "sonuclar": [
    {
      "skor": 87,
      "aciklama": "Bu fon, Türkiye genelinde geçerli bir Ar-Ge destek programıdır...",
      "fon_id": "1501",
      "sehir_durumu": "ulusal",
      "konu": "Yapay Zeka ve Yazılım Ar-Ge",
      "son_basvuru": null,
      "hibe_orani": "%75",
      "baslik": "1501 - Sanayi Ar-Ge Projeleri Destekleme Programı",
      "url": "https://tubitak.gov.tr/tr/destekler/..."
    }
  ]
}
```

---

### `GET /fetch-grants`

`fonlar.json` içindeki tüm fonların başlık ve URL bilgilerini kısa liste hâlinde döner.

**Örnek Yanıt:**

```json
{
  "adet": 42,
  "fonlar": [
    {
      "baslik": "1501 - Sanayi Ar-Ge Projeleri Destekleme Programı",
      "url": "https://tubitak.gov.tr/..."
    }
  ]
}
```

---

### `GET /generate-report`

Şu an için bir **stub (iskelet)** uç noktadır; girdi almaz, yalnızca endpoint'in çalıştığını doğrular. İleride raporlama özelliği için ayrılmıştır.

**Örnek Yanıt:**

```json
{ "durum": "başarılı", "mesaj": "generate-report endpoint çalışıyor." }
```

> **Not:** `tubitak_Scraper/main.py` içinde ayrıca bağımsız, deneysel bir `FastAPI` uygulaması (`is_scraping_active` kilit mantığıyla) yer alır; bu dosya yalnızca kazıma katmanını (`/fetch-grants`) izole biçimde test etmek için kullanılır ve `mainAPI.py`'deki asıl uygulamadan ayrıdır.

## Proje Yapısı (Directory Structure)

```
kuzka-FonRadarAI/
├── mainAPI.py                  # Ana FastAPI uygulaması ve endpoint'ler (/match-score, /fetch-grants, /generate-report)
├── requirements.txt            # Python bağımlılıkları
├── .env.example                # Örnek ortam değişkenleri şablonu
│
├── backend/
│   ├── __init__.py
│   └── app/
│       └── main.py             # (Ayrılmış/boş) uygulama katmanı taslağı
│
├── fon/
│   ├── __init__.py
│   └── fon.py                  # skorlanacak_fonlar(): Chroma araması + JSON'dan tam kayıt eşleştirme + dedup
│
├── llm/
│   ├── __init__.py
│   ├── config.py                # .env'den LLM_PROVIDER / LLM_API_KEY / LLM_MODEL okur
│   ├── llm.py                   # ChatGroq (varsayılan) ve Gemini (opsiyonel, lazy-import) istemci kurulumu
│   └── skorlama.py              # FonSkoru (Pydantic) şeması, tek çağrılı LLM skorlama mantığı, retry/rate-limit yönetimi
│
└── tubitak_Scraper/
    ├── __init__.py
    ├── main.py                  # Kazıma katmanını izole test eden bağımsız FastAPI app'i
    ├── scraper.py                # TÜBİTAK kazıma: link toplama, PDF metin çıkarımı, TTL cache, robots.txt uyumu
    ├── vector_db.py              # Metin temizleme/chunking, ChromaDB indeksleme, JSON-DB senkronizasyonu, semantik arama
    ├── fonlar.json                # Kazınan fon verilerinin önbelleklendiği yerel JSON dosyası (cache)
    └── chroma_db_data/            # ChromaDB kalıcı vektör veritabanı dosyaları (.sync_time dahil)
```
