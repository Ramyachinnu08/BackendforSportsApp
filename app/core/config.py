"""Application configuration.

Every tunable lives here and is overridable via environment variables /
a `.env` file (see `.env.example`).  Scoring weights are deliberately
config, not code, so product can tune them without a schema change.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- core ---------------------------------------------------------
    app_name: str = "SportyQo API"
    api_version: str = "1.0.0"
    environment: str = "development"  # development | staging | production
    base_url: str = "http://localhost:8000"
    share_base_url: str = "https://sportyqo.app"

    database_url: str = "postgresql+asyncpg://sportyqo:sportyqo@localhost:5432/sportyqo"
    cors_origins: str = "*"  # comma separated; Flutter Web origin(s) in prod

    # --- auth ---------------------------------------------------------
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 15 * 60          # 15 min
    refresh_token_ttl_days: int = 30                 # 30 days, rotating
    password_min_length: int = 8
    account_deletion_grace_days: int = 30

    # --- OTP (coach onboarding) ----------------------------------------
    otp_ttl_seconds: int = 300
    otp_max_attempts: int = 5
    otp_send_limit: int = 3          # per window
    otp_send_window_minutes: int = 15
    otp_dev_echo: bool = True        # include code in response in development only

    # --- providers ------------------------------------------------------
    sms_provider: str = "console"     # console | msg91 | twilio
    email_provider: str = "console"   # console | smtp | ses
    push_provider: str = "console"    # console | fcm
    storage_provider: str = "local"   # local | db | cloudinary | s3     cloudinary_cloud_name: str = ""     cloudinary_api_key: str = ""     cloudinary_api_secret: str = ""
    storage_dir: str = "./storage"
    signed_url_ttl_seconds: int = 600

    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    email_from: str = "no-reply@sportyqo.com"

    msg91_auth_key: str = ""
    twilio_sid: str = ""
    twilio_token: str = ""
    twilio_from: str = ""

    fcm_service_account_json: str = ""
    s3_bucket: str = ""
    s3_region: str = "ap-south-1"
    cdn_base_url: str = ""

    # --- admin ----------------------------------------------------------
    admin_api_key: str = "change-me-admin-key"

    # --- media constraints (bytes) ---------------------------------------
    max_avatar_bytes: int = 5 * 1024 * 1024
    max_logo_bytes: int = 2 * 1024 * 1024
    max_image_bytes: int = 10 * 1024 * 1024
    max_video_bytes: int = 200 * 1024 * 1024
    max_cert_doc_bytes: int = 10 * 1024 * 1024
    max_post_media: int = 10
    max_cert_documents: int = 5

    # --- Qo Score weights (tunable) --------------------------------------
    points_per_run: float = 0.5
    points_per_wicket: int = 20
    points_per_catch: int = 10
    points_mom_bonus: int = 25
    points_win_bonus: int = 15
    points_participation: int = 5
    points_per_post: int = 12
    points_per_recommendation: int = 25
    recommendation_cooldown_days: int = 30

    # --- misc -------------------------------------------------------------
    max_players_per_team: int = 30
    feed_default_limit: int = 20

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
