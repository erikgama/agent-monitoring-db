FROM python:3.13-slim
WORKDIR /repository/apps/lab-console/api
COPY pyproject.toml uv.lock /repository/
COPY agent_monitoring /repository/agent_monitoring
COPY config/agent-monitoring.example.toml /repository/config/
COPY apps/lab-console/api/pyproject.toml apps/lab-console/api/uv.lock ./
COPY apps/lab-console/api/labconsole ./labconsole
RUN pip install --no-cache-dir uv && uv sync --locked --no-dev
RUN useradd --uid 10001 --create-home console && mkdir runtime && chown console:console runtime
USER console
EXPOSE 8000
CMD [".venv/bin/uvicorn", "labconsole.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
