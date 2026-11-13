# syntax=docker/dockerfile:1
# Imagem do job de migração: cria usuários por serviço, aplica Alembic e roda o seed.
# No ECS, rode como tarefa avulsa (run-task) antes de atualizar os serviços.
FROM public.ecr.aws/docker/library/python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN python -m venv /opt/venv
ENV PATH=/opt/venv/bin:$PATH
WORKDIR /srv/db
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
USER 10001:10001
CMD ["python", "bootstrap.py"]
