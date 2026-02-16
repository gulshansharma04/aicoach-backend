# backend_deploy/Dockerfile
FROM python:3.11-slim

# Install ffmpeg (and ca-certificates for HTTPS), clean apt cache
RUN apt-get update \
  && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
  && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy dependency list & install
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy backend source
COPY . /app

# IMPORTANT: set OPENAI_API_KEY at deploy time (never bake it into the image)
ENV PORT=8000
EXPOSE 8000

# Use uvicorn to run the FastAPI app
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
