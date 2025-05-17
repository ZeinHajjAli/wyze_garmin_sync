FROM python:3.11-slim

RUN apt-get update && apt-get install -y cron

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

COPY pyproject.toml .
COPY uv.lock .
RUN uv sync --locked

COPY ./scale.py .
COPY ./fit.py .

# Run immediately at startup
RUN (uv run scale.py) &

RUN echo "* * * * * uv run scale.py" > /etc/cron.d/scale_cron
RUN chmod 0644 /etc/cron.d/scale_cron
RUN crontab /etc/cron.d/scale_cron

# Start the cron service in the background
CMD ["cron", "-f"]
