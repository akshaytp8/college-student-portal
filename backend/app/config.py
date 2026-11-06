from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    secret_key: str = "dev-only-change-this-secret-before-deployment"
    database_url: str = "sqlite:///./college_portal.db"
    frontend_origins: str = "http://localhost:5173"
    cookie_secure: bool = False
    cookie_samesite: str = "strict"
    access_token_minutes: int = 30
    integration_api_key: str = "dev-integration-key-change-me"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [origin.strip() for origin in self.frontend_origins.split(",") if origin.strip()]


settings = Settings()
if settings.app_env.lower() == "production":
    if len(settings.secret_key) < 32 or settings.secret_key.startswith("dev-"):
        raise RuntimeError("Set a strong SECRET_KEY (32+ characters) in production.")
    if not settings.cookie_secure:
        raise RuntimeError("COOKIE_SECURE must be true in production (HTTPS required).")
