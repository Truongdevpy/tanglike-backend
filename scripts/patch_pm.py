with open("backend/app/providers/manager.py", "r", encoding="utf-8") as f:
    text = f.read()

if "def create_transient_provider" not in text:
    method = """
    @classmethod
    def create_transient_provider(cls, base_url: str, api_key: str, provider_type: str = "generic_smm") -> ProviderInterface:
        ptype = (provider_type or "").lower()
        if ptype == "tuongtaccheo":
            return TuongTacCheoProvider(api_url=base_url, api_key=api_key)
        elif ptype == "thmxh" or "thmxh" in (base_url or "").lower():
            return THMXHProvider(api_url=base_url, api_key=api_key)
        elif ptype == "generic_smm":
            return GenericSMMProvider(base_url=base_url, api_key=api_key)
        else:
            return MockSMMProvider(name="Transient Provider")
"""
    text += method
    with open("backend/app/providers/manager.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("ProviderManager updated with create_transient_provider!")
