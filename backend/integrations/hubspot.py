# hubspot.py (snippets)
import os
import time
import json
import uuid
import requests
from urllib.parse import quote_plus
from typing import List, Dict, Any, Optional
from fastapi import Request
from fastapi.responses import RedirectResponse, JSONResponse

# --- config (read from env)
HUBSPOT_CLIENT_ID = os.environ.get("HUBSPOT_CLIENT_ID")
HUBSPOT_CLIENT_SECRET = os.environ.get("HUBSPOT_CLIENT_SECRET")
HUBSPOT_REDIRECT_URI = os.environ.get("HUBSPOT_REDIRECT_URI", "http://localhost:8000/integrations/hubspot/oauth2callback")

# default scopes used during install (space-separated)
HUBSPOT_SCOPES = "crm.objects.contacts.read crm.objects.companies.read crm.objects.deals.read"

# HubSpot endpoints
HUBSPOT_AUTHORIZE_URL = "https://app.hubspot.com/oauth/authorize"
HUBSPOT_TOKEN_URL = "https://api.hubapi.com/oauth/v1/token"
HUBSPOT_API_BASE = "https://api.hubapi.com"

# Redis key prefixes
REDIS_PREFIX = "integration:hubspot:"


# --- Helper to get a redis client from request, or create one (adapt to your app)
def _get_redis_client(request: Request):
    """
    If your app attaches a redis client to request.state.redis, use that.
    Otherwise this will create a local redis.StrictRedis client using REDIS_URL env var.
    """
    try:
        redis_client = request.state.redis  # expected in many FastAPI apps
        return redis_client
    except Exception:
        import redis
        REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        return redis.from_url(REDIS_URL)


# --- IntegrationItem fallback
# If your repo defines an IntegrationItem dataclass, import it instead of using this fallback.
try:
    from ..models import IntegrationItem  # adapt this import if your project differs
except Exception:
    from dataclasses import dataclass
    @dataclass
    class IntegrationItem:
        id: str
        title: str
        type: str
        parameters: Dict[str, Any]


# 1) authorize_hubspot(request) -> RedirectResponse
def authorize_hubspot(request: Request):
    """
    Build the HubSpot authorize URL, store a short-lived state token in redis, and redirect.
    Endpoint pattern expected in repo: GET /integrations/hubspot/authorize
    """
    if not HUBSPOT_CLIENT_ID:
        return JSONResponse({"error": "HUBSPOT_CLIENT_ID not configured"}, status_code=500)

    state = str(uuid.uuid4())
    redis_client = _get_redis_client(request)
    # store state for 5 minutes
    try:
        redis_client.set(f"{REDIS_PREFIX}state:{state}", "1", ex=300)
    except Exception:
        # if redis is not available, still proceed but you lose state protection
        pass

    params = {
        "client_id": HUBSPOT_CLIENT_ID,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "scope": HUBSPOT_SCOPES,
        "state": state,
    }
    # Build URL (safe encoding)
    q = "&".join(f"{k}={quote_plus(v)}" for k, v in params.items())
    url = f"{HUBSPOT_AUTHORIZE_URL}?{q}"
    return RedirectResponse(url)


# 2) oauth2callback_hubspot(request) -> RedirectResponse / JSON
def oauth2callback_hubspot(request: Request):
    """
    Called by HubSpot after user authorizes.
    Exchanges the code for access + refresh tokens and persists them in redis.
    You may choose to redirect to frontend with a short success page.
    """
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        return JSONResponse({"error": "hubspot_authorization_failed", "details": error}, status_code=400)

    if not code:
        return JSONResponse({"error": "missing_code"}, status_code=400)

    redis_client = _get_redis_client(request)
    # optional: verify state value in redis if present
    try:
        if state:
            st = redis_client.get(f"{REDIS_PREFIX}state:{state}")
            # allow missing as a non-fatal case
    except Exception:
        pass

    # Exchange code for tokens
    data = {
        "grant_type": "authorization_code",
        "client_id": HUBSPOT_CLIENT_ID,
        "client_secret": HUBSPOT_CLIENT_SECRET,
        "redirect_uri": HUBSPOT_REDIRECT_URI,
        "code": code,
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}
    resp = requests.post(HUBSPOT_TOKEN_URL, data=data, headers=headers)
    if resp.status_code != 200:
        return JSONResponse({"error": "token_exchange_failed", "details": resp.text}, status_code=500)
    token_payload = resp.json()
    # token_payload includes: access_token, refresh_token, expires_in, hub_domain, etc.
    access_token = token_payload.get("access_token")
    refresh_token = token_payload.get("refresh_token")
    expires_in = token_payload.get("expires_in", 0)
    hub_domain = token_payload.get("hub_domain", "unknown")

    # store tokens in redis keyed by hub_domain (or some installation id). Adapt to your storage model.
    key = f"{REDIS_PREFIX}tokens:{hub_domain}"
    saved = {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": int(time.time()) + int(expires_in or 0),
        "hub_domain": hub_domain,
        "scope": token_payload.get("scope"),
    }
    try:
        redis_client.set(key, json.dumps(saved))
    except Exception as e:
        # fallback: return the tokens (not secure) — prefer redis
        return JSONResponse({"warning": "redis_save_failed", "tokens": saved, "error": str(e)}, status_code=500)

    # Redirect back to frontend (optional) with success message
    frontend = os.environ.get("FRONTEND_BASE_URL", "http://localhost:3000")
    return RedirectResponse(f"{frontend}/?integrations=hubspot_connected")


# 3) get_hubspot_credentials(request, hub_domain) -> dict with access_token (refreshes if needed)
def get_hubspot_credentials(request: Request, hub_domain: Optional[str] = None) -> Dict[str, Any]:
    """
    Load saved tokens from redis for hub_domain (or the first saved installation if hub_domain is None).
    If access_token is expired (or near expiry), automatically refresh it using HubSpot's token endpoint.
    Returns a dict with at least access_token and hub_domain.
    """
    redis_client = _get_redis_client(request)

    # If hub_domain not provided, pick the first saved key
    if not hub_domain:
        # naive: scan keys
        try:
            keys = redis_client.keys(f"{REDIS_PREFIX}tokens:*")
            if not keys:
                raise Exception("no hubspot tokens found")
            key = keys[0] if isinstance(keys[0], bytes) else keys[0]
            hub_domain = key.decode().split(":")[-1] if isinstance(key, bytes) else key.split(":")[-1]
        except Exception as e:
            raise RuntimeError("No stored HubSpot credentials: " + str(e))

    key = f"{REDIS_PREFIX}tokens:{hub_domain}"
    raw = redis_client.get(key)
    if not raw:
        raise RuntimeError("No tokens for hub_domain: " + str(hub_domain))
    tokens = json.loads(raw if isinstance(raw, str) else raw.decode())

    # If token expired or will within 60 seconds, refresh
    if tokens.get("expires_at", 0) - time.time() < 60:
        # refresh
        data = {
            "grant_type": "refresh_token",
            "client_id": HUBSPOT_CLIENT_ID,
            "client_secret": HUBSPOT_CLIENT_SECRET,
            "refresh_token": tokens.get("refresh_token"),
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        r = requests.post(HUBSPOT_TOKEN_URL, data=data, headers=headers)
        if r.status_code != 200:
            raise RuntimeError("Failed refreshing token: " + r.text)
        new = r.json()
        tokens["access_token"] = new.get("access_token")
        tokens["refresh_token"] = new.get("refresh_token", tokens.get("refresh_token"))
        tokens["expires_at"] = int(time.time()) + int(new.get("expires_in", 0))
        tokens["scope"] = new.get("scope", tokens.get("scope"))
        # persist updated tokens
        redis_client.set(key, json.dumps(tokens))
    return tokens


# 4) get_items_hubspot(request, hub_domain) -> List[IntegrationItem]
def get_items_hubspot(request: Request, hub_domain: Optional[str] = None) -> List[IntegrationItem]:
    """
    Query HubSpot CRM endpoints (contacts, companies, deals) and return a list of IntegrationItem objects.
    This example returns up to 50 contacts/companies/deals each and maps basic properties.
    """
    tokens = get_hubspot_credentials(request, hub_domain)
    access_token = tokens["access_token"]
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}

    items: List[IntegrationItem] = []

    # Helper to call object list endpoint
    def fetch_objects(obj: str, properties: str = "") -> List[Dict[str, Any]]:
        url = f"{HUBSPOT_API_BASE}/crm/v3/objects/{obj}"
        params = {"limit": 50}
        if properties:
            params["properties"] = properties
        r = requests.get(url, headers=headers, params=params)
        r.raise_for_status()
        j = r.json()
        return j.get("results", [])

    # Contacts
    try:
        contacts = fetch_objects("contacts", "email,firstname,lastname,phone")
        for c in contacts:
            props = c.get("properties", {})
            title = props.get("email") or f"{props.get('firstname','')} {props.get('lastname','')}".strip()
            item = IntegrationItem(
                id=f"contact:{c.get('id')}",
                title=title,
                type="contact",
                parameters={
                    "hubspot_id": c.get("id"),
                    "email": props.get("email"),
                    "firstname": props.get("firstname"),
                    "lastname": props.get("lastname"),
                    "phone": props.get("phone"),
                },
            )
            items.append(item)
    except Exception as e:
        # non-fatal; continue with companies/deals
        print("Error fetching contacts:", e)

    # Companies
    try:
        companies = fetch_objects("companies", "name,domain,phone")
        for comp in companies:
            props = comp.get("properties", {})
            item = IntegrationItem(
                id=f"company:{comp.get('id')}",
                title=props.get("name") or props.get("domain") or f"Company {comp.get('id')}",
                type="company",
                parameters={
                    "hubspot_id": comp.get("id"),
                    "name": props.get("name"),
                    "domain": props.get("domain"),
                    "phone": props.get("phone"),
                },
            )
            items.append(item)
    except Exception as e:
        print("Error fetching companies:", e)

    # Deals
    try:
        deals = fetch_objects("deals", "dealname,amount,dealstage,closedate")
        for d in deals:
            props = d.get("properties", {})
            item = IntegrationItem(
                id=f"deal:{d.get('id')}",
                title=props.get("dealname") or f"Deal {d.get('id')}",
                type="deal",
                parameters={
                    "hubspot_id": d.get("id"),
                    "dealname": props.get("dealname"),
                    "amount": props.get("amount"),
                    "dealstage": props.get("dealstage"),
                },
            )
            items.append(item)
    except Exception as e:
        print("Error fetching deals:", e)

    return items
