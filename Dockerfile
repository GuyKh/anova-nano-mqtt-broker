FROM python:3.12-slim

WORKDIR /app

# bleak talks to the host's BlueZ over D-Bus (socket mounted by docker compose);
# no Bluetooth packages are needed inside the image.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY anova_mqtt_service.py .

ENV PYTHONUNBUFFERED=1

# Runs as root: BlueZ's D-Bus policy only lets root (or the host's "bluetooth"
# group) talk to org.bluez. See README "Bluetooth access" for running unprivileged.
CMD ["python", "anova_mqtt_service.py"]
