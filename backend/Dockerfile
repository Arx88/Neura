FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    ENV_MODE="production" \
    PYTHONPATH=/app \
    # Rust environment variables
    CARGO_HOME=/usr/local/cargo \
    RUSTUP_HOME=/usr/local/rustup \
    PATH=/usr/local/cargo/bin:$PATH

WORKDIR /app

# Install system dependencies
# Added: libffi-dev, libssl-dev, libjpeg-dev, libpng-dev, zlib1g-dev, libxml2-dev, libxslt1-dev, pkg-config
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    libffi-dev \
    libssl-dev \
    libjpeg-dev \
    libpng-dev \
    zlib1g-dev \
    libxml2-dev \
    libxslt1-dev \
    pkg-config \
    && rm -rf /var/lib/apt/lists/*

# Install Rust
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --default-toolchain stable --profile minimal --no-modify-path
ENV PATH="/root/.cargo/bin:${PATH}"


# Install Poetry & configure
RUN pip install poetry
RUN poetry --version
RUN poetry config virtualenvs.create false

# Copy Poetry project files
COPY pyproject.toml poetry.lock /app/

# Install dependencies using Poetry
RUN poetry cache clear . --all -n
RUN echo "BUILD_INFO --- Branch: $(git rev-parse --abbrev-ref HEAD) --- Commit: $(git rev-parse HEAD) --- Timestamp: $(date)" || echo "BUILD_INFO --- Git info not available (backend/Dockerfile) --- Timestamp: $(date)"
RUN poetry install --no-root --without dev --sync -vvv

# Run Dramatiq check using poetry run
RUN poetry run python -c "print('--- Docker Build Dramatiq Check ---'); import dramatiq; import dramatiq.middleware; print('Dramatiq version in Docker build:', dramatiq.__version__); print('Middleware path:', dramatiq.middleware.__file__); print('Middleware contents:', dir(dramatiq.middleware)); print('--- End Docker Build Dramatiq Check ---')"

# Copy application code the rest of the application code
COPY . .

# Create non-root user and set up directories and permissions
RUN useradd -m -u 1000 appuser && \
    mkdir -p /app/logs && \
    chown -R appuser:appuser /app

# Add appuser to docker group (from original, if needed for Docker socket access)
RUN groupadd -r docker || true && \
    usermod -aG docker appuser

# Switch to non-root user for runtime and subsequent commands
USER appuser

# Remove any potentially stale .pyc files or __pycache__ directories
RUN find /app -type f -name "*.pyc" -delete -o -type d -name "__pycache__" -exec rm -rf {} +

# Expose the port the app runs on
EXPOSE 8000

# CMD (ensure this is the active one from your original file)
CMD ["sh", "-c", "gunicorn api:app --workers ${WORKERS:-4} --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000"]
