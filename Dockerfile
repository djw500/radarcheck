FROM python:3.11-slim

# Install system dependencies for scientific Python
RUN apt-get update && apt-get install -y --no-install-recommends \
    libeccodes0 \
    libgeos-dev \
    libproj-dev \
    proj-data \
    proj-bin \
    curl \
    supervisor \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (for Docker layer caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create directories for volume mount and logs
RUN mkdir -p /app/cache /app/logs

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Start supervisor (runs both web server and cache builder)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
