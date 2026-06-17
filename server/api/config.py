"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://agrimesh:agrimesh_secret@localhost:5432/agrimesh"
    jwt_secret: str = "change-this-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440  # 24 hours
    edge_timeout_seconds: int = 120          # mark offline after 2 min no heartbeat

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
