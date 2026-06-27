# Image tunggal dipakai dua service (bot + dashboard) di docker-compose.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# default: forward-test paper (di-override per service di compose)
CMD ["python", "forwardtest.py", "--poll", "30"]
