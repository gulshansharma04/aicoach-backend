# Customer Management AI Agent
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

# Persist the SQLite DB outside the image in production by mounting a volume here.
ENV CRM_DB_PATH=/app/data/crm.db
# Optional: set OPENAI_API_KEY at deploy time to enable AI features.
ENV PORT=8000
EXPOSE 8000

# Contents of customer_agent/ live in /app, so the package is "app".
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
