# Jarvis AI Flight Tracker

## Stack
Python 3.12, FastAPI, httpx, SQLite, Ollama (llama3.2:1b), Ignav API (Amadeus Self-Service is shut down), Docker Compose.

## Done (Phases 1-5)
- /jarvis: raw chat with Ollama
- /health: checks Ollama is reachable
- /flights: Ignav fares, SQLite cache (30 min TTL), sorted cheapest first
- /advice: code decides the pick, LLM only phrases it, number-guard rejects invented numbers (fallback text)
- Docker Compose: services api + ollama; volumes ollama (external) and jarvis-data
- Run with: docker compose up -d --build (rebuild after any code change)

## Files
app.py, db.py, Dockerfile, docker-compose.yml, requirements.txt, .env (IGNAV_API_KEY, never commit)

## Next
1. /history endpoint (cheapest price over time from SQLite)
2. Price-drop alerts
3. README + architecture diagram + GitHub

## About the user
Senior web dev, new to Python/Docker. Wants one step at a time, a short kid-style purpose line per step, no huge blocks, full files instead of patches (indentation errors), no secrets pasted in chat.