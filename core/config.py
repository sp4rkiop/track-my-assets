from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # ---- App ---------------------------------------------------------------
    APP_NAME: str = "fastapi-starter"
    APP_VERSION: str = "0.1.0"
    CORS_ORIGINS: list[str] = Field(default_factory=lambda: ["*"])
    DEBUG: bool = False
    LOG_LEVEL: str = "INFO"
    CACHE_EXPIRY: int = 3600

    # ---- Database ----------------------------------------------------------
    DB_NAME: str = "postgres"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "dev_password"
    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_PASSWORD: str = "dev_password"

    # ---- MQTT --------------------------------------------------------------
    MQTT_HOST: str = Field(default="localhost")
    MQTT_PORT: int = Field(default=8883)
    MQTT_USER: str | None = Field(default="devuser")
    MQTT_PASSWORD: str | None = Field(default="devpassword")


settings = Settings()
