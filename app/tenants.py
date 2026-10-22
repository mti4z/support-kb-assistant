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


def create_tenant(name: str) -> tuple[dict, str]:
    """Return (tenant, api_key). The key cannot be recovered later."""
    name = name.strip()
    if not name:
        raise ValueError("name is required")
    init_schema()
    key = "kb_" + secrets.token_urlsafe(24)
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO tenants (name, api_key_hash) VALUES (%s, %s) "
            "RETURNING id, name, created_at",
            (name, hash_key(key)),
        ).fetchone()
    return row, key


def get_tenant(key: str | None) -> dict | None:
    if not key:
        return None
    with connect() as conn:
        return conn.execute(
            "SELECT id, name FROM tenants WHERE api_key_hash = %s", (hash_key(key),)
        ).fetchone()


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
        print(f"API key (shown only once, save it now): {key}")
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
