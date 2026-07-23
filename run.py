"""Dev entrypoint: uvicorn app.main:app --host 0.0.0.0 --port <PORT>."""
import uvicorn

from app.core.config import settings
from app.core.logging_config import setup_logging

if __name__ == "__main__":
    setup_logging()
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.port, reload=False)
