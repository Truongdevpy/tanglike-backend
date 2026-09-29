"""CLI entrypoint for bank transaction auto-sync cron job.

Usage:
    python backend/cron_bank_sync.py
    python backend/cron_bank_sync.py --secret=YOUR_CRON_SECRET
"""
import argparse
import asyncio
import os
import sys

# Ensure backend root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.database.session import AsyncSessionLocal
from app.payments.bank_sync import BankSyncService
from app.payments.service import PaymentService
import secrets

async def main():
    parser = argparse.ArgumentParser(description="TangLike Bank Sync Cron CLI")
    parser.add_argument("--secret", default=os.getenv("BANK_CRON_SECRET", ""), help="Cron secret for authentication")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        cfg = await PaymentService(db).get_banking_config()
        configured_secret = (cfg.get("cron_secret") or "").strip()

        if configured_secret:
            if not args.secret or not secrets.compare_digest(args.secret.strip(), configured_secret):
                print("Error: Invalid or missing cron secret.", file=sys.stderr)
                sys.exit(1)

        result = await BankSyncService(db).sync_mbbank()
        print(f"Bank Sync Result: {result}")
        if result.get("failed", 0) > 0 and result.get("processed", 0) == 0 and result.get("seen", 0) == 0 and "error" in result:
            sys.exit(1)

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    asyncio.run(main())
