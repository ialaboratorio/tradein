from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

PLACEHOLDER_SECRETS = {"cambiate-en-el-env", "pon-aqui-una-clave-secreta-larga"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "TradeIn"
    secret_key: str = "cambiate-en-el-env"
    public_base_url: str = ""
    session_https_only: bool = False
    enable_scheduler: bool = True

    google_client_id: str = ""
    google_client_secret: str = ""
    admin_emails: str = ""

    database_url: str = "sqlite:///./data/tradein.db"

    coef_cash: float = 0.7
    coef_store: float = 0.805
    supported_games: str = "1,6,18,24"
    price_alert_pct: float = 10.0
    price_cache_ttl_hours: int = 24
    sync_time: str = "03:30"
    sync_timezone: str = "Europe/Madrid"

    def validate_runtime(self) -> None:
        if self.secret_key in PLACEHOLDER_SECRETS:
            raise RuntimeError(
                "SECRET_KEY sigue con el valor de plantilla. Genera uno con: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )

    def admin_email_set(self) -> set[str]:
        return {e.strip().lower() for e in self.admin_emails.split(",") if e.strip()}

    def game_ids(self) -> list[int]:
        return [int(g.strip()) for g in self.supported_games.split(",") if g.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
