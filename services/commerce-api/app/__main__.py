import uvicorn

from app.config import Settings
from app.logs import setup_logging
from app.main import create_app

settings = Settings.from_env()
setup_logging("commerce-api", settings.log_level)
uvicorn.run(
    create_app(settings),
    host="0.0.0.0",
    port=settings.port,
    log_config=None,
    access_log=False,
    timeout_graceful_shutdown=20,
    # Maior que o idle timeout do ALB (60 s): com o padrão de 5 s, o ALB reaproveitava conexões que a task já tinha
    # fechado e devolvia 502 esporádicos (docs/resultados/ensaio-sp.md).
    timeout_keep_alive=65,
)
