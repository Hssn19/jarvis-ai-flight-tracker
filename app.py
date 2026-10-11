import os

import db
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

load_dotenv()

OLLAMA_BASE = os.getenv("OLLAMA_BASE", "http://localhost:11434")
OLLAMA_URL = f"{OLLAMA_BASE}/api/generate"
MODEL = "llama3.2:1b"
IGNAV_URL = "https://ignav.com/api/fares/one-way"
IGNAV_KEY = os.getenv("IGNAV_API_KEY")
CACHE_TTL_MINUTES = 30

app = FastAPI(title="Jarvis API")
db.init_db()


class JarvisRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000)


@app.post("/jarvis")
async def jarvis(req: JarvisRequest):
    payload = {"model": MODEL, "prompt": req.prompt, "stream": False}
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(OLLAMA_URL, json=payload)
            r.raise_for_status()
    except httpx.ConnectError:
        raise HTTPException(503, "Cannot reach Ollama. Is the container running?")
    except httpx.TimeoutException:
        raise HTTPException(504, "Ollama timed out")
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Ollama error {e.response.status_code}")

    return {"model": MODEL, "response": r.json()["response"].strip()}


@app.get("/health")
async def health():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{OLLAMA_BASE}/api/tags")
            r.raise_for_status()
    except httpx.HTTPError:
        raise HTTPException(503, "Ollama is unreachable")
    return {"status": "ok", "model": MODEL}


@app.get("/flights")
async def flights(
    origin: str = Query(..., pattern=r"^[A-Za-z]{3}$", description="IATA code, e.g. MRU"),
    destination: str = Query(..., pattern=r"^[A-Za-z]{3}$", description="IATA code, e.g. CDG"),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$", description="YYYY-MM-DD"),
):
    origin, destination = origin.upper(), destination.upper()

    hit = db.get_cached(origin, destination, date, CACHE_TTL_MINUTES)
    if hit:
        return {**hit["payload"], "cached": True, "cached_at": hit["fetched_at"]}

    if not IGNAV_KEY:
        raise HTTPException(500, "IGNAV_API_KEY missing in .env")

    body = {"origin": origin, "destination": destination, "departure_date": date}
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                IGNAV_URL,
                headers={"X-Api-Key": IGNAV_KEY, "Content-Type": "application/json"},
                json=body,
            )
            r.raise_for_status()
    except httpx.TimeoutException:
        raise HTTPException(504, "Flight provider timed out")
    except httpx.ConnectError:
        raise HTTPException(503, "Cannot reach flight provider")
    except httpx.HTTPStatusError as e:
        code = e.response.status_code
        if code in (401, 403):
            raise HTTPException(502, "Flight provider rejected the API key")
        if code == 429:
            raise HTTPException(429, "Flight provider rate limit hit, retry later")
        raise HTTPException(502, f"Flight provider error {code}")

    data = r.json()
    results = []
    for it in data.get("itineraries", []):
        out = it.get("outbound", {})
        segs = out.get("segments", [])
        if not segs or "price" not in it:
            continue
        results.append(
            {
                "price": it["price"]["amount"],
                "currency": it["price"]["currency"],
                "carrier": out.get("carrier"),
                "stops": len(segs) - 1,
                "duration_minutes": out.get("duration_minutes"),
                "departure": segs[0]["departure_time_local"],
                "arrival": segs[-1]["arrival_time_local"],
                "route": [s["departure_airport"] for s in segs] + [segs[-1]["arrival_airport"]],
            }
        )

    results.sort(key=lambda x: x["price"])
    response = {
        "origin": origin,
        "destination": destination,
        "date": date,
        "observed_at": data.get("observed_at"),
        "count": len(results),
        "flights": results,
    }
    db.save_search(origin, destination, date, response, results[0]["price"] if results else None)
    return {**response, "cached": False}


def _hm(minutes):
    return f"{minutes // 60}h{minutes % 60:02d}" if minutes else "n/a"


def _line(f):
    stops = "direct" if f["stops"] == 0 else f"{f['stops']} stop(s)"
    return f"{f['carrier']}, {f['price']} {f['currency']}, {stops}, {_hm(f['duration_minutes'])}"


import re


def _numbers(text):
    return {float(n) for n in re.findall(r"\d+(?:\.\d+)?", text)}


@app.get("/advice")
async def advice(
    origin: str = Query(..., pattern=r"^[A-Za-z]{3}$"),
    destination: str = Query(..., pattern=r"^[A-Za-z]{3}$"),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    data = await flights(origin=origin, destination=destination, date=date)
    fl = data["flights"]
    if not fl:
        raise HTTPException(404, "No flights found for this search")

    cheapest = fl[0]
    fastest = min(fl, key=lambda f: f["duration_minutes"] or 10**9)
    direct = next((f for f in fl if f["stops"] == 0), None)

    pick = cheapest  # the DECISION is made in code, not by the AI
    facts = {
        "recommended": _line(pick),
        "cheapest": _line(cheapest),
        "fastest": _line(fastest),
        "cheapest_direct": _line(direct) if direct else None,
    }

    fallback = f"Best value: {_line(pick)}."
    if fastest is not pick:
        fallback += f" Fastest option: {_line(fastest)}."

    prompt = (
        f"Flight {data['origin']} to {data['destination']} on {date}.\n"
        f"The best-value pick is: {_line(pick)}.\n\n"
        "Write 2 short friendly sentences explaining why this is a good pick. "
        "Use only the facts above. Do not mention other flights."
    )

    text = None
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                OLLAMA_URL,
                json={
                    "model": MODEL,
                    "prompt": prompt,
                    "stream": False,
                    "keep_alive": "30m",
                    "options": {"num_predict": 80, "temperature": 0.3},
                },
            )
            r.raise_for_status()
        text = r.json()["response"].strip()
    except httpx.HTTPError:
        text = None

    # Guard: reject the AI's sentence if it contains numbers not in the pick
    if text and not _numbers(text) <= _numbers(_line(pick)):
        text = None

    return {
        "facts": facts,
        "advice": text or fallback,
        "advice_source": "llm" if text else "fallback",
        "cached": data["cached"],
    }
@app.get("/history")
async def history(
    origin: str = Query(..., pattern=r"^[A-Za-z]{3}$"),
    destination: str = Query(..., pattern=r"^[A-Za-z]{3}$"),
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    origin, destination = origin.upper(), destination.upper()
    points = db.get_history(origin, destination, date)
    if not points:
        raise HTTPException(404, "No history yet. Call /flights for this route first.")

    prices = [p["cheapest"] for p in points]
    latest = prices[-1]
    change = round(latest - prices[-2], 2) if len(prices) > 1 else None

    return {
        "origin": origin,
        "destination": destination,
        "date": date,
        "points": len(points),
        "latest": latest,
        "lowest": min(prices),
        "highest": max(prices),
        "change_vs_previous": change,
        "history": points,
    }