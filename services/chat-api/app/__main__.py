import uvicorn

from app.config import Settings
from app.logs import setup_logging
from app.main import create_app

settings = Settings.from_env()
setup_logging("chat-api", settings.log_level)
uvicorn.run(
    create_app(settings),
    host="0.0.0.0",
    port=settings.port,
    log_config=None,
    access_log=False,
    proxy_headers=True,
    forwarded_allow_ips="*",  # atrás do ALB
    timeout_graceful_shutdown=25,
)
