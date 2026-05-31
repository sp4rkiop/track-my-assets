import asyncio
from datetime import datetime, timezone
import logging
import json
import math
import ssl
import aiomqtt
from sqlalchemy import select
from core.config import settings
from core.database import PostgreSQLDatabase
from models.tracker import Device, Telemetry

logger = logging.getLogger(__name__)


class MQTTService:
    _client: aiomqtt.Client | None = None
    _listener_task: asyncio.Task | None = None

    @classmethod
    async def initialize(cls):
        """Prepares the MQTT client configuration with MQTTS (TLS) and Auth."""

        # Configure TLS context for MQTTS
        # create_default_context() is perfect if Mosquitto uses Let's Encrypt/Public Certs.
        tls_context = ssl.create_default_context()

        # Optional: If you are using self-signed certs in local dev, uncomment below:
        tls_context.check_hostname = False
        tls_context.verify_mode = ssl.CERT_NONE

        cls._client = aiomqtt.Client(
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            username=settings.MQTT_USER,
            password=settings.MQTT_PASSWORD,
            tls_context=tls_context,  # <-- This enables MQTTS (Port 8883)
            clean_session=False,
            identifier="fastapi_backend",
        )
        logger.info("MQTT Client (MQTTS) configuration initialized.")

    @classmethod
    async def start_listening(cls):
        """Starts the non-blocking background subscription loop."""

        if cls._client is None:
            raise RuntimeError(
                "MQTT Client is not initialized. Call initialize() first."
            )

        client = cls._client

        async def listen_loop():
            reconnect_interval = 3
            while True:
                try:
                    async with client:
                        logger.info("Connected securely to Mosquitto Broker via MQTTS.")
                        await client.subscribe("trackers/+/location", qos=1)

                        async for message in client.messages:
                            # Process messages asynchronously
                            asyncio.create_task(cls._process_message(message))

                except aiomqtt.MqttError as error:
                    logger.error(
                        f"MQTT Connection lost: {error}. Reconnecting in {reconnect_interval}s..."
                    )
                    await asyncio.sleep(reconnect_interval)
                except asyncio.CancelledError:
                    logger.info("MQTT Listener loop cancelled.")
                    break
                except Exception as e:
                    logger.error(f"Unexpected error in MQTT listener: {e}")
                    await asyncio.sleep(reconnect_interval)

        cls._listener_task = asyncio.create_task(listen_loop())

    @classmethod
    async def _process_message(cls, message: aiomqtt.Message):
        """Handles incoming payloads, maps IMEI to Device, and writes to TimescaleDB."""
        try:
            if message.payload is None:
                return

            # Decode payload
            raw_payload = (
                message.payload.decode()
                if isinstance(message.payload, (bytes, bytearray))
                else str(message.payload)
            )
            payload = json.loads(raw_payload)

            # Extract IMEI from topic: e.g., trackers/123456789012345/location
            topic = str(message.topic)
            imei = topic.split("/")[1]

            logger.info(f"Incoming telemetry from {imei}: {payload}")

            async with PostgreSQLDatabase.get_session() as db:
                # 1. Find or Create Device
                result = await db.execute(select(Device).where(Device.imei == imei))
                device = result.scalar_one_or_none()

                if not device:
                    logger.info(f"New device detected. Registering IMEI: {imei}")
                    device = Device(name=f"Tracker-{imei[-4:]}", imei=imei)
                    db.add(device)
                    await db.flush()  # Flush to generate the device.id UUID immediately

                # 2. Parse Timestamp (Fallback to server time if device has no RTC lock)
                device_ts_str = payload.get("ts")
                if device_ts_str:
                    # Replace Z with +00:00 for Python ISO format compatibility
                    device_ts = datetime.fromisoformat(
                        device_ts_str.replace("Z", "+00:00")
                    )
                else:
                    device_ts = datetime.now(timezone.utc)

                ax, ay, az = (
                    payload.get("accel_x"),
                    payload.get("accel_y"),
                    payload.get("accel_z"),
                )
                magnitude = (
                    math.sqrt(ax**2 + ay**2 + az**2)
                    if all(v is not None for v in (ax, ay, az))
                    else None
                )

                # 3. Create Telemetry Record
                telemetry = Telemetry(
                    device_id=device.id,
                    device_ts=device_ts,
                    event_type=payload.get("event", "PERIODIC"),
                    latitude=payload.get("lat"),
                    longitude=payload.get("lon"),
                    altitude_m=payload.get("alt"),
                    speed_kmh=payload.get("speed"),
                    battery_voltage=payload.get("bat_v"),
                    battery_pct=payload.get("bat_pct"),
                    cpsi_raw=payload.get("cpsi"),
                    gps_ttff=payload.get("ttff"),
                    accel_x=ax,
                    accel_y=ay,
                    accel_z=az,
                    accel_magnitude=magnitude,
                    is_moving=magnitude > 1.2
                    if magnitude is not None
                    else None,  # tune threshold
                )
                db.add(telemetry)

                # 4. Update Device Quick-Status
                device.last_seen = datetime.now(timezone.utc)

                # Commit is handled automatically by the get_session context manager

        except json.JSONDecodeError:
            logger.error("Failed to decode MQTT payload. Invalid JSON.")
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}")

    @classmethod
    async def close_connection(cls):
        """Gracefully shuts down the listener."""
        if cls._listener_task:
            cls._listener_task.cancel()
            try:
                await cls._listener_task
            except asyncio.CancelledError:
                pass
        logger.info("MQTT background listener stopped.")
