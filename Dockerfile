# Reproducible local run: tests, synthetic data, validation. No AWS access needed.
#   docker build -t bankguard .
#   docker run --rm bankguard
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["sh", "-c", "pytest -q && python -m src.generate_data --out data && python -m src.transform data output"]
