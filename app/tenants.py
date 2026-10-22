"""Clients ("tenants") and their API keys.

Each client gets one secret key. Only its SHA-256 hash is stored, so a leaked
database does not leak working keys. The key is shown once, when it is created.

    python -m app.tenants create "Acme IT Services"
    python -m app.tenants list
"""
import hashlib
import secrets
import sys

from app.db import connect, init_schema


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def new_widget_token() -> str:
    return "wk_" + secrets.token_urlsafe(16)


def create_tenant(name: str) -> tuple[dict, str]:
    """Return (tenant, api_key). The key cannot be recovered later."""
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    init_schema()
    key = "kb_" + secrets.token_urlsafe(24)
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO tenants (name, api_key_hash, widget_token) VALUES (%s, %s, %s) "
            "RETURNING id, name, created_at",
            (name, hash_key(key), new_widget_token()),
        ).fetchone()
    return row, key


def get_tenant(key: str | None) -> dict | None:
    if not key:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT id, name FROM tenants WHERE api_key_hash = %s", (hash_key(key),)
        ).fetchone()


def get_tenant_by_widget_token(token: str | None) -> dict | None:
    """The widget key is public (it sits in the client's web page); it only allows asking."""
    if not token:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT id, name, allowed_origins FROM tenants WHERE widget_token = %s", (token,)
        ).fetchone()


def get_widget(tenant_id: int) -> dict:
    """Token and allowed origins of a client (creates the token for older clients)."""
    with connect() as conn:
        row = conn.execute(
            "SELECT widget_token, allowed_origins FROM tenants WHERE id = %s", (tenant_id,)
        ).fetchone()
        if not row["widget_token"]:
            token = new_widget_token()
            conn.execute("UPDATE tenants SET widget_token = %s WHERE id = %s", (token, tenant_id))
            row = {**row, "widget_token": token}
    return {"token": row["widget_token"], "allowed_origins": list(row["allowed_origins"])}


def set_allowed_origins(tenant_id: int, origins: list[str]) -> None:
    with connect() as conn:
        conn.execute("UPDATE tenants SET allowed_origins = %s WHERE id = %s", (origins, tenant_id))


def list_tenants() -> list[dict]:
    init_schema()
    with connect() as conn:
        return conn.execute(
            "SELECT t.id, t.name, t.created_at, "
            "(SELECT count(DISTINCT slug) FROM chunks c WHERE c.tenant_id = t.id) AS documents "
            "FROM tenants t ORDER BY t.id"
        ).fetchall()


def delete_tenant(tenant_id: int) -> None:
    """Remove a client and everything of theirs (documents and question log)."""
    with connect() as conn:
        conn.execute("DELETE FROM chunks WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM query_log WHERE tenant_id = %s", (tenant_id,))
        conn.execute("DELETE FROM tenants WHERE id = %s", (tenant_id,))


def main(argv: list[str]) -> int:
    if len(argv) >= 3 and argv[1] == "create":
        tenant, key = create_tenant(" ".join(argv[2:]))
        print(f"Client created: {tenant['name']} (id {tenant['id']})")
        print(f"API key (secret, shown only once, save it now): {key}")
        print(f"Widget key (public, can only ask questions): {get_widget(tenant['id'])['token']}")
        return 0
    if len(argv) == 2 and argv[1] == "list":
        for t in list_tenants():
            print(f"{t['id']:>3}  {t['name']}  documents={t['documents']}  created={t['created_at']:%Y-%m-%d}")
        return 0
    if len(argv) == 3 and argv[1] == "delete" and argv[2].isdigit():
        delete_tenant(int(argv[2]))
        print("Client and all their data deleted.")
        return 0
    print('Usage: python -m app.tenants create "Name" | list | delete <id>')
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
