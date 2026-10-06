FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY agent ./agent
COPY service ./service
RUN pip install ".[service]" \
 && useradd --create-home --uid 10001 agent \
 && mkdir -p /data/workspace && chown -R agent:agent /data
USER agent
ENV AGENT_WORKSPACE=/data/workspace PORT=8000
EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --start-period=10s --retries=5 \
  CMD python -c "import urllib.request,os;urllib.request.urlopen('http://127.0.0.1:%s/healthz'%os.environ.get('PORT','8000'),timeout=2)"
CMD ["python", "-m", "service"]
