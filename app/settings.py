import os


def get_database_url() -> str:
    return os.getenv(
        "DATABASE_URL",
        "postgresql://miles:milespass@localhost:5432/milesmemories",
    )


def get_app_port() -> int:
    return int(os.getenv("APP_PORT", "8000"))
