from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    gateway_api_key: str
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-west-2"
    host: str = "0.0.0.0"
    port: int = 8789

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
