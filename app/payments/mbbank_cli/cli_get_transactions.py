import argparse
import io
import json
import os
import sys
from contextlib import redirect_stdout
from datetime import datetime, timedelta

# Import the existing test_mb which contains MBBankService
import test_mb

def _default_from_date():
    return (datetime.now() - timedelta(days=2)).strftime("%d/%m/%Y")

def _default_to_date():
    return datetime.now().strftime("%d/%m/%Y")

def main():
    parser = argparse.ArgumentParser(description="Standalone MBBank login/balance/transaction flow returning pure JSON.")
    parser.add_argument("--config-file", required=True)
    args = parser.parse_args()

    with open(args.config_file, 'r', encoding='utf-8') as f:
        config = json.load(f)

    username = config.get("username")
    password = config.get("password")
    account_no = config.get("account_no")
    from_date = config.get("from_date", _default_from_date())
    to_date = config.get("to_date", _default_to_date())

    f_out = io.StringIO()
    with redirect_stdout(f_out):
        try:
            service = test_mb.MBBankService.auto_login(username=username, password=password)
            transactions_data = service.get_transactions(
                account_no=account_no,
                from_date=from_date,
                to_date=to_date,
            )
            
            # Extract transactions
            tx_list = transactions_data.get("transactionHistoryList", [])
            formatted_txs = []
            for tx in tx_list:
                credit = float(tx.get("creditAmount", 0))
                debit = float(tx.get("debitAmount", 0))
                
                if credit > 0:
                    amount = int(credit)
                    tx_type = "IN"
                else:
                    amount = int(debit)
                    tx_type = "OUT"
                    
                formatted_txs.append({
                    "transactionID": tx.get("refNo", tx.get("transactionId", "")),
                    "amount": amount,
                    "type": tx_type,
                    "description": tx.get("description", ""),
                    "transactionDate": tx.get("transactionDate", "")
                })

            result = {
                "status": "success",
                "transactions": formatted_txs,
                "raw_response": transactions_data
            }
        except Exception as exc:
            result = {
                "status": "error",
                "message": str(exc),
                "debug": f_out.getvalue()
            }

    print(json.dumps(result, ensure_ascii=False))

if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
