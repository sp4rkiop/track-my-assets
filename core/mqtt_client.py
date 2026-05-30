import asyncio
import logging
import json
import ssl
import aiomqtt
from core.config import settings
from core.database import PostgreSQLDatabase
from models.tracker import Telemetry

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
                        await client.subscribe("trackers/+/location")

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
        """Handles incoming payloads and writes them to PostgreSQL."""
        try:
            # We must verify the payload is not None before decoding
            if message.payload is None:
                return

            # payload can be bytes, bytearray, etc. decode it.
            raw_payload = (
                message.payload.decode()
                if isinstance(message.payload, (bytes, bytearray))
                else str(message.payload)
            )
            payload = json.loads(raw_payload)

            # message.topic can be accessed as a string
            topic = str(message.topic)
            device_id = topic.split("/")[1]

            logger.info(f"Incoming telemetry from {device_id}: {payload}")

            async with PostgreSQLDatabase.get_session() as db:
                telemetry = Telemetry(
                    event_type=payload.get("event"),
                    battery_voltage=payload.get("battery"),
                    cpsi=payload.get("cpsi"),
                    accel_x=payload.get("accel_x"),
                    accel_y=payload.get("accel_y"),
                    accel_z=payload.get("accel_z"),
                )
                db.add(telemetry)

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
