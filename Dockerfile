FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot
COPY plugins ./plugins

# data/ holds settings and history, plugins/ holds tools the assistant writes.
VOLUME ["/app/data", "/app/plugins"]

CMD ["python", "-m", "bot"]
