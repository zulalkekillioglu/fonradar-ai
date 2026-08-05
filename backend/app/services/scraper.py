import httpx
from bs4 import BeautifulSoup
from typing import List
from app.schemas.fund import FundSchema

class FundScraper:
    def __init__(self):
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }

    async def fetch_funds(self) -> List[FundSchema]:
        """
        Fon ve hibe duyurularını getiren ve il/konu detayları içeren servis.
        """
        mock_funds = [
            FundSchema(
                id="fon-101",
                title="Yapay Zeka ve Yazılım Teknolojileri Hibe Programı",
                organization="TÜBİTAK",
                description="Yazılım, yapay zeka ve veri analitiği odaklı AR-GE projelerine %75 hibe desteği sağlanacaktır.",
                cities=["Ankara", "İstanbul", "İzmir"],
                topics=["Yazılım", "Yapay Zeka", "Teknoloji"],
                budget="1.500.000 TL",
                deadline="2026-10-15",
                source_url="https://tubitak.gov.tr/example-101"
            ),
            FundSchema(
                id="fon-102",
                title="Kırsal Kalkınma ve Modern Tarım Desteği",
                organization="KOSGEB",
                description="Tarımda dijitalleşme ve sürdürülebilir sulama projeleri geliştiren KOBİ'lere ekipman desteği.",
                cities=["Konya", "Adana", "Bursa", "Tüm İller"],
                topics=["Tarım", "Sürdürülebilirlik", "KOBİ"],
                budget="750.000 TL",
                deadline="2026-11-01",
                source_url="https://kosgeb.gov.tr/example-102"
            ),
            FundSchema(
                id="fon-103",
                title="Yenilenebilir Enerji ve Yeşil Dönüşüm Fonu",
                organization="Ticaret Bakanlığı",
                description="Güneş ve rüzgar enerjisi depolama sistemleri üreten yerli girişimciler için finansman.",
                cities=["Tüm İller"],
                topics=["Enerji", "Yeşil Dönüşüm", "Üretim"],
                budget="3.000.000 TL",
                deadline="2026-12-31",
                source_url="https://ticaret.gov.tr/example-103"
            )
        ]
        return mock_funds