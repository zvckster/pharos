FROM python:3.15-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 PHAROS_STATE_DIR=/data PHAROS_CONFIG=/config/pharos.json
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --uid 10001 --create-home pharos && mkdir /data && chown pharos:pharos /data
USER pharos
VOLUME ["/data"]
ENTRYPOINT ["pharos"]
CMD ["watch", "--notify"]
