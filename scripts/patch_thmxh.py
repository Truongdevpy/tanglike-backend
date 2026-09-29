with open("backend/app/providers/thmxh.py", "r", encoding="utf-8") as f:
    text = f.read()

# Add rate limiter in _post_with_retry
old_post = """    async def _post_with_retry(self, data: Dict[str, Any], timeout: float = 20.0) -> httpx.Response:
        from app.utils.network import async_retry
        async def _call():
            async with httpx.AsyncClient(timeout=timeout) as client:
                res = await client.post(self.api_url, data=data)
                if res.status_code >= 500:
                    res.raise_for_status()
                return res
        return await async_retry(_call, max_retries=3, initial_delay=0.5)"""

new_post = """    async def _post_with_retry(self, data: Dict[str, Any], timeout: float = 20.0) -> httpx.Response:
        from app.utils.network import async_retry
        from app.providers.rate_limiter import provider_rate_limiter
        await provider_rate_limiter.acquire(self.api_url)
        try:
            async def _call():
                async with httpx.AsyncClient(timeout=timeout) as client:
                    res = await client.post(self.api_url, data=data)
                    if res.status_code >= 500:
                        res.raise_for_status()
                    return res
            return await async_retry(_call, max_retries=3, initial_delay=0.5)
        finally:
            provider_rate_limiter.release(self.api_url)

    async def test_connection(self) -> Dict[str, Any]:
        \"\"\"Kiểm tra API Key và URL của THMXH bằng action=balance.\"\"\"
        if not self.api_key:
            return {"success": False, "error": "API Key không được để trống."}
        try:
            res = await self._post_with_retry({"key": self.api_key, "action": "balance"}, timeout=12.0)
            if res.status_code != 200:
                return {"success": False, "error": f"THMXH phản hồi HTTP {res.status_code}: {res.text[:200]}"}
            data = res.json()
            if isinstance(data, dict):
                if "error" in data:
                    return {"success": False, "error": f"Lỗi từ THMXH: {data['error']}"}
                if "balance" in data:
                    bal = float(data.get("balance", 0.0))
                    curr = str(data.get("currency", "USD"))
                    return {"success": True, "balance": bal, "currency": curr}
            return {"success": False, "error": f"Phản hồi không hợp lệ: {str(data)[:200]}"}
        except Exception as e:
            logger.error(f"THMXH test_connection error: {e}")
            return {"success": False, "error": f"Không thể kết nối đến THMXH: {str(e)}"}"""

if old_post in text:
    text = text.replace(old_post, new_post, 1)
    with open("backend/app/providers/thmxh.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("THMXHProvider updated with rate limiter and test_connection!")
else:
    print("Pattern not found in THMXHProvider")
