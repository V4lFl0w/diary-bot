import asyncio
import os

if os.getenv("APP_ROLE") != "worker":
    raise RuntimeError("This entrypoint is worker-only. Set APP_ROLE=worker")

from app.main import main

if __name__ == "__main__":
    asyncio.run(main())
