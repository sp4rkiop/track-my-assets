from pydantic import BaseModel
from typing import Optional


class IPInfoResponse(BaseModel):
    ip: str
    asn: Optional[int] = None
    isp: Optional[str] = None
    country: Optional[str] = None
    city: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    note: Optional[str] = None
    error: Optional[str] = None
