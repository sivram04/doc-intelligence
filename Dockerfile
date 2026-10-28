FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml .
COPY docintel docintel
RUN pip install --no-cache-dir ".[ocr]"
COPY scripts scripts
CMD ["uvicorn", "docintel.api.main:app", "--host", "0.0.0.0", "--port", "8080"]
