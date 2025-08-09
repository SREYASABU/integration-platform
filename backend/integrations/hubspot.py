import os
import time
import json
import urllib.parse
import httpx
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse
import redis
from dotenv import load_dotenv
load_dotenv()

# HubSpot OAuth and API endpoints
HUBSPOT_OAUTH_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_OAUTH_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API_BASE = "https://api.hubapi.com"

# Config from environment
HUBSPOT_CLIENT_ID = os.getenv("HUBSPOT_CLIENT_ID")
HUBSPOT_CLIENT_SECRET = os.getenv("HUBSPOT_CLIENT_SECRET")
HUBSPOT_REDIRECT_URI = os.getenv("HUBSPOT_REDIRECT_URI", "http://localhost:8000/integrations/hubspot/oauth2callback")

HUBSPOT_SCOPES = [
    "crm.objects.companies.read",
    "crm.objects.contacts.read",
    "crm.objects.deals.read",
    "oauth"
]

# Redis client
redis_url = os.getenv("REDIS_URL")
if redis_url:
    _redis = redis.from_url(redis_url, decode_responses=True)
else:
    _redis = redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 0)),
        decode_responses=True
    )

def _redis_key(user_id, org_id):
    return f"hubspot:tokens:{user_id}:{org_id}"

async def authorize_hubspot(user_id, org_id):
    state = urllib.parse.quote(f"{user_id}:{org_id}", safe="")
    scope_str = " ".join(HUBSPOT_SCOPES)
    params = {
        "client_id": HUBSPOT_CLIENT_ID,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "scope": scope_str,
        "state": state,
    }
    query = urllib.parse.urlencode(params)
    return f"{HUBSPOT_OAUTH_AUTHORIZE_URL}?{query}"

async def oauth2callback_hubspot(request: Request):
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        raise HTTPException(status_code=400, detail=f"HubSpot OAuth error: {error}")
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing code or state")

    user_id, org_id = urllib.parse.unquote(state).split(":", 1)

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            HUBSPOT_OAUTH_TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": HUBSPOT_CLIENT_ID,
                "client_secret": HUBSPOT_CLIENT_SECRET,
                "redirect_uri": HUBSPOT_REDIRECT_URI,
                "code": code,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Token exchange failed: {resp.text}")
    token_data = resp.json()

    key = _redis_key(user_id, org_id)
    rec = {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token"),
        "expires_in": token_data.get("expires_in"),
        "obtained_at": int(time.time()),
        "raw": json.dumps(token_data),
    }
    _redis.hmset(key, rec)
    if token_data.get("expires_in"):
        _redis.expire(key, int(token_data["expires_in"]) + 300)

    # Return HTML that closes the window
    close_window_script = """
    <html>
        <script>
            window.close();
        </script>
        <body>
            <p>Authentication successful! You can close this window if it doesn't close automatically.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=close_window_script)

async def get_hubspot_credentials(user_id, org_id):
    key = _redis_key(user_id, org_id)
    if not _redis.exists(key):
        return None
    rec = _redis.hgetall(key)
    access = rec.get("access_token")
    expires_in = rec.get("expires_in")
    obtained = rec.get("obtained_at")
    if access and expires_in and obtained:
        try:
            if time.time() > (int(obtained) + int(expires_in) - 60):
                rec = await _refresh_token(user_id, org_id, rec.get("refresh_token"))
        except Exception:
            rec = await _refresh_token(user_id, org_id, rec.get("refresh_token"))
    return rec

async def _refresh_token(user_id, org_id, refresh_token):
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            HUBSPOT_OAUTH_TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": HUBSPOT_CLIENT_ID,
                "client_secret": HUBSPOT_CLIENT_SECRET,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"}
        )
    if resp.status_code != 200:
        raise HTTPException(status_code=400, detail=f"Refresh token failed: {resp.text}")
    token_data = resp.json()

    key = _redis_key(user_id, org_id)
    rec = {
        "access_token": token_data.get("access_token"),
        "refresh_token": token_data.get("refresh_token", refresh_token),
        "expires_in": token_data.get("expires_in"),
        "obtained_at": int(time.time()),
        "raw": json.dumps(token_data),
    }
    _redis.hmset(key, rec)
    if token_data.get("expires_in"):
        _redis.expire(key, int(token_data["expires_in"]) + 300)
    return rec

async def create_integration_item_metadata_object(response_json):
    obj_id = response_json.get("id")
    props = response_json.get("properties", {})
    # infer type
    if "dealname" in props:
        obj_type = "deal"
        title = props.get("dealname") or f"Deal {obj_id}"
        params = {"amount": props.get("amount"), "dealstage": props.get("dealstage")}
    elif "firstname" in props or "lastname" in props:
        obj_type = "contact"
        title = " ".join(p for p in [props.get("firstname"), props.get("lastname")] if p) or props.get("email") or f"Contact {obj_id}"
        params = {
            "email": props.get("email"),
            "first_name": props.get("firstname"),
            "last_name": props.get("lastname"),
            "company": props.get("company"),
        }
    elif "name" in props or "domain" in props:
        obj_type = "company"
        title = props.get("name") or props.get("domain") or f"Company {obj_id}"
        params = {"name": props.get("name"), "domain": props.get("domain")}
    else:
        obj_type = "unknown"
        title = props.get("name") or f"Item {obj_id}"
        params = props
    return {"id": obj_id, "title": title, "type": obj_type, "parameters": params}

async def get_items_hubspot(credentials):
    access = credentials.get("access_token")
    if not access:
        raise HTTPException(status_code=400, detail="Missing access_token")

    headers = {"Authorization": f"Bearer {access}", "Content-Type": "application/json"}
    items = []

    async with httpx.AsyncClient() as client:
        for obj_type, props in [
            ("contacts", "firstname,lastname,email,company"),
            ("companies", "name,domain"),
            ("deals", "dealname,amount,dealstage"),
        ]:
            url = f"{HUBSPOT_API_BASE}/crm/v3/objects/{obj_type}"
            resp = await client.get(url, headers=headers, params={"limit": 50, "properties": props})
            if resp.status_code != 200:
                # skip on failure
                continue
            data = resp.json()
            for obj in data.get("results", []):
                meta = await create_integration_item_metadata_object(obj)
                items.append(meta)

    return items