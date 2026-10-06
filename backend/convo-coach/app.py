"""Convo Coach, Step 0 stub: proves both delivery paths buzz the watch before any
coaching code exists. (1) webhook reply {"message": ...}; (2) app-initiated push via
POST /v1/integrations/notification. Fires once per process start; restart the
container to run it again."""

import asyncio
import logging
import os

import httpx
from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("convo-coach")

OMI_API_URL = os.getenv("OMI_API_URL", "http://backend:8080")

app = FastAPI()
fired = False


async def push(uid, text):
    async with httpx.AsyncClient(timeout=5) as client:
        r = await client.post(
            f"{OMI_API_URL}/v1/integrations/notification",
            headers={"Authorization": f"Bearer {os.environ['OMI_APP_API_KEY']}"},
            json={"aid": os.environ["OMI_APP_ID"], "uid": uid, "message": text},
        )
    # The error body is the backend's detail string (bad key, not installed, 429), no transcript
    log.info("push status=%d%s", r.status_code, "" if r.is_success else f" detail={r.text[:200]}")
    return r.is_success


async def silence_push(uid):
    await asyncio.sleep(8)
    try:
        await push(uid, "coach silence test")
    except Exception as e:
        log.warning("push error: %s", type(e).__name__)


@app.post("/webhook")
async def webhook(request: Request):
    global fired
    try:
        uid = (await request.json()).get("session_id")
    except Exception:
        return {}
    if fired or not uid:
        return {}
    fired = True
    log.info("step0 webhook fired")
    app.state.pending = asyncio.create_task(silence_push(uid))  # keep a ref so it isn't GC'd
    return {"message": "coach test"}
