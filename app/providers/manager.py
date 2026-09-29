from app.utils.crypto import decrypt_secret
from typing import Optional, Dict
from app.config.settings import settings
from app.models.all import Provider
from app.providers.base import ProviderInterface
from app.providers.tuongtaccheo import TuongTacCheoProvider
from app.providers.generic_smm import GenericSMMProvider
from app.providers.thmxh import THMXHProvider
from app.providers.mock import MockSMMProvider

class ProviderManager:
    _instances: Dict[str, ProviderInterface] = {}

    @classmethod
    def clear_cache(cls, provider_id: Optional[int] = None):
        if provider_id is None:
            cls._instances.clear()
        else:
            keys_to_del = [k for k in cls._instances if k.endswith(f"_{provider_id}")]
            for k in keys_to_del:
                del cls._instances[k]

    @classmethod
    def get_provider(cls, provider: Optional[Provider] = None) -> ProviderInterface:
        import os
        # CRITICAL SAFEGUARD: Under test environment, NEVER connect to real providers or spend real money!
        if (
            os.environ.get("APP_ENV") == "test"
            or bool(os.environ.get("PYTEST_CURRENT_TEST"))
            or getattr(settings, "APP_ENV", "") == "test"
        ):
            mock_key = f"mock_test_{provider.id if provider else 'default'}"
            if mock_key not in cls._instances:
                cls._instances[mock_key] = MockSMMProvider(name=provider.name if provider else "Test Mock Provider")
            return cls._instances[mock_key]

        if not provider:
            # Fallback to TTC if configured, else Mock
            if settings.TTC_API_KEY and settings.TTC_API_URL:
                key = "ttc_default"
                if key not in cls._instances:
                    cls._instances[key] = TuongTacCheoProvider(
                        api_url=settings.TTC_API_URL,
                        api_key=settings.TTC_API_KEY
                    )
                return cls._instances[key]
            
            # Default mock provider
            if "mock_default" not in cls._instances:
                cls._instances["mock_default"] = MockSMMProvider()
            return cls._instances["mock_default"]

        cache_key = f"{provider.provider_type}_{provider.id}"
        if cache_key in cls._instances:
            return cls._instances[cache_key]

        ptype = (provider.provider_type or "").lower()
        if ptype == "tuongtaccheo":
            instance = TuongTacCheoProvider(
                api_url=provider.base_url or settings.TTC_API_URL,
                api_key=decrypt_secret(provider.api_key_encrypted) or settings.TTC_API_KEY or ""
            )
        elif ptype == "thmxh" or "thmxh" in (provider.base_url or "").lower():
            instance = THMXHProvider(
                api_url=provider.base_url or settings.THMXH_API_URL or "https://thmxh.com/api/v2",
                api_key=decrypt_secret(provider.api_key_encrypted) or settings.THMXH_API_KEY or "",
                usd_rate=getattr(settings, "THMXH_USD_RATE", 28000.0)
            )
        elif ptype == "generic_smm":
            instance = GenericSMMProvider(
                base_url=provider.base_url,
                api_key=decrypt_secret(provider.api_key_encrypted) or ""
            )
        else:
            instance = MockSMMProvider(name=provider.name)

        cls._instances[cache_key] = instance
        return instance
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
