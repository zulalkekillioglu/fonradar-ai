from fastapi import FastAPI, Query
from typing import List, Optional
from app.schemas.fund import FundSchema
from app.services.scraper import FundScraper

app = FastAPI(
    title="Fonradar AI Backend API",
    description="Fon ve Hibe Takip Platformu API Servisi",
    version="1.0.0"
)

scraper = FundScraper()

@app.get("/")
def home():
    return {
        "status": "online",
        "message": "Fonradar AI Backend Servisi Çalışıyor!",
        "has_gemini_key": True,
        "has_groq_key": True
    }

@app.get("/api/v1/funds", response_model=List[FundSchema])
async def get_funds(
    city: Optional[str] = Query(None, description="Filtrelenecek İl (örn: Ankara, İstanbul)"),
    topic: Optional[str] = Query(None, description="Filtrelenecek Konu/Sektör (örn: Yazılım, Tarım)")
):
    """
    Tüm fon ilanlarını getiren ve il/konu parametrelerine göre filtreleyen endpoint.
    """
    all_funds = await scraper.fetch_funds()
    
    # İl Filtrelemesi
    if city:
        all_funds = [
            f for f in all_funds 
            if city.lower() in [c.lower() for c in f.cities] or "Tüm İller" in f.cities
        ]
        
    # Konu Filtrelemesi
    if topic:
        all_funds = [
            f for f in all_funds 
            if topic.lower() in [t.lower() for t in f.topics]
        ]
        
    return all_funds