"""Load optional repository-local credentials without changing exported values."""
from pathlib import Path

PROJECT_ENV = Path(__file__).resolve().parents[1] / ".env"


def load_local_env() -> None:
    """Only the project's ignored .env is read; never search parent directories.

    Exported variables win, and interpolation is disabled so values are literal.
    This is invoked at authentication time, never by offline config validation.
    """
    if not PROJECT_ENV.is_file():
        return
    try:
        from dotenv import load_dotenv
    except ImportError as exc:
        raise RuntimeError("Install requirements-c.txt to load the project's .env file") from exc
    load_dotenv(dotenv_path=PROJECT_ENV, override=False, interpolate=False)
