from pydantic import BaseModel
from typing import Optional, List

class FundSchema(BaseModel):
    id: str
    title: str                  
    organization: str           
    description: str            
    cities: List[str]           
    topics: List[str]           
    budget: Optional[str] = None      
    deadline: Optional[str] = None     
    source_url: str            