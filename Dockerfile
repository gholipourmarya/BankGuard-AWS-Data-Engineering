# Reproducible local run: tests, synthetic data, validation. No AWS access needed.
#   docker build -t bankguard .
#   docker run --rm bankguard
# Python 3.12.16; update the digest deliberately together with dependency checks.
FROM python:3.12-slim@sha256:b18b261e1b4974b4f321451ad5251ded85abb22c2b8b262da527dda1d031ab30

WORKDIR /app
COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
CMD ["sh", "-c", "pytest -q && python -m src.generate_data --out data && python -m src.transform data output"]
