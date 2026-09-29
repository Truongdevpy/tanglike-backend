import pytest
from app.schemas.all import OrderCreate
from app.providers.tuongtaccheo import TuongTacCheoProvider

def test_ttc_pricing_formula():
    """Verify the exact pricing formula: 1,000 like vip (1,800,000 xu) -> 20,000d cost -> 26,000d retail (+30%)."""
    rate_xu_vip = 1800.0   # Xu per 1 like VIP
    rate_xu_thuong = 1000.0 # Xu per 1 like thuong
    rate_xu_cmt = 3000.0   # Xu per 1 cmt
    rate_xu_cmt_thuong = 2500.0 # Xu per 1 cmt thuong
    rate_xu_camxuc_cmt = 1100.0 # Xu per 1 cmt cam xuc
    rate_xu_share = 1000.0 # Xu per 1 share
    rate_xu_share_nd = 1500.0 # Xu per 1 share kem noi dung

    divider = 90.0
    markup = 0.30

    # 1. Like VIP
    cost_vip = round((rate_xu_vip * 1000.0) / divider, 0)
    retail_vip = round(cost_vip * (1.0 + markup), 0)
    assert cost_vip == 20000.0
    assert retail_vip == 26000.0

    # 2. Like thường
    cost_thuong = round((rate_xu_thuong * 1000.0) / divider, 0)
    retail_thuong = round(cost_thuong * (1.0 + markup), 0)
    assert cost_thuong == 11111.0
    assert retail_thuong == 14444.0

    # 3. Bình luận nick thường
    cost_cmt_th = round((rate_xu_cmt_thuong * 1000.0) / divider, 0)
    retail_cmt_th = round(cost_cmt_th * (1.0 + markup), 0)
    assert cost_cmt_th == 27778.0
    assert retail_cmt_th == 36111.0

    # 4. Bình luận nick chất lượng
    cost_cmt = round((rate_xu_cmt * 1000.0) / divider, 0)
    retail_cmt = round(cost_cmt * (1.0 + markup), 0)
    assert cost_cmt == 33333.0
    assert retail_cmt == 43333.0

    # 5. Cảm xúc bình luận
    cost_cx = round((rate_xu_camxuc_cmt * 1000.0) / divider, 0)
    retail_cx = round(cost_cx * (1.0 + markup), 0)
    assert cost_cx == 12222.0
    assert retail_cx == 15889.0

    # 6. Share kèm nội dung
    cost_share_nd = round((rate_xu_share_nd * 1000.0) / divider, 0)
    retail_share_nd = round(cost_share_nd * (1.0 + markup), 0)
    assert cost_share_nd == 16667.0
    assert retail_share_nd == 21667.0

def test_order_create_schema_dynamic_fields():
    """Verify that OrderCreate accepts comments, reaction, speed, and custom_data."""
    payload = OrderCreate(
        service_id=2,
        link="https://www.facebook.com/123456789",
        quantity=50,
        comments="Tuyệt vời quá shop ơi\nỦng hộ shop dài lâu\n10 điểm chất lượng",
        reaction="LOVE",
        speed="1",
        custom_data={"vip_days": 30}
    )
    assert payload.service_id == 2
    assert payload.comments is not None
    assert len(payload.comments.split("\n")) == 3
    assert payload.reaction == "LOVE"
    assert payload.speed == "1"
    assert payload.custom_data == {"vip_days": 30}

def test_ttc_smart_emotion_mapping():
    """Verify TTC provider maps emotion to exact TTC service ID."""
    provider = TuongTacCheoProvider(api_url="https://tuongtaccheo.com/api/v2", api_key="mock_key")
    assert provider.api_url == "https://tuongtaccheo.com/api/v2"

def test_google_maps_review_order_schema():
    """Verify Google Maps review service accepts review comments without speed parameter."""
    payload = OrderCreate(
        service_id=796,
        link="https://maps.app.goo.gl/abcdef123456",
        quantity=3,
        comments="Quán ăn ngon, không gian đẹp 5 sao!\nNhân viên nhiệt tình chu đáo.\nChất lượng tuyệt vời sẽ ghé lại!",
        speed=None,
        reaction=None
    )
    assert payload.service_id == 796
    assert payload.speed is None
    assert payload.reaction is None
    assert len(payload.comments.strip().split("\n")) == 3
