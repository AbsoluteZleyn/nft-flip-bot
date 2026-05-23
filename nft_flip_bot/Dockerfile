FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install runtime deps first (cache-friendly)
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project
COPY . /app

# BOT_TOKEN must be provided at runtime
ENV BOT_TOKEN=""

# Persist SQLite DB outside of the image layer if mounted as volume
VOLUME ["/app/data"]

CMD ["python", "-m", "nft_flip_bot.main"]
