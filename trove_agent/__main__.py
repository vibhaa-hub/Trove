from __future__ import annotations

import argparse
import getpass
import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    load_dotenv(Path.cwd() / ".env")
    load_dotenv(Path(__file__).resolve().parents[1] / ".env")
    parser = argparse.ArgumentParser(description="Trove legacy vault")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--db", help="SQLite path or PostgreSQL URL (default: DATABASE_URL or trove.db)")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the web app (default)")
    sub.add_parser("worker", help="Run the background worker (timers, reminders, delivery)")
    reviewer = sub.add_parser("create-reviewer", help="Create an independent reviewer account")
    reviewer.add_argument("username")
    reviewer.add_argument("--role", default="reviewer", choices=["reviewer", "admin"])
    backup = sub.add_parser("backup", help="Back up the database and encrypted vault")
    backup.add_argument("--out", default="backups")
    sub.add_parser("warm-models", help="Download the local encoder and build the RAG index")
    sub.add_parser("check", help="Validate configuration and connectivity")
    demo = sub.add_parser("demo", help="Create the sample 'Rao family' use case for live testing (dev only)")
    demo.add_argument("--reset", action="store_true", help="Delete the demo database and files first")
    totp = sub.add_parser("totp", help="Print the current authenticator code for a setup key (testing aid)")
    totp.add_argument("secret")
    args = parser.parse_args()
    if args.command == "demo" and not args.db:
        args.db = "demo.db"

    from trove_agent import config
    from trove_agent.core.logging_setup import configure

    settings = config.load(args.db, args.host, args.port)
    config.set_current(settings)
    configure(settings.log_json, os.getenv("TROVE_LOG_LEVEL", "INFO"))
    command = args.command or "serve"

    if command == "serve":
        from trove_agent.api.server import run

        run(settings.host, settings.port, settings.database_url, settings)
    elif command == "worker":
        from trove_agent.services.legacy import LegacyService
        from trove_agent.core.store import TroveService
        from trove_agent.services.worker import run_forever

        service = TroveService(settings.database_url)
        run_forever(service, LegacyService(service), settings.worker_interval)
    elif command == "create-reviewer":
        from trove_agent.services.legacy import LegacyService
        from trove_agent.services.reviewer import ReviewerService
        from trove_agent.core.store import TroveService

        password = os.getenv("TROVE_REVIEWER_PASSWORD") or getpass.getpass("Reviewer password: ")
        service = TroveService(settings.database_url)
        result = ReviewerService(service, LegacyService(service)).create_reviewer(
            args.username, password, args.role)
        print(f"Reviewer {result['username']} created.")
        print("Add this key to an authenticator app now (shown once):")
        print(f"  {result['totp_secret']}")
        print(f"  {result['otpauth_uri']}")
    elif command == "backup":
        from trove_agent.core.backup import run_backup

        print(run_backup(settings.database_url, Path(args.out)))
    elif command == "warm-models":
        from trove_agent.agents.rag import index

        index().build()
        print(f"Knowledge index ready: {len(index().chunks)} chunks")
    elif command == "demo":
        from trove_agent.demo import run as run_demo

        print(run_demo(settings.database_url, reset=args.reset))
        print(f"(This guide was also saved next to the database as *-logins.txt)")
    elif command == "totp":
        import time

        from trove_agent.core import security as sec

        secret = args.secret.replace(" ", "").upper()
        print(f"{sec.totp_at(secret, sec.current_step())}  (valid for {30 - int(time.time()) % 30}s)")
    elif command == "check":
        from trove_agent.core.store import TroveService

        service = TroveService(settings.database_url)
        service.vault._fernet()
        print({"env": settings.env, "database": service.db.dialect, "db_ok": service.db.ping(),
               "notifier": settings.notifier, "public_origin": settings.public_origin})


if __name__ == "__main__":
    main()
