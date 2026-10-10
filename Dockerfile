FROM python:3.12-alpine

WORKDIR /app

# Install system dependencies for BLE
# Alpine packages for bluez and development headers
RUN apk add --no-cache \
    bluez \
    bluez-dev \
    gcc \
    musl-dev \
    python3-dev \
    && rm -rf /var/cache/apk/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY anova_mqtt_service.py .

# Create a non-root user for security
RUN addgroup -g 1000 appgroup && \
    adduser -D -u 1000 -G appgroup appuser
USER appuser

# Default command
CMD ["python", "anova_mqtt_service.py"]