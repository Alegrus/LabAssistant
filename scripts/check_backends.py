"""Preflight check for the two remote backends (Postgres + Ollama).

Run this first whenever the app won't start or answers stop working — especially when
working away from the home LAN. It reports, per backend, whether the host resolves, the
port accepts a connection, and the service actually answers, with the round-trip time.

    uv run python scripts/check_backends.py
"""
import socket
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import text
from sqlalchemy.engine import make_url

# Runnable directly (`python scripts/check_backends.py`) without installing the package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings  # noqa: E402

OK, BAD, WARN = "\033[32m✓\033[0m", "\033[31m✗\033[0m", "\033[33m!\033[0m"


def _resolve(host: str) -> str | None:
    try:
        return socket.gethostbyname(host)
    except OSError:
        return None


def _port_open(host: str, port: int, timeout: float = 5.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_host(label: str, host: str, port: int) -> bool:
    """Shared DNS + TCP reachability check. Returns False if we can't get a socket."""
    ip = _resolve(host)
    if ip is None:
        print(f"  {BAD} DNS: cannot resolve {host!r}")
        if host.endswith(".ts.net"):
            print("      Tailscale name — is Tailscale running and logged in on THIS machine?")
        return False
    print(f"  {OK} DNS: {host} -> {ip}")

    started = time.perf_counter()
    if not _port_open(host, port):
        print(f"  {BAD} TCP: {host}:{port} refused or timed out")
        print(f"      Is the {label} service up, and are you on the same network/tailnet?")
        return False
    print(f"  {OK} TCP: {host}:{port} open ({(time.perf_counter() - started) * 1000:.0f} ms)")
    return True


def check_database() -> bool:
    url = make_url(settings.database_url)
    host, port = url.host or "localhost", url.port or 5432
    print(f"\nDatabase  ({url.database} @ {host}:{port})")
    if not _check_host("Postgres", host, port):
        return False

    try:
        from app.database import engine

        started = time.perf_counter()
        with engine.connect() as conn:
            version = conn.execute(text("select version()")).scalar() or ""
            vector = conn.execute(
                text("select extversion from pg_extension where extname='vector'")
            ).scalar()
            docs = conn.execute(text("select count(*) from documents")).scalar()
            chunks = conn.execute(text("select count(*) from chunks")).scalar()
        elapsed = (time.perf_counter() - started) * 1000
    except Exception as exc:  # noqa: BLE001 - report any failure to the user
        print(f"  {BAD} Query failed: {type(exc).__name__}: {str(exc)[:120]}")
        return False

    print(f"  {OK} Connected ({elapsed:.0f} ms) — {version.split(',')[0]}")
    if vector:
        print(f"  {OK} pgvector {vector}")
    else:
        print(f"  {BAD} pgvector extension missing — retrieval will fail")
        return False
    print(f"  {OK} Indexed: {docs} documents, {chunks} chunks")
    if not docs:
        print(f"  {WARN} No manuals uploaded — every answer will be 'not found'")
    return True


def check_llm() -> bool:
    base = settings.llm_base_url.rstrip("/")
    parsed = urlparse(base)
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    print(f"\nLLM / vision  (provider={settings.llm_provider}, {base})")

    if settings.llm_provider != "local":
        print(f"  {WARN} Provider is not 'local'; skipping mini checks.")
        return True
    if not _check_host("Ollama", host, port):
        return False

    try:
        started = time.perf_counter()
        resp = httpx.get(f"{base}/models", timeout=15)
        resp.raise_for_status()
        installed = {m["id"] for m in resp.json().get("data", [])}
        elapsed = (time.perf_counter() - started) * 1000
    except Exception as exc:  # noqa: BLE001
        print(f"  {BAD} API call failed: {type(exc).__name__}: {str(exc)[:120]}")
        return False

    print(f"  {OK} API responding ({elapsed:.0f} ms) — {len(installed)} models installed")

    ok = True
    for label, name in (
        ("chat", settings.llm_chat_model),
        ("vision", settings.llm_vision_model),
    ):
        if name in installed:
            print(f"  {OK} {label} model present: {name}")
        else:
            print(f"  {BAD} {label} model NOT installed: {name}")
            print(f"      Fix: ssh to the host and run  ollama pull {name}")
            ok = False
    return ok


def main() -> int:
    print("Backend preflight — checking the services this app depends on.")
    results = [check_database(), check_llm()]
    print()
    if all(results):
        print(f"{OK} All backends reachable. Start the app with:")
        print("    uv run uvicorn app.main:app --host 0.0.0.0 --port 8000")
        return 0
    print(f"{BAD} One or more backends are unreachable — see above.")
    print("    Away from the home network? Both machines must be on the same tailnet;")
    print("    check Tailscale is running here and the host is online in `tailscale status`.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
