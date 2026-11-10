FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY docintel docintel
COPY samples samples
RUN pip install --no-cache-dir ".[local]"
RUN useradd --system --uid 10001 docintel && mkdir -p /app/data/uploads /app/data/corrections /app/data/longdocs /app/models && chown -R docintel /app/data /app/models
USER docintel
ENV PYTHONUNBUFFERED=1
HEALTHCHECK --interval=15s --timeout=3s --retries=5 CMD curl -fsS http://localhost:8080/health || exit 1
CMD ["uvicorn", "docintel.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
