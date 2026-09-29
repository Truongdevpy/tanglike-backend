import pytest
from app.providers.thmxh import THMXHProvider
from app.providers.generic_smm import GenericSMMProvider
from app.models.all import Category, Service
from app.database.session import AsyncSessionLocal
from sqlalchemy import select

def test_thmxh_platform_detection():
    adapter = THMXHProvider(api_url="https://thmxh.com/api/v2", api_key="")
    # Check category-based detection
    assert adapter._determine_platform("Shopee Live Stream Views | Server 1", "") == "Shopee"
    assert adapter._determine_platform("Website Traffic from Vietnam [+ Choose Referrer]", "") == "Webtraffic"
    assert adapter._determine_platform("Website Traffic", "") == "Webtraffic"
    assert adapter._determine_platform(" Threads Followers", "") == "Threads"
    assert adapter._determine_platform("Telegram Members Non-Drops", "") == "Telegram"
    assert adapter._determine_platform("Twitter Followers", "") == "Twitter"
    assert adapter._determine_platform("Tiktok Video Views |👁‍🗨✴️", "") == "TikTok"
    assert adapter._determine_platform("Facebook Page Follow", "") == "Facebook"
    assert adapter._determine_platform("Instagram Bot Likes ❤️", "") == "Instagram"
    assert adapter._determine_platform("YouTube | Views | Watchtime", "") == "YouTube"
    
    # Check name-based fallback detection
    assert adapter._determine_platform("General", "YouTube Livestream Views | 15 Phút") == "YouTube"
    assert adapter._determine_platform("General", "Shopee Follower VIP") == "Shopee"
    assert adapter._determine_platform("General", "Telegram Channel Members") == "Telegram"

@pytest.mark.asyncio
async def test_categories_platforms_in_db():
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Category.platform).distinct())
        db_platforms = {r[0] for r in res.fetchall() if r[0]}
        assert len(db_platforms) >= 4
        assert "Facebook" in db_platforms
        assert "TikTok" in db_platforms
