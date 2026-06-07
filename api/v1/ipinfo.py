from fastapi import APIRouter, Request
from services.geoip_service import get_ip_info
from schemas.ip_schema import IPInfoResponse

router = APIRouter()


@router.get("/", response_model=IPInfoResponse)
async def ip_information(request: Request):
    client = request.client
    if client is not None:
        ip = client.host
        # Delegate the actual lookup work to the service
        return await get_ip_info(ip)
    else:
        return IPInfoResponse(ip="unknown", error="Client information not available")
