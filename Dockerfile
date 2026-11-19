FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY bot ./bot

# Holds server settings, encrypted API keys, history and custom tools.
VOLUME ["/app/data"]

# The bot runs as root inside the container on purpose: the sandbox process for
# custom tools then switches to the unprivileged "nobody" user before running
# any untrusted code, so it can neither read the data folder nor signal the bot.
CMD ["python", "-m", "bot"]
