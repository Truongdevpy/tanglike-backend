import asyncio
from sqlalchemy import select
from app.database.session import AsyncSessionLocal
from app.models import Category, Service

async def main():
    async with AsyncSessionLocal() as session:
        # Check categories
        r_cat = await session.execute(select(Category))
        cats = r_cat.scalars().all()
        tt_cat = next((c for c in cats if c.platform == "TikTok"), None)
        yt_cat = next((c for c in cats if c.platform == "YouTube"), None)
        print("TikTok Cat:", tt_cat.id if tt_cat else None, tt_cat.name if tt_cat else None)
        print("YouTube Cat:", yt_cat.id if yt_cat else None, yt_cat.name if yt_cat else None)
        
        # Check if services exist
        r_s1 = await session.execute(select(Service).filter(Service.external_service_id == "ttc_tiktok_share_live"))
        s1 = r_s1.scalars().first()
        r_s2 = await session.execute(select(Service).filter(Service.external_service_id == "ttc_youtube_sub"))
        s2 = r_s2.scalars().first()
        
        if not s1:
            s1 = Service(
                provider_id=1,
                category_id=tt_cat.id if tt_cat else 2,
                external_service_id="ttc_tiktok_share_live",
                name="Tăng Chia Sẻ LIVE TikTok (Share Live TikTok Siêu Tốc)",
                description="Nhập link livestream TikTok hoặc username đang phát trực tiếp. Lượt share bắt đầu tăng sau 1 - 10 phút.",
                platform="TikTok",
                service_type="default",
                provider_price=16700.0,
                dealer_price=19000.0,
                price=21700.0,
                min_quantity=50,
                max_quantity=10000000,
                dripfeed_enabled=False,
                refill_enabled=False,
                cancel_enabled=False,
                status="ACTIVE"
            )
            session.add(s1)
            print("Added ttc_tiktok_share_live")
        else:
            print("ttc_tiktok_share_live exists")

        if not s2:
            s2 = Service(
                provider_id=1,
                category_id=yt_cat.id if yt_cat else 4,
                external_service_id="ttc_youtube_sub",
                name="Tăng Theo Dõi Kênh YouTube (YouTube Subscribers Thật)",
                description="Lưu ý: Kênh YouTube cần đăng ít nhất 1 video mới có thể mua để tránh tụt. Trong quá trình tăng không được xóa video!",
                platform="YouTube",
                service_type="subscribers",
                provider_price=27800.0,
                dealer_price=31000.0,
                price=36100.0,
                min_quantity=50,
                max_quantity=10000000,
                dripfeed_enabled=False,
                refill_enabled=False,
                cancel_enabled=False,
                status="ACTIVE"
            )
            session.add(s2)
            print("Added ttc_youtube_sub")
        
        await session.commit()
        print("Committed successfully!")

asyncio.run(main())