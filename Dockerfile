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

# Memory optimization for 1GB container:
# - MALLOC_ARENA_MAX: limit glibc malloc arenas (default = 8*cores, wastes RSS)
# - OMP/OPENBLAS threads: numpy/scipy default to all cores, 1 is enough
# - PYTHONDONTWRITEBYTECODE: skip .pyc writes (tiny savings)
ENV MALLOC_ARENA_MAX=2 \
    OMP_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Copy supervisor config
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

# Start supervisor (runs both web server and cache builder)
CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]
