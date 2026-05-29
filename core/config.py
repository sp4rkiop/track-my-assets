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

    # ---- Database ----------------------------------------------------------
    DATABASE_URL: str = ""

    # ---- External services -------------------------------------------------
    EXTERNAL_API_URL: str = ""
    EXTERNAL_API_KEY: str = ""


settings = Settings()
