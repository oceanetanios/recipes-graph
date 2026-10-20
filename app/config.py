"""Application settings loaded from environment variables and the .env file."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration values for the API, read from the environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = ""
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.5-flash"
    # Tried in this order when the main model fails (each model has its own free daily quota).
    gemini_fallback_models: str = "gemini-3-flash-preview,gemini-3.1-flash-lite"
    api_key: str = ""


settings = Settings()
