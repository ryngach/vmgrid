# Dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system dependencies and Docker CLI
RUN apt-get update && apt-get install -y \
    gcc \
    apt-transport-https \
    ca-certificates \
    curl \
    gnupg \
    lsb-release \
    && curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg \
    && echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/debian \
    $(lsb_release -cs) stable" | tee /etc/apt/sources.list.d/docker.list > /dev/null \
    && apt-get update \
    && apt-get install -y docker-ce-cli \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first to leverage Docker cache
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create non-root user and docker group
RUN groupadd -r docker && \
    useradd -r -g docker -u 1000 appuser && \
    # Create vmgrid directory with correct permissions
    mkdir -p /var/lib/vmgrid && \
    chown -R appuser:docker /var/lib/vmgrid && \
    chmod 775 /var/lib/vmgrid

USER appuser

ENV PYTHONPATH=/app

CMD ["python", "app.py"]