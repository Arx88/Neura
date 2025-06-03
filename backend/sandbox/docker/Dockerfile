FROM python:3.11-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    netcat-traditional \
    gnupg \
    curl \
    unzip \
    zip \
    xvfb \
    libgconf-2-4 \
    libxss1 \
    libnss3 \
    libnspr4 \
    libasound2 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdbus-1-3 \
    libdrm2 \
    libgbm1 \
    libgtk-3-0 \
    libxcomposite1 \
    libxdamage1 \
    libxfixes3 \
    libxrandr2 \
    xdg-utils \
    fonts-liberation \
    dbus \
    xauth \
    xvfb \
    x11vnc \
    tigervnc-tools \
    supervisor \
    net-tools \
    procps \
    git \
    python3-numpy \
    fontconfig \
    fonts-dejavu \
    fonts-dejavu-core \
    fonts-dejavu-extra \
    tmux \
    # PDF Processing Tools
    poppler-utils \
    wkhtmltopdf \
    # Document Processing Tools
    antiword \
    unrtf \
    catdoc \
    # Text Processing Tools
    grep \
    gawk \
    sed \
    # File Analysis Tools
    file \
    # Data Processing Tools
    jq \
    csvkit \
    xmlstarlet \
    # Additional Utilities
    less \
    vim \
    tree \
    rsync \
    lsof \
    iputils-ping \
    dnsutils \
    sudo \
    # OCR Tools
    tesseract-ocr \
    tesseract-ocr-eng \
    findutils \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js and npm
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g npm@latest

# Install Cloudflare Wrangler CLI globally
RUN npm install -g wrangler

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"
RUN poetry --version
RUN poetry config virtualenvs.create false

# Set platform for ARM64 compatibility
ARG TARGETPLATFORM=linux/amd64

# Set up working directory
WORKDIR /app

# Copy Poetry project files
COPY pyproject.toml poetry.lock* /app/

# Install Python dependencies using Poetry
RUN poetry cache clear . --all -n
RUN echo "BUILD_INFO --- Branch: $(git rev-parse --abbrev-ref HEAD) --- Commit: $(git rev-parse HEAD) --- Timestamp: $(date)" || echo "BUILD_INFO --- Git info not available --- Timestamp: $(date)"
RUN poetry install --no-root --without dev --sync -vvv

# Verification step for Dramatiq
RUN poetry run python -c "print('--- Docker Build Dramatiq Check (Sandbox) ---'); import dramatiq; import dramatiq.middleware; print('Dramatiq version in Docker build (Sandbox):', dramatiq.__version__); print('Middleware path (Sandbox):', dramatiq.middleware.__file__); print('Middleware contents (Sandbox):', dir(dramatiq.middleware)); print('--- End Docker Build Dramatiq Check (Sandbox) ---')"

# Copy the rest of the application code (includes server.py, browser_api.py etc.)
COPY . /app

# Install Playwright browsers and OS dependencies (Playwright Python package itself is installed by Poetry)
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install-deps
RUN playwright install chromium
# Verify Playwright installation
RUN python -c "from playwright.sync_api import sync_playwright; print('Playwright installation verified')"

# Install noVNC
RUN git clone https://github.com/novnc/noVNC.git /opt/novnc \
    && git clone https://github.com/novnc/websockify /opt/novnc/utils/websockify \
    && ln -s /opt/novnc/vnc.html /opt/novnc/index.html

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV CHROME_PATH=/ms-playwright/chromium-*/chrome-linux/chrome
ENV ANONYMIZED_TELEMETRY=false
ENV DISPLAY=:99
ENV RESOLUTION=1024x768x24
ENV VNC_PASSWORD=vncpassword
ENV CHROME_PERSISTENT_SESSION=true
ENV RESOLUTION_WIDTH=1024
ENV RESOLUTION_HEIGHT=768
# Add Chrome flags to prevent multiple tabs/windows
ENV CHROME_FLAGS="--single-process --no-first-run --no-default-browser-check --disable-background-networking --disable-background-timer-throttling --disable-backgrounding-occluded-windows --disable-breakpad --disable-component-extensions-with-background-pages --disable-dev-shm-usage --disable-extensions --disable-features=TranslateUI --disable-ipc-flooding-protection --disable-renderer-backgrounding --enable-features=NetworkServiceInProcess2 --force-color-profile=srgb --metrics-recording-only --mute-audio --no-sandbox --disable-gpu"

# Set up supervisor configuration
RUN mkdir -p /var/log/supervisor
COPY supervisord.conf /etc/supervisor/conf.d/supervisord.conf

EXPOSE 7788 6080 5901 8000 8080

CMD ["/usr/bin/supervisord", "-c", "/etc/supervisor/conf.d/supervisord.conf"]