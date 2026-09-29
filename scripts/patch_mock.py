with open("backend/app/providers/mock.py", "r", encoding="utf-8") as f:
    text = f.read()

if "async def test_connection" not in text:
    text += """
    async def test_connection(self) -> Dict[str, Any]:
        return {"success": True, "balance": 5000000.0, "currency": "VND"}
"""
    with open("backend/app/providers/mock.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Mock provider test_connection added!")
