FROM python:3.12-slim

WORKDIR /app
RUN pip install --no-cache-dir "starlette>=0.37" "uvicorn>=0.29" "python-multipart>=0.0.9" \
    && useradd --system --uid 10001 mock
COPY mock_stoat ./mock_stoat
USER mock

ENV MOCK_STOAT_HOST=0.0.0.0 \
    MOCK_STOAT_PORT=8000
EXPOSE 8000
CMD ["python", "-m", "mock_stoat"]
