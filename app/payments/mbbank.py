"""MB Bank transaction-source adapter.

The adapter intentionally targets a configured middleware/API endpoint rather than
scraping bank sessions. It accepts common transaction field names and keeps bank
credentials out of application records and browser responses.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import httpx


@dataclass(frozen=True)
class BankTransactionPayload:
    transaction_id: str
    amount: float
    content: str
    occurred_at: datetime | None
    raw: dict[str, Any]


class MBBankTransactionSource:
    def __init__(self, api_url: str = "", api_token: str = "") -> None:
        self.api_url = (api_url or "").strip()
        self.api_token = (api_token or "").strip()
        if not self.api_url and self.api_token:
            self.api_url = f"https://thueapi.pro/historyapimbbankv2/{self.api_token}"

    @property
    def configured(self) -> bool:
        return bool(self.api_url or self.api_token)

    async def fetch_transactions(self) -> list[BankTransactionPayload]:
        if not self.configured:
            return []
        if self.api_url and not self.api_url.startswith("https://"):
            raise ValueError("MB_BANK_API_URL must use HTTPS")

        headers = {"Accept": "application/json", "User-Agent": "tanglike-bank-sync/1.0"}
        if self.api_token and not self.api_url.startswith("https://thueapi.pro/"):
            headers["Authorization"] = f"Bearer {self.api_token}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(20.0), follow_redirects=True, max_redirects=3) as client:
            response = await client.get(self.api_url, headers=headers)
            response.raise_for_status()
            body = response.json()

        items: Iterable[Any]
        if isinstance(body, list):
            items = body
        elif isinstance(body, dict):
            items = body.get("transactions") or body.get("data") or body.get("items") or []
        else:
            raise ValueError("MB Bank source returned an unsupported response")

        transactions: list[BankTransactionPayload] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            # Skip outgoing transfers
            tx_type = str(item.get("type") or item.get("transactionType") or "").upper()
            if tx_type == "OUT":
                continue

            transaction_id = str(
                item.get("transaction_id")
                or item.get("transactionId")
                or item.get("transactionID")
                or item.get("refNo")
                or item.get("id")
                or ""
            ).strip()
            amount_raw = item.get("amount") or item.get("creditAmount") or item.get("transferAmount") or 0
            content = str(item.get("content") or item.get("description") or item.get("memo") or "").strip()
            try:
                amount = float(amount_raw)
            except (TypeError, ValueError):
                continue
            if not transaction_id or amount <= 0:
                continue
            transactions.append(BankTransactionPayload(transaction_id, amount, content, None, item))
        return transactions
