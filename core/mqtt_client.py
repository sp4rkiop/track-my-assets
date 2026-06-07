import asyncio
from datetime import datetime, timezone
import logging
import json
import math
import aiomqtt
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from core.config import settings
from core.database import PostgreSQLDatabase
from models.tracker import Device, OtaJob, Telemetry

logger = logging.getLogger(__name__)


class MQTTService:
    _client: aiomqtt.Client | None = None
    _listener_task: asyncio.Task | None = None

    @classmethod
    async def initialize(cls):
        """Prepares the MQTT client configuration with plain MQTT and Auth."""

        cls._client = aiomqtt.Client(
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            username=settings.MQTT_USER,
            password=settings.MQTT_PASSWORD,
            # tls_context is intentionally omitted here to use plaintext MQTT over port 1883
            clean_session=False,
            identifier="fastapi_backend",
        )
        logger.info("MQTT Client (Plaintext) configuration initialized.")

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
                        logger.info("Connected to Mosquitto Broker via plain MQTT.")
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
        """Handles incoming payloads, updates Device state, manages OTA jobs, and writes Telemetry."""
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
                # 1. Find or create device
                result = await db.execute(select(Device).where(Device.imei == imei))
                device = result.scalar_one_or_none()

                if not device:
                    logger.info(f"New device detected. Registering IMEI: {imei}")
                    device = Device(name=f"Tracker-{imei[-4:]}", imei=imei)
                    db.add(device)
                    await db.flush()  # Flush to get device.id immediately

                # 2. Update Core Device Metadata (Syncing physical state)
                fw_ver = payload.get("fw_ver")
                iccid = payload.get("iccid")

                if fw_ver and device.firmware_version != fw_ver:
                    device.firmware_version = fw_ver
                if iccid and device.sim_iccid != iccid:
                    device.sim_iccid = iccid

                # 3. Handle OTA Job State Machine
                ota_status = payload.get("ota_status")
                if ota_status:
                    # Find the most recent active OTA job for this device
                    job_query = (
                        select(OtaJob)
                        .where(
                            OtaJob.device_id == device.id,
                            OtaJob.status.in_(["pending", "sent", "downloading"]),
                        )
                        .order_by(OtaJob.created_at.desc())
                        .limit(1)
                    )

                    active_job = (await db.execute(job_query)).scalar_one_or_none()

                    if active_job:
                        active_job.status = ota_status
                        if ota_status in ["success", "failed"]:
                            active_job.completed_at = datetime.now(timezone.utc)
                        logger.info(
                            f"Updated OTA Job {active_job.id} for {imei} to {ota_status}"
                        )

                # 4. Bulletproof Timestamp Parsing
                device_ts_str = payload.get("ts")
                device_ts = None
                if device_ts_str:
                    try:
                        # Replace Z to make it compatible with Python's ISO parser
                        device_ts = datetime.fromisoformat(
                            device_ts_str.replace("Z", "+00:00")
                        )
                    except ValueError:
                        logger.warning(
                            f"Malformed timestamp '{device_ts_str}' from {imei}. Falling back to server time."
                        )

                if not device_ts:
                    device_ts = datetime.now(timezone.utc)

                # 5. Compute IMU magnitude
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

                # 6. Upsert telemetry — ON CONFLICT DO NOTHING handles QoS-1 duplicates.
                stmt = (
                    insert(Telemetry)
                    .values(
                        device_id=device.id,
                        device_ts=device_ts,
                        event_type=payload.get("event", "PERIODIC"),
                        latitude=payload.get("lat"),
                        longitude=payload.get("lon"),
                        altitude_m=payload.get("alt"),
                        speed_kmh=payload.get("speed"),
                        heading_deg=payload.get("heading"),
                        accuracy_m=payload.get("accuracy"),
                        hdop=payload.get("hdop"),
                        fix_quality=payload.get("fix_quality"),
                        gps_ttff=payload.get("ttff"),
                        rssi=payload.get("rssi"),
                        rsrp=payload.get("rsrp"),
                        rsrq=payload.get("rsrq"),
                        cell_id=payload.get("cell_id"),
                        cpsi_raw=payload.get("cpsi"),
                        battery_voltage=payload.get("bat_v"),
                        battery_pct=payload.get("bat_pct"),
                        accel_x=ax,
                        accel_y=ay,
                        accel_z=az,
                        accel_magnitude=magnitude,
                        is_moving=magnitude > 1.2 if magnitude is not None else None,
                    )
                    .on_conflict_do_nothing(index_elements=["device_id", "device_ts"])
                )
                await db.execute(stmt)

                # 7. Update device quick-status
                device.last_seen = datetime.now(timezone.utc)

        except json.JSONDecodeError:
            logger.error(
                f"Failed to decode MQTT payload from topic {message.topic}. Invalid JSON."
            )
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}", exc_info=True)

    @classmethod
    async def publish_command(cls, imei: str, payload: dict):
        """Pushes a command directly to a specific tracker."""
        if cls._client is None:
            logger.error("Cannot publish: MQTT client is not initialized.")
            return False

        topic = f"trackers/{imei}/commands"
        try:
            # QoS 1 ensures the tower holds the message if the bike is in a tunnel
            await cls._client.publish(topic, payload=json.dumps(payload), qos=1)
            logger.info(f"Successfully published command to {topic}: {payload}")
            return True
        except Exception as e:
            logger.error(f"Failed to publish to {topic}: {e}")
            return False

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
