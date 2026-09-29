from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from pydantic import BaseModel

class ProviderServiceItem(BaseModel):
    service_id: str
    name: str
    category: str
    platform: str
    rate: float  # Gia von quy doi ra VND cho 1.000 don vi (GUARANTEED VND)
    raw_rate: Optional[float] = None  # Gia goc nguyen ban tu Provider (USD, XU, hoac VND)
    currency: Optional[str] = "VND"  # Don vi tien te goc cua Provider: USD, XU, VND
    min: int
    max: int
    dripfeed: bool = False
    refill: bool = False
    cancel: bool = False

class ProviderOrderStatus(BaseModel):
    order_id: str
    status: str  # PENDING, PROCESSING, COMPLETED, PARTIAL, CANCELED, FAILED
    start_count: int = 0
    remains: int = 0
    error_message: Optional[str] = None

class ProviderInterface(ABC):
    @abstractmethod
    async def get_services(self) -> List[ProviderServiceItem]:
        pass

    @abstractmethod
    async def create_order(
        self, service_id: str, link: str, quantity: int, **kwargs
    ) -> Dict[str, Any]:
        """Returns dict with at least {'order_id': str} or raises Exception"""
        pass

    @abstractmethod
    async def get_order_status(self, order_id: str) -> ProviderOrderStatus:
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def refill_order(self, order_id: str) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def get_balance(self) -> float:
        pass
