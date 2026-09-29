import asyncio
import os
import sys
sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))
from sqlalchemy import select
from app.database.session import AsyncSessionLocal
from app.models import Category, Service

SERVICES_DATA = [
    {
        'ext_id': '24',
        'name': 'Tăng Theo Dõi Facebook Cá Nhân (Sub Thường - TTC)',
        'platform': 'Facebook',
        'provider_price': 14400.0,
        'dealer_price': 16000.0,
        'price': 18800.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng lượt theo dõi nick Facebook cá nhân thật. Nhập link trang cá nhân hoặc ID Facebook.'
    },
    {
        'ext_id': '57',
        'name': 'Tăng Theo Dõi Facebook VIP Ít Tụt (Sub VIP - TTC)',
        'platform': 'Facebook',
        'provider_price': 21100.0,
        'dealer_price': 24000.0,
        'price': 27400.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Sub Facebook VIP chất lượng cao, hạn chế tụt tối đa, tốc độ ổn định.'
    },
    {
        'ext_id': '31',
        'name': 'Tăng Thành Viên Nhóm Facebook (Group Members - TTC)',
        'platform': 'Facebook',
        'provider_price': 22200.0,
        'dealer_price': 25000.0,
        'price': 28900.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng thành viên vào nhóm Facebook công khai hoặc riêng tư. Nhập link nhóm.'
    },
    {
        'ext_id': '30',
        'name': 'Tăng Đề Xuất / Đánh Giá 5 Sao Fanpage (Review Page - TTC)',
        'platform': 'Facebook',
        'provider_price': 27800.0,
        'dealer_price': 31000.0,
        'price': 36100.0,
        'min_q': 20,
        'max_q': 10000000,
        'desc': 'Tăng review, đánh giá kèm nội dung đề xuất cho Fanpage Facebook.'
    },
    {
        'ext_id': '23',
        'name': 'Tăng Like / Follow Fanpage Facebook (Like Page - TTC)',
        'platform': 'Facebook',
        'provider_price': 20000.0,
        'dealer_price': 23000.0,
        'price': 26000.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng lượt thích và người theo dõi trang Fanpage Facebook. Nhập link page.'
    },
    {
        'ext_id': '17',
        'name': 'Tăng Tym TikTok Server 1 Siêu Tốc (Like TikTok V1 - TTC)',
        'platform': 'TikTok',
        'provider_price': 6700.0,
        'dealer_price': 7500.0,
        'price': 8700.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng like / thả tim video TikTok siêu tốc độ, giá rẻ.'
    },
    {
        'ext_id': '15',
        'name': 'Tăng Tym TikTok Server 2 Chất Lượng Cao (Like TikTok VIP - TTC)',
        'platform': 'TikTok',
        'provider_price': 11100.0,
        'dealer_price': 12500.0,
        'price': 14400.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng like video TikTok chất lượng cao, giữ tương tác tốt.'
    },
    {
        'ext_id': '47',
        'name': 'VIP Like TikTok Tự Động (Gói 3 bài/ngày | 7 ngày - TTC)',
        'platform': 'TikTok',
        'provider_price': 175000.0,
        'dealer_price': 200000.0,
        'price': 227500.0,
        'min_q': 1,
        'max_q': 100,
        'desc': 'Tự động tăng like cho 3 bài viết TikTok mới mỗi ngày trong 7 ngày.'
    },
    {
        'ext_id': '48',
        'name': 'VIP Like TikTok Tự Động (Gói 7 bài/ngày | 7 ngày - TTC)',
        'platform': 'TikTok',
        'provider_price': 408300.0,
        'dealer_price': 460000.0,
        'price': 530800.0,
        'min_q': 1,
        'max_q': 100,
        'desc': 'Tự động tăng like cho 7 bài viết TikTok mới mỗi ngày trong 7 ngày.'
    },
    {
        'ext_id': '25',
        'name': 'Tăng Lưu / Yêu Thích Video TikTok (Save TikTok - TTC)',
        'platform': 'TikTok',
        'provider_price': 9400.0,
        'dealer_price': 10500.0,
        'price': 12300.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng lượt thêm vào mục yêu thích (Bookmark/Save) cho video TikTok, giúp đẩy xu hướng.'
    },
    {
        'ext_id': '26',
        'name': 'Tăng Chia Sẻ Video TikTok (Share Video TikTok - TTC)',
        'platform': 'TikTok',
        'provider_price': 11700.0,
        'dealer_price': 13000.0,
        'price': 15200.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng lượt share cho video TikTok giúp lan truyền thuật toán.'
    },
    {
        'ext_id': '27',
        'name': 'Tăng Lượt Xem Video TikTok Siêu Nhanh (View TikTok - TTC)',
        'platform': 'TikTok',
        'provider_price': 2800.0,
        'dealer_price': 3100.0,
        'price': 3600.0,
        'min_q': 1000,
        'max_q': 10000000,
        'desc': 'Tăng lượt xem video TikTok siêu tốc độ, kéo đề xuất tài khoản.'
    },
    {
        'ext_id': 'ttc_tiktok_share_live',
        'name': 'Tăng Chia Sẻ LIVE TikTok (Share Live TikTok Siêu Tốc - TTC)',
        'platform': 'TikTok',
        'provider_price': 16700.0,
        'dealer_price': 19000.0,
        'price': 21700.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Nhập link livestream TikTok hoặc username đang phát trực tiếp. Lượt share bắt đầu tăng sau 1 - 10 phút.'
    },
    {
        'ext_id': '29',
        'name': 'Tăng Bình Luận Video TikTok Theo Yêu Cầu (Cmt TikTok - TTC)',
        'platform': 'TikTok',
        'provider_price': 38900.0,
        'dealer_price': 44000.0,
        'price': 50600.0,
        'min_q': 10,
        'max_q': 10000000,
        'desc': 'Tăng bình luận tùy chọn cho video TikTok. Nhập mỗi dòng là 1 comment.'
    },
    {
        'ext_id': '16',
        'name': 'Tăng Người Theo Dõi TikTok Chất Lượng (Sub TikTok Thường - TTC)',
        'platform': 'TikTok',
        'provider_price': 14400.0,
        'dealer_price': 16000.0,
        'price': 18800.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Tăng follow TikTok người dùng thật Việt Nam, đủ điều kiện bật livestream và kiếm tiền.'
    },
    {
        'ext_id': '56',
        'name': 'Tăng Follow TikTok Siêu VIP Ít Tụt (Sub TikTok VIP - TTC)',
        'platform': 'TikTok',
        'provider_price': 38900.0,
        'dealer_price': 44000.0,
        'price': 50600.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Follow TikTok VIP chuẩn người dùng thật, tỷ lệ tụt cực thấp.'
    },
    {
        'ext_id': '28',
        'name': 'Tăng Bình Luận Video YouTube Theo Ý Muốn (Cmt YouTube - TTC)',
        'platform': 'YouTube',
        'provider_price': 38900.0,
        'dealer_price': 44000.0,
        'price': 50600.0,
        'min_q': 15,
        'max_q': 10000000,
        'desc': 'Tăng bình luận cho video YouTube. Mỗi dòng là 1 nội dung bình luận.'
    },
    {
        'ext_id': 'ttc_youtube_sub',
        'name': 'Tăng Theo Dõi Kênh YouTube (Sub YouTube Thật - TTC)',
        'platform': 'YouTube',
        'provider_price': 27800.0,
        'dealer_price': 31000.0,
        'price': 36100.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Kênh YouTube cần đăng ít nhất 1 video để tránh tụt. Đạt chuẩn điều kiện bật kiếm tiền.'
    },
    {
        'ext_id': '58',
        'name': 'Tăng Đánh Giá / Review 5 Sao Google Maps (Google Reviews - TTC)',
        'platform': 'Google',
        'provider_price': 1111100.0,
        'dealer_price': 1250000.0,
        'price': 1444400.0,
        'min_q': 5,
        'max_q': 10000000,
        'desc': 'Tăng đánh giá 5 sao kèm bài nhận xét thật trên Google Maps, giúp lên top tìm kiếm địa điểm.'
    },
    {
        'ext_id': '1',
        'name': 'Tăng LIKE Bài Viết Facebook Giá Rẻ (Like Thường - TTC)',
        'platform': 'Facebook',
        'provider_price': 11100.0,
        'dealer_price': 12500.0,
        'price': 14400.0,
        'min_q': 10,
        'max_q': 10000000,
        'desc': 'Tăng like bài viết Facebook người dùng Việt Nam giá rẻ nhất. Có hỗ trợ chọn cảm xúc.'
    },
    {
        'ext_id': '2',
        'name': 'Tăng LIKE Bài Viết Facebook VIP Chất Lượng Cao (Like VIP - TTC)',
        'platform': 'Facebook',
        'provider_price': 20000.0,
        'dealer_price': 23000.0,
        'price': 26000.0,
        'min_q': 50,
        'max_q': 10000000,
        'desc': 'Like bài viết VIP nick thật, tương tác cao, tốc độ lên nhanh.'
    }
]

async def sync():
    async with AsyncSessionLocal() as session:
        r_cats = await session.execute(select(Category))
        cats = r_cats.scalars().all()
        cat_map = {c.platform: c.id for c in cats}

        for item in SERVICES_DATA:
            r_s = await session.execute(
                select(Service).where(
                    Service.provider_id == 1,
                    Service.external_service_id == item['ext_id']
                )
            )
            svc = r_s.scalars().first()
            cat_id = cat_map.get(item['platform'], 1)

            if svc:
                svc.name = item['name']
                svc.platform = item['platform']
                svc.category_id = cat_id
                svc.provider_price = item['provider_price']
                svc.dealer_price = item['dealer_price']
                svc.price = item['price']
                svc.min_quantity = item['min_q']
                svc.max_quantity = item['max_q']
                svc.description = item['desc']
                svc.status = 'ACTIVE'
                svc.is_deleted = False
                print(f"Updated: {svc.id} - {item['name']}")
            else:
                new_s = Service(
                    provider_id=1,
                    category_id=cat_id,
                    external_service_id=item['ext_id'],
                    name=item['name'],
                    description=item['desc'],
                    platform=item['platform'],
                    service_type='default',
                    price=item['price'],
                    dealer_price=item['dealer_price'],
                    provider_price=item['provider_price'],
                    min_quantity=item['min_q'],
                    max_quantity=item['max_q'],
                    dripfeed_enabled=False,
                    refill_enabled=False,
                    cancel_enabled=False,
                    status='ACTIVE',
                    is_deleted=False
                )
                session.add(new_s)
                print(f"Added: {item['name']}")

        await session.commit()
        print('Successfully committed all TTC services to DB!')

asyncio.run(sync())
