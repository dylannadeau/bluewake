"""Application settings, loaded from environment (.env in dev, injected in prod)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://bluewake:bluewake@localhost:5432/bluewake"

    # Data source credentials (all free tier). Empty string = source disabled.
    aisstream_api_key: str = ""
    aishub_username: str = ""
    gfw_api_token: str = ""

    # OFAC SDN is unauthenticated; URL pinned here so it's overridable.
    ofac_sdn_url: str = "https://www.treasury.gov/ofac/downloads/sdn.csv"
    ofac_alt_url: str = "https://www.treasury.gov/ofac/downloads/alt.csv"

    # Phase 4: email alerts
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    alert_from_address: str = "alerts@bluewake.example"

    report_storage_dir: str = "/data/reports"

    cors_origins: list[str] = ["http://localhost:3000"]


settings = Settings()
