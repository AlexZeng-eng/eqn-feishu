FROM python:3.12-slim

WORKDIR /app

COPY keywords.json /app/keywords.json
COPY worker_loop.py /app/worker_loop.py

# 只用标准库，不需要 pip install
ENV PYTHONUNBUFFERED=1 \
    POLL_INTERVAL=60 \
    STATE_FILE=/app/.state.json

CMD ["python3", "/app/worker_loop.py"]
