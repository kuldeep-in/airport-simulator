"""Genie (AI/BI) integration — the app's "Ask Genie" tab.

The browser never talks to Genie directly: it calls /api/genie/*, and this module calls
the Genie Conversation API as the APP'S SERVICE PRINCIPAL (credentials injected into the
app). The space queries Delta through its SQL warehouse, so the app needs no warehouse
or table access of its own beyond what the space requires. Permissions come from the
app's resources in resources/app.yml: genie_space CAN_RUN + sql_warehouse CAN_USE, plus
UC SELECT on the space's tables.

    UI ──POST /api/genie/ask──▶ start-conversation | create-message   (returns ids at once)
    UI ──GET  /api/genie/message (poll ~1.5 s)──▶ message status → text, SQL, result rows

Calls are plain REST through the SDK's api_client (works across SDK versions) and are
run off the event loop, so a slow Genie answer never blocks the WebSocket.
"""
import asyncio
import os

from server import config

SPACE_ID = os.environ.get("GENIE_SPACE_ID", "")
_MAX_ROWS = 200          # rows returned to the browser per answer
_ws = None


def _client():
    global _ws
    if _ws is None:
        _ws = config.get_workspace_client()
    return _ws


def _api(method: str, path: str, body: dict | None = None) -> dict:
    return _client().api_client.do(method, f"/api/2.0/genie/spaces/{SPACE_ID}{path}", body=body) or {}


def space_info() -> dict:
    if not SPACE_ID:
        return {"configured": False}
    d = _client().api_client.do("GET", f"/api/2.0/genie/spaces/{SPACE_ID}") or {}
    return {"configured": True, "space_id": SPACE_ID, "title": d.get("title"),
            "description": d.get("description"), "warehouse_id": d.get("warehouse_id")}


def ask(question: str, conversation_id: str | None) -> dict:
    """Start a conversation, or add a follow-up to one. Returns immediately with ids;
    the answer is fetched by polling `message()`."""
    if conversation_id:
        r = _api("POST", f"/conversations/{conversation_id}/messages", {"content": question})
        return {"conversation_id": conversation_id, "message_id": r.get("message_id") or r.get("id")}
    r = _api("POST", "/start-conversation", {"content": question})
    return {"conversation_id": r.get("conversation_id") or (r.get("conversation") or {}).get("id"),
            "message_id": r.get("message_id") or (r.get("message") or {}).get("id")}


def message(conversation_id: str, message_id: str) -> dict:
    """Current state of one Genie answer: status, text, generated SQL and — once the
    query finished — its result rows (first _MAX_ROWS)."""
    m = _api("GET", f"/conversations/{conversation_id}/messages/{message_id}")
    status = m.get("status")
    out = {"status": status, "text": None, "sql": None, "sql_description": None,
           "columns": [], "rows": [], "row_count": None, "truncated": False,
           "suggested_questions": [], "error": (m.get("error") or {}).get("error") if m.get("error") else None}
    query_att = None
    for att in m.get("attachments") or []:
        if att.get("text"):
            out["text"] = (att["text"] or {}).get("content")
        if att.get("query"):
            q = att["query"]
            out["sql"] = q.get("query")
            out["sql_description"] = q.get("description")
            query_att = att.get("attachment_id") or att.get("id")
        if att.get("suggested_questions"):
            out["suggested_questions"] = (att["suggested_questions"] or {}).get("questions") or []
    if status == "COMPLETED" and query_att:
        res = _api("GET", f"/conversations/{conversation_id}/messages/{message_id}"
                          f"/attachments/{query_att}/query-result")
        sr = res.get("statement_response") or {}
        cols = [c.get("name") for c in ((sr.get("manifest") or {}).get("schema") or {}).get("columns") or []]
        rows = ((sr.get("result") or {}).get("data_array")) or []
        out.update(columns=cols, rows=rows[:_MAX_ROWS],
                   row_count=(sr.get("manifest") or {}).get("total_row_count", len(rows)),
                   truncated=len(rows) > _MAX_ROWS)
    return out


async def run(fn, *args):
    return await asyncio.to_thread(fn, *args)
