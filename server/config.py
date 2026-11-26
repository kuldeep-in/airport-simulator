import base64
import os
import requests
from databricks.sdk import WorkspaceClient

IS_DATABRICKS_APP = bool(os.environ.get("DATABRICKS_APP_NAME"))

# True inside a Databricks JOB (the position generator — see generator.py), which is a
# different runtime from a Databricks App: no DATABRICKS_APP_NAME, but the ambient
# credentials a bare WorkspaceClient() needs are still present.
IS_DATABRICKS_JOB = bool(
    os.environ.get("DB_IS_DRIVER")
    or os.environ.get("DATABRICKS_RUNTIME_VERSION")
    or os.environ.get("SPARK_HOME")
)

# Either managed runtime: credentials are ambient, and Secrets must be fetched via the
# API rather than read from env vars. Gating secret access on IS_DATABRICKS_APP alone
# silently pushed the generator job into demo mode (no PGPASSWORD → no Lakebase → it
# had nowhere to publish), which is exactly the failure this distinction prevents.
IS_MANAGED_RUNTIME = IS_DATABRICKS_APP or IS_DATABRICKS_JOB


def get_workspace_client():
    """Return a WorkspaceClient.

    In a Databricks App the service principal credentials are injected into the
    environment, so a bare WorkspaceClient() auto-configures. Locally we fall
    back to the named CLI profile.
    """
    if IS_MANAGED_RUNTIME:
        # App OR job: credentials are injected into the environment.
        return WorkspaceClient()
    return WorkspaceClient(profile=os.environ.get("DATABRICKS_PROFILE", "fevm01"))


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

