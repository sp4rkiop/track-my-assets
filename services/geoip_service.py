import os
import ipaddress
import geoip2.database
import geoip2.errors
import asyncio
from schemas.ip_schema import IPInfoResponse

ASN_DB_PATH = "./utils/GeoLite2-ASN.mmdb"
CITY_DB_PATH = "./utils/GeoLite2-City.mmdb"

_asn_reader: geoip2.database.Reader | None = None
_city_reader: geoip2.database.Reader | None = None


async def load_readers():
    """Closes old readers and loads the newest databases asynchronously."""

    def _reload():
        global _asn_reader, _city_reader
        if _asn_reader:
            _asn_reader.close()
        if _city_reader:
            _city_reader.close()

        if os.path.exists(ASN_DB_PATH) and os.path.exists(CITY_DB_PATH):
            _asn_reader = geoip2.database.Reader(ASN_DB_PATH)
            _city_reader = geoip2.database.Reader(CITY_DB_PATH)

    # File handler manipulation runs in thread
    await asyncio.to_thread(_reload)


async def close_readers():
    def _close():
        if _asn_reader:
            _asn_reader.close()
        if _city_reader:
            _city_reader.close()

    await asyncio.to_thread(_close)


async def is_special_ip(ip: str) -> bool:
    try:
        ip_obj = ipaddress.ip_address(ip)
        return (
            ip_obj.is_private
            or ip_obj.is_loopback
            or ip_obj.is_reserved
            or ip_obj.is_link_local
        )
    except ValueError:
        return True


def _sync_lookup(
    asn_reader: geoip2.database.Reader,
    city_reader: geoip2.database.Reader,
    target_ip: str,
) -> IPInfoResponse:
    try:
        asn_data = asn_reader.asn(target_ip)
        city_data = city_reader.city(target_ip)

        return IPInfoResponse(
            ip=target_ip,
            asn=asn_data.autonomous_system_number,
            isp=asn_data.autonomous_system_organization,
            country=city_data.country.name if city_data.country else None,
            city=city_data.city.name if city_data.city else None,
            latitude=city_data.location.latitude if city_data.location else None,
            longitude=city_data.location.longitude if city_data.location else None,
        )
    except geoip2.errors.AddressNotFoundError:
        return IPInfoResponse(ip=target_ip, error="IP address not found in database")


async def get_ip_info(ip: str) -> IPInfoResponse:
    # Bind globals to local variables
    asn_r = _asn_reader
    city_r = _city_reader

    # Type checker now knows that past this point, asn_r and city_r are NOT None
    if not asn_r or not city_r:
        return IPInfoResponse(
            ip=ip,
            error="GeoIP databases are not currently available. They may be downloading.",
        )

    if await is_special_ip(ip):
        return IPInfoResponse(
            ip=ip,
            note="Special/Private IP address - no public geolocation available",
        )

    # Pass the explicitly checked local variables into the thread
    return await asyncio.to_thread(_sync_lookup, asn_r, city_r, ip)
