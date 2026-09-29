with open("backend/tests/test_provider_sync_feature.py", "r", encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    'assert prices["dealer_price"] == 115.0',
    'assert prices["dealer_price"] == 120.0  # 115 rounded to nearest multiple of 10 is 120'
)

with open("backend/tests/test_provider_sync_feature.py", "w", encoding="utf-8") as f:
    f.write(text)

print("Test assertion updated")