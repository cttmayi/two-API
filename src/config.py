from pydantic import BaseModel, model_validator, Field
import yaml


class ServerConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080


class LoggingConfig(BaseModel):
    level: str = "INFO"
    output: str = "file"
    dir: str = "./logs"


class ModelEntry(BaseModel):
    names: list[str] = Field(min_length=1)
    openai_base_url: str | None = None
    anthropic_base_url: str | None = None
    api_key: str | None = None
    strip_path_prefix: str | None = None

    @model_validator(mode="after")
    def check_at_least_one_base_url(self):
        if not self.openai_base_url and not self.anthropic_base_url:
            raise ValueError("At least one of openai_base_url or anthropic_base_url must be set")
        return self


class Config(BaseModel):
    server: ServerConfig = ServerConfig()
    models: list[ModelEntry]
    logging: LoggingConfig = LoggingConfig()


def load_config(path: str) -> Config:
    with open(path, "r") as f:
        data = yaml.safe_load(f)
    return Config(**data)