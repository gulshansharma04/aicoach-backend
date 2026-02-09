# Deploy the FastAPI backend

You can deploy this backend anywhere that can run a Docker container (Render, Fly.io, Azure App Service, AWS ECS, GCP Cloud Run, etc.).

## Required env vars
- OPENAI_API_KEY=...
- OPENAI_CHAT_MODEL=gpt-4o-mini  (optional)

## Local test
```bash
docker build -t aicoach-api .
docker run -p 8000:8000 -e OPENAI_API_KEY=... aicoach-api
```
Then open http://localhost:8000/health
