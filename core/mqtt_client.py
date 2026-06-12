import asyncio
from datetime import datetime, timezone
import hashlib
import logging
import json
import math
import aiomqtt
import ssl
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
        """Prepares the MQTT client configuration with dynamic TLS based on environment settings."""
        use_tls = settings.MQTT_TLS

        if use_tls:
            tls_context = ssl.create_default_context()
            # tls_context.check_hostname = False  # Uncomment if using self-signed certs
            # tls_context.verify_mode = ssl.CERT_NONE
            logger.info("MQTT Client configuration initialized WITH TLS (MQTTS).")
        else:
            tls_context = None
            logger.info(
                "MQTT Client configuration initialized WITHOUT TLS (Plaintext)."
            )

        cls._client = aiomqtt.Client(
            hostname=settings.MQTT_HOST,
            port=settings.MQTT_PORT,
            username=settings.MQTT_USER,
            password=settings.MQTT_PASSWORD,
            tls_context=tls_context,
            clean_session=False,
            identifier=settings.MQTT_CLIENT_ID,
        )

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
                        logger.info("Connected to Mosquitto Broker.")
                        await client.subscribe("trackers/+/location", qos=1)

                        async for message in client.messages:
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

                is_new_device = False
                if not device:
                    logger.info(f"New device detected. Registering IMEI: {imei}")
                    hex_id = hashlib.md5(imei.encode()).hexdigest()[:6].upper()
                    device_name = f"Tracker-{hex_id}"
                    device = Device(name=device_name, imei=imei)
                    db.add(device)
                    await db.flush()
                    is_new_device = True

                # 2. Extract Extended Metadata
                fw_ver = payload.get("fw_ver")
                iccid = payload.get("iccid")
                hw_mfg = payload.get("hw_mfg")
                hw_model = payload.get("hw_model")
                hw_rev = payload.get("hw_rev")

                metadata_changed = False
                if fw_ver and device.firmware_version != fw_ver:
                    device.firmware_version = fw_ver
                    metadata_changed = True
                if iccid and device.sim_iccid != iccid:
                    device.sim_iccid = iccid
                    metadata_changed = True

                # --- TRIGGER HA DISCOVERY ---
                # Republish HA configs if it's a new device OR if firmware/SIM changed
                if is_new_device or metadata_changed:
                    asyncio.create_task(
                        cls.publish_ha_discovery(
                            imei=device.imei,
                            device_name=device.name,
                            fw_ver=device.firmware_version,
                            hw_mfg=hw_mfg,
                            hw_model=hw_model,
                            hw_rev=hw_rev,
                        )
                    )

                # 3. Handle OTA Job State Machine
                ota_status = payload.get("ota_status")
                if ota_status:
                    # Find the most recent active OTA job for this device
                    job_query = (
                        select(OtaJob)
                        .where(
                            OtaJob.device_id == device.id,
                            OtaJob.status.in_(
                                ["pending", "sent", "queued", "downloading"]
                            ),
                        )
                        .order_by(OtaJob.created_at.desc())
                        .limit(1)
                    )

                    active_job = (await db.execute(job_query)).scalar_one_or_none()

                    if active_job:
                        active_job.status = ota_status

                        # Catch "success" or ANY of the new "failed_..." strings
                        if ota_status == "success" or ota_status.startswith("failed"):
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

                # 6. Upsert telemetry
                stmt = (
                    insert(Telemetry)
                    .values(
                        device_id=device.id,
                        device_ts=device_ts,
                        event_type=payload.get("event", "PERIODIC"),
                        latitude=payload.get("latitude"),
                        longitude=payload.get("longitude"),
                        altitude_m=payload.get("alt"),
                        speed_kmh=payload.get("speed"),
                        heading_deg=payload.get("heading"),
                        accuracy_m=payload.get("accuracy"),
                        hdop=payload.get("hdop"),
                        satellites=payload.get("gnss_sats"),
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
    async def publish_ha_discovery(
        cls,
        imei: str,
        device_name: str,
        fw_ver: str | None = None,
        hw_mfg: str | None = None,
        hw_model: str | None = None,
        hw_rev: str | None = None,
    ):
        """Publishes Home Assistant Auto-Discovery configurations."""
        if cls._client is None:
            return

        # 1. Dynamic Device Registry
        ha_device = {
            "identifiers": [f"tracker_{imei}"],
            "name": device_name,
            "manufacturer": hw_mfg if hw_mfg else "Waveshare",
            "model": hw_model if hw_model else "SIM7670G ESP32 Tracker",
        }

        if fw_ver:
            ha_device["sw_version"] = fw_ver
        if hw_rev:
            ha_device["hw_version"] = hw_rev

        state_topic = f"trackers/{imei}/location"

        # --- SENSOR CONFIGURATIONS ---
        configs = {}

        # Tracker (GPS Map)
        configs["device_tracker"] = {
            "name": "Location",
            "has_entity_name": True,
            "unique_id": f"{imei}_tracker",
            "json_attributes_topic": state_topic,
            "source_type": "gps",
            "device": ha_device,
        }

        # Battery Percentage
        configs["sensor_battery"] = {
            "name": "Battery",
            "has_entity_name": True,
            "unique_id": f"{imei}_battery",
            "state_topic": state_topic,
            "value_template": "{{ value_json.bat_pct }}",
            "device_class": "battery",
            "unit_of_measurement": "%",
            "device": ha_device,
        }

        # Battery Voltage
        configs["sensor_voltage"] = {
            "name": "Voltage",
            "has_entity_name": True,
            "unique_id": f"{imei}_voltage",
            "state_topic": state_topic,
            "value_template": "{{ value_json.bat_v | float(0) }}",
            "device_class": "voltage",
            "unit_of_measurement": "V",
            "suggested_display_precision": 2,
            "device": ha_device,
        }

        # Speed
        configs["sensor_speed"] = {
            "name": "Speed",
            "has_entity_name": True,
            "unique_id": f"{imei}_speed",
            "state_topic": state_topic,
            "value_template": "{{ value_json.speed }}",
            "device_class": "speed",
            "unit_of_measurement": "km/h",
            "device": ha_device,
        }

        # Event
        configs["sensor_event"] = {
            "name": "Event",
            "has_entity_name": True,
            "unique_id": f"{imei}_event",
            "state_topic": state_topic,
            "value_template": "{{ value_json.event }}",
            "icon": "mdi:bell-ring",
            "device": ha_device,
        }

        # Cellular Signal
        configs["sensor_cellular"] = {
            "name": "Cellular CPSI",
            "unique_id": f"{imei}_cpsi",
            "state_topic": state_topic,
            "value_template": "{{ value_json.cpsi }}",
            "icon": "mdi:cellphone-wireless",
            "entity_category": "diagnostic",
            "device": ha_device,
        }

        # GNSS Satellites
        configs["sensor_sats"] = {
            "name": "Satellites",
            "unique_id": f"{imei}_sats",
            "state_topic": state_topic,
            "value_template": "{{ value_json.gnss_sats }}",
            "icon": "mdi:satellite-variant",
            "entity_category": "diagnostic",
            "device": ha_device,
        }

        # GNSS HDOP (Accuracy)
        configs["sensor_hdop"] = {
            "name": "HDOP",
            "unique_id": f"{imei}_hdop",
            "state_topic": state_topic,
            "value_template": "{{ value_json.hdop }}",
            "icon": "mdi:crosshairs-gps",
            "entity_category": "diagnostic",
            "device": ha_device,
        }

        # Motion Binary Sensor
        configs["binary_sensor_motion"] = {
            "name": "Moving",
            "has_entity_name": True,
            "unique_id": f"{imei}_motion",
            "state_topic": state_topic,
            "value_template": "{% if value_json.speed | float > 2.0 %}ON{% else %}OFF{% endif %}",
            "device_class": "moving",
            "device": ha_device,
        }

        # Publish all configs
        try:
            publish_tasks = [
                cls._client.publish(
                    f"homeassistant/device_tracker/{imei}/config",
                    payload=json.dumps(configs["device_tracker"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_battery/config",
                    payload=json.dumps(configs["sensor_battery"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_voltage/config",
                    payload=json.dumps(configs["sensor_voltage"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_speed/config",
                    payload=json.dumps(configs["sensor_speed"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_event/config",
                    payload=json.dumps(configs["sensor_event"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_cellular/config",
                    payload=json.dumps(configs["sensor_cellular"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_sats/config",
                    payload=json.dumps(configs["sensor_sats"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/sensor/{imei}_hdop/config",
                    payload=json.dumps(configs["sensor_hdop"]),
                    retain=True,
                ),
                cls._client.publish(
                    f"homeassistant/binary_sensor/{imei}_motion/config",
                    payload=json.dumps(configs["binary_sensor_motion"]),
                    retain=True,
                ),
            ]
            await asyncio.gather(*publish_tasks)

            logger.info(
                f"Published extended HA Auto-Discovery for {imei} (FW: {fw_ver})"
            )
        except Exception as e:
            logger.error(f"Failed to publish HA discovery: {e}")

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
