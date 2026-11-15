from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://sport:sport@localhost/sport_center"
    redis_url: str = "redis://localhost:6379/0"
    jwt_secret: str = "local-development-change-this-secret"
    access_token_minutes: int = 720
    cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(case_sensitive=False)


settings = Settings()
