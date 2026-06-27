from pydantic import BaseModel, model_validator, Field
import os
import yaml


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class LoggingConfig(BaseModel):
    level: str = "INFO"
    output: str = "file"
    dir: str = "~/.two-api/logs"


class ModelEntry(BaseModel):
    names: list[str | dict[str, str]] = Field(min_length=1)
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None
    max_tokens: int | None = None
    responses_to_chat: bool = False

    @model_validator(mode="after")
    def check_at_least_one_base_url(self):
        if not self.openai_base_url and not self.anthropic_base_url:
            raise ValueError("At least one of openai_base_url or anthropic_base_url must be set")
        return self

    def get_name_map(self) -> dict[str, str]:
        """Return {client_name: backend_model} mapping."""
        result: dict[str, str] = {}
        for item in self.names:
            if isinstance(item, str):
                result[item] = item
            elif isinstance(item, dict):
                result.update(item)
        return result

    @property
    def client_names(self) -> list[str]:
        """All client-facing model names."""
        return list(self.get_name_map().keys())


class CacheConfigModel(BaseModel):
    enabled: bool = True
    ttl_seconds: int = 3600
    max_entries: int = 2000
    aliases: list[str] = []
    key_fields: list[str] = []


class Config(BaseModel):
    server: ServerConfig = ServerConfig()
    models: list[ModelEntry]
    alias: dict[str, str] = {}
    logging: LoggingConfig = LoggingConfig()
    cache: CacheConfigModel = CacheConfigModel()


def load_config(path: str) -> Config:
    path = os.path.expanduser(path)
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return Config(**data)