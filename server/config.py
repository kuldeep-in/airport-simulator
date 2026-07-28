import base64
import os
import requests
from databricks.sdk import WorkspaceClient

IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))


def get_workspace_client():
    """Return a WorkspaceClient.

    In a Databricks App the service principal credentials are injected into the
    environment, so a bare WorkspaceClient() auto-configures. Locally we fall
    back to the named CLI profile.
    """
    if IS_DATABRICKS_APP:
        return WorkspaceClient()
    return WorkspaceClient(profile=os.environ.get("DATABRICKS_PROFILE", "fevm01"))


def get_oauth_token():
    """Return a bearer token string suitable for Lakebase / serving endpoints.

    We use config.authenticate() rather than config.token because the token
    attribute is None for OAuth / U2M auth flows.
    """
    client = get_workspace_client()
    auth = client.config.authenticate()
    return auth.get("Authorization", "").replace("Bearer ", "")


def get_workspace_host():
    """Return the workspace host with an https:// scheme."""
    if IS_DATABRICKS_APP:
        host = os.environ.get("DATABRICKS_HOST", "")
        if host and not host.startswith("http"):
            return f"https://{host}"
        return host
    return get_workspace_client().config.host


def get_secret_value(scope: str, key: str) -> str:
    """Fetch a Databricks Secret value via the REST API using current credentials."""
    try:
        host = get_workspace_host()
        w = get_workspace_client()
        auth = w.config.authenticate()
        resp = requests.get(
            f"{host}/api/2.0/secrets/get",
            headers=auth,
            params={"scope": scope, "key": key},
            timeout=10,
        )
        if resp.ok:
            val = base64.b64decode(resp.json().get("value", "")).decode("utf-8")
            print(f"[config] secret {scope}/{key} fetched (len={len(val)}).")
            return val
        print(f"[config] secret {scope}/{key} HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:  # noqa: BLE001
        print(f"[config] secret {scope}/{key} error: {e}")
    return ""


def get_lakebase_token(instance_name: str) -> str:
    """Get a Lakebase-specific JWT for Provisioned Tier database connections.

    Lakebase rejects plain workspace bearer tokens — a proper JWT must be minted
    via POST /api/2.0/database-credentials. On failure, logs the error and raises
    so the caller can fall to demo mode rather than silently use a bad token.
    """
    host = get_workspace_host()
    # Use M2M token from the service principal credentials
    import time as _time
    request_id = f"hia-sim-{int(_time.time())}"
    try:
        w = get_workspace_client()
        auth_header = w.config.authenticate()
        resp = requests.post(
            f"{host}/api/2.0/database/credentials",
            headers={**auth_header, "Content-Type": "application/json"},
            json={"request_id": request_id, "instance_names": [instance_name]},
            timeout=15,
        )
        print(f"[config] database-credentials status: {resp.status_code}")
        if resp.ok and resp.text:
            data = resp.json()
            token = data.get("token", "")
            if token:
                print(f"[config] Lakebase JWT obtained (len={len(token)}).")
                return token
            print(f"[config] No token in response: {list(data.keys())}")
        else:
            print(f"[config] database-credentials error: {resp.status_code} {resp.text[:300]}")
    except Exception as e:  # noqa: BLE001
        print(f"[config] database-credentials exception: {e}")
    # Raise so the pool builder catches it and logs properly rather than
    # connecting with a token Lakebase will reject.
    raise RuntimeError("Could not obtain Lakebase JWT — check service principal permissions.")
