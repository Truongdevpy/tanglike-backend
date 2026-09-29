import os
from typing import List
from pydantic import model_validator, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    APP_ENV: str = "development"
    APP_NAME: str = "TangLike SMM Panel"
    APP_VERSION: str = "1.0.0"

    DATABASE_URL: str = "sqlite+aiosqlite:///./tanglike.db"
    REDIS_URL: str = "redis://localhost:6379/0"

    JWT_SECRET: str = ""
    JWT_REFRESH_SECRET: str = ""
    # Dedicated encryption material for provider credentials; do not rotate it
    # with access-token signing keys.
    SECRET_KEY: str = ""
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    ALGORITHM: str = "HS256"

    # Providers
    TTC_API_URL: str = "https://tuongtaccheo.com/api/v2"
    TTC_ACCESS_TOKEN: str = ""
    THMXH_API_URL: str = "https://thmxh.com/api/v2"
    THMXH_API_KEY: str = ""
    THMXH_USD_RATE: float = 28000.0
    TTC_API_KEY: str = ""
    TTC_USERNAME: str = ""
    TTC_PASSWORD: str = ""

    # Telegram
    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_ADMIN_CHAT_ID: str = ""

    # Payment
    PAYMENT_WEBHOOK_SECRET: str = ""
    BANK_NAME: str = ""
    BANK_ACCOUNT_NO: str = ""
    BANK_ACCOUNT_HOLDER: str = ""
    MB_BANK_API_URL: str = ""
    MB_BANK_API_TOKEN: str = ""
    MB_BANK_POLL_INTERVAL_SECONDS: int = 60
    DEPOSIT_CONTENT_PREFIX: str = "NAP"

    # Email is optional in development but required to deliver production password resets.
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_STARTTLS: bool = True
    PUBLIC_APP_URL: str = ""

    # Production bootstrap is deliberately opt-in; never ship usable accounts.
    BOOTSTRAP_ADMIN_USERNAME: str = ""
    BOOTSTRAP_ADMIN_EMAIL: str = ""
    BOOTSTRAP_ADMIN_PASSWORD: str = ""

    # Business rules
    DEFAULT_MARKUP_PERCENT: float = 30.0
    DEFAULT_REFERRAL_COMMISSION_PERCENT: float = 5.0
    MIN_DEPOSIT_AMOUNT: float = 10000.0
    REGISTRATION_ENABLED: bool = True
    JOB_RUN_RETENTION_DAYS: int = 90
    PASSWORD_RESET_TOKEN_RETENTION_DAYS: int = 7
    AUDIT_LOG_RETENTION_DAYS: int = 365
    PRICE_SYNC_LOG_RETENTION_DAYS: int = 180
    PROVIDER_MAX_RETRY_ATTEMPTS: int = 3
    PROVIDER_SYNC_INTERVAL_SECONDS: int = 900
    USER_API_RATE_LIMIT_PER_MINUTE: int = 120

    # CORS
    CORS_ORIGINS: List[str] = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "http://localhost:3001",
        "http://127.0.0.1:3001",
        "http://localhost:8000",
        "http://127.0.0.1:8000",
        "https://tanglike.vercel.app",
        "https://tanglike.io.vn",
        "https://www.tanglike.io.vn",
    ]
    # Only these reverse proxies may provide a client address through X-Forwarded-For.
    TRUSTED_PROXY_IPS: List[str] = ["127.0.0.1", "::1"]

    @field_validator("CORS_ORIGINS", "TRUSTED_PROXY_IPS", mode="before")
    @classmethod
    def parse_string_list(cls, v):
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                import json
                try:
                    return json.loads(v)
                except Exception:
                    pass
            return [item.strip() for item in v.split(",") if item.strip()]
        return v

    @model_validator(mode="after")
    def validate_production_secrets(self):
        if self.APP_ENV.lower() == "production":
            required = ("JWT_SECRET", "JWT_REFRESH_SECRET", "SECRET_KEY", "PAYMENT_WEBHOOK_SECRET")
            missing = [name for name in required if not getattr(self, name)]
            if missing:
                raise ValueError(f"Missing required production environment variables: {', '.join(missing)}")
            if self.JWT_SECRET == self.JWT_REFRESH_SECRET:
                raise ValueError("JWT_SECRET and JWT_REFRESH_SECRET must be different in production")
            weak = [name for name in ("JWT_SECRET", "JWT_REFRESH_SECRET", "SECRET_KEY") if len(getattr(self, name)) < 32]
            if weak:
                raise ValueError(f"Production secrets must be at least 32 characters: {', '.join(weak)}")
        return self

settings = Settings()
