import math
from decimal import Decimal, ROUND_HALF_UP, ROUND_CEILING, ROUND_FLOOR
from typing import Dict, Any, List, Optional

def calculate_single_price(
    rate: float,
    markup_type: str = "PERCENT",
    markup_value: float = 30.0,
    auto_round: bool = True,
    round_type: str = "nearest",
    round_unit: float = 10.0,
) -> float:
    """
    Tính giá bán từ giá gốc (rate) theo loại markup và quy tắc làm tròn (auto-round).
    Dùng Decimal để tránh sai số dấu phẩy động (float precision).
    Đối với các dịch vụ giá nhỏ (ví dụ 1.3đ/1k tương tác), giữ nguyên số thập phân
    để tuyệt đối không bao giờ làm tròn về 0đ.
    """
    if rate < 0:
        rate = 0.0

    m_type = (markup_type or "PERCENT").upper()
    val = Decimal(str(markup_value or 0.0))
    d_rate = Decimal(str(rate))

    if m_type == "PERCENT":
        raw = d_rate * (Decimal("1") + (val / Decimal("100")))
    else:  # FIXED
        raw = d_rate + val

    if raw <= Decimal("0"):
        return 0.0

    # Nếu làm tròn tự động và giá trị đủ lớn hơn đơn vị làm tròn (>= round_unit)
    if auto_round and round_unit > 0:
        d_unit = Decimal(str(round_unit))
        factor = raw / d_unit
        r_type = (round_type or "nearest").lower()

        if r_type == "ceil":
            rounded_factor = Decimal(math.ceil(float(factor)))
        elif r_type == "floor":
            rounded_factor = Decimal(math.floor(float(factor)))
        else:  # nearest (Half up)
            rounded_factor = factor.quantize(Decimal("1"), rounding=ROUND_HALF_UP)

        final_val = rounded_factor * d_unit
        if final_val > Decimal("0"):
            return float(final_val)

    # Đối với giá trị nhỏ hơn round_unit (VD: 1.3đ, 2.34đ) hoặc khi tắt auto_round:
    # Giữ 2-4 chữ số thập phân, không bao giờ làm tròn thành 0
    if raw < Decimal("1"):
        return float(raw.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
    else:
        return float(raw.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

def calculate_service_prices(
    rate: float,
    user_markup_type: str = "PERCENT",
    user_markup_value: float = 30.0,
    dealer_markup_type: str = "PERCENT",
    dealer_markup_value: float = 15.0,
    auto_round: bool = True,
    round_type: str = "nearest",
    round_unit: float = 10.0,
) -> Dict[str, float]:
    selling_price = calculate_single_price(
        rate=rate,
        markup_type=user_markup_type,
        markup_value=user_markup_value,
        auto_round=auto_round,
        round_type=round_type,
        round_unit=round_unit,
    )

    dealer_price = calculate_single_price(
        rate=rate,
        markup_type=dealer_markup_type,
        markup_value=dealer_markup_value,
        auto_round=auto_round,
        round_type=round_type,
        round_unit=round_unit,
    )

    return {
        "selling_price": selling_price,
        "dealer_price": dealer_price,
    }

def generate_markup_previews(
    items: List[Dict[str, Any]],
    user_markup_type: str = "PERCENT",
    user_markup_value: float = 30.0,
    dealer_markup_type: str = "PERCENT",
    dealer_markup_value: float = 15.0,
    auto_round: bool = True,
    round_type: str = "nearest",
    round_unit: float = 10.0,
) -> List[Dict[str, Any]]:
    previews = []
    for item in items:
        rate = float(item.get("rate", 0.0))
        prices = calculate_service_prices(
            rate=rate,
            user_markup_type=user_markup_type,
            user_markup_value=user_markup_value,
            dealer_markup_type=dealer_markup_type,
            dealer_markup_value=dealer_markup_value,
            auto_round=auto_round,
            round_type=round_type,
            round_unit=round_unit,
        )
        previews.append({
            "service_id": str(item.get("service") or item.get("service_id", "")),
            "name": item.get("name", ""),
            "category": item.get("category", ""),
            "original_rate": rate,
            "user_markup": f"+{user_markup_value}%" if user_markup_type == "PERCENT" else f"+{user_markup_value:,.0f}đ",
            "dealer_markup": f"+{dealer_markup_value}%" if dealer_markup_type == "PERCENT" else f"+{dealer_markup_value:,.0f}đ",
            "selling_price": prices["selling_price"],
            "dealer_price": prices["dealer_price"],
            "min": int(item.get("min", 1)),
            "max": int(item.get("max", 10000)),
            "refill": bool(item.get("refill", False)),
        })
    return previews

def convert_provider_rate_to_vnd(
    raw_rate: float,
    provider_type: str,
    currency: Optional[str] = None,
    usd_rate: float = 28000.0,
    ttc_divider: float = 100.0,
) -> float:
    """
    Quy doi gia goc tu Provider sang gia von VND cho 1.000 don vi dich vu.
    - TuongTacCheo (XU): Gia goc la Xu cho 1 don vi. 1.000 don vi = (raw_rate * 1000) / divider (VND).
    - USD (THMXH, GenericSMM USD): Gia goc la USD cho 1.000 don vi. Quy doi = raw_rate * usd_rate (VND).
    - VND: Gia goc da la VND cho 1.000 don vi. Giu nguyen.
    Tuyet doi khong dung heuristic phong doan gia < 500d de tranh nhan kep hoac nhan nham tien te.
    """
    p_type = (provider_type or "").lower().strip()
    curr = (currency or "").upper().strip()

    if p_type == "tuongtaccheo" or curr == "XU" or "ttc" in p_type:
        div = float(ttc_divider or 100.0)
        eff_div = div if div < 300 else (div / 10.0)
        vnd_cost = round((float(raw_rate) * 1000.0) / eff_div, 2)
        return max(vnd_cost, 100.0)

    if curr == "USD" or p_type == "thmxh":
        rate_val = float(raw_rate or 0.0)
        u_rate = float(usd_rate or 28000.0)
        return round(rate_val * u_rate, 2)

    return round(float(raw_rate or 0.0), 2)
