import uuid
import random
from typing import Dict, Any, List
from app.providers.base import ProviderInterface, ProviderServiceItem, ProviderOrderStatus

class MockSMMProvider(ProviderInterface):
    """
    Mock provider for local development, tests, and offline resilience.
    Generates realistic responses without requiring real credentials.
    """
    def __init__(self, name: str = "TangLike Core Provider"):
        self.name = name

    async def get_services(self) -> List[ProviderServiceItem]:
        return [
            ProviderServiceItem(
                service_id="mock_fb_like",
                name="Facebook Post Likes [Cực Nhanh - Không Tụt]",
                category="Facebook Likes",
                platform="Facebook",
                rate=12000.0,
                min=50,
                max=50000,
                refill=True,
                cancel=True
            ),
            ProviderServiceItem(
                service_id="mock_fb_follow",
                name="Facebook Follow Profile [Sub Thật]",
                category="Facebook Follow",
                platform="Facebook",
                rate=28000.0,
                min=100,
                max=100000,
                refill=True
            ),
            ProviderServiceItem(
                service_id="mock_tt_view",
                name="TikTok Video Views [Lên Siêu Tốc]",
                category="TikTok Views",
                platform="TikTok",
                rate=1500.0,
                min=500,
                max=1000000
            ),
            ProviderServiceItem(
                service_id="mock_tt_follower",
                name="TikTok Followers Việt Nam",
                category="TikTok Followers",
                platform="TikTok",
                rate=45000.0,
                min=100,
                max=50000,
                refill=True
            ),
            ProviderServiceItem(
                service_id="mock_yt_sub",
                name="YouTube Subscribers HQ",
                category="YouTube Subscribers",
                platform="YouTube",
                rate=150000.0,
                min=50,
                max=10000,
                refill=True
            ),
            ProviderServiceItem(
                service_id="mock_ig_like",
                name="Instagram Likes Quốc Tế",
                category="Instagram Likes",
                platform="Instagram",
                rate=8000.0,
                min=50,
                max=20000
            ),
        ]

    async def create_order(self, service_id: str, link: str, quantity: int, **kwargs) -> Dict[str, Any]:
        random_id = f"MOCK-{random.randint(100000, 999999)}"
        return {"order_id": random_id}

    async def get_order_status(self, order_id: str) -> ProviderOrderStatus:
        return ProviderOrderStatus(
            order_id=order_id,
            status="PROCESSING",
            start_count=random.randint(10, 500),
            remains=0
        )

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        return {"status": "success", "message": f"Order {order_id} canceled successfully"}

    async def refill_order(self, order_id: str) -> Dict[str, Any]:
        return {"status": "success", "refill_id": f"MOCK-REF-{order_id}"}

    async def get_balance(self) -> float:
        return 5000000.0

    async def test_connection(self) -> Dict[str, Any]:
        return {"success": True, "balance": 5000000.0, "currency": "VND"}
