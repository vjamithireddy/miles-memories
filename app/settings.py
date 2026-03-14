import os


def get_database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://miles:milespass@localhost:5432/milesmemories",
    )


def get_app_port() -> int:
    return int(os.getenv("APP_PORT", "8000"))


def get_app_host() -> str:
    return os.getenv("APP_HOST", "0.0.0.0")


def get_app_reload() -> bool:
    return os.getenv("APP_RELOAD", "false").strip().lower() in {"1", "true", "yes", "on"}
