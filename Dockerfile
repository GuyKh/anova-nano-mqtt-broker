FROM python:3.12-slim

WORKDIR /app

# Install system dependencies for BLE
# BlueZ and pygatt require certain system libraries
RUN apt-get update && apt-get install -y \
    bluez \
    bluetooth \
    libbluetooth-dev \
    python3-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY anova_mqtt_service.py .

# Create a non-root user for security
RUN useradd -m appuser
USER appuser

# Default command
CMD ["python", "anova_mqtt_service.py"]