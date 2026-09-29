with open("backend/app/services/order_service.py", "r", encoding="utf-8") as f:
    text = f.read()

old_calc = """        # 3. Calculate price
        base_price = (quantity / 1000.0) * service.price"""

new_calc = """        # 3. Calculate price
        # Check dealer/reseller tier pricing
        is_dealer = getattr(user, "role", "").upper() in ["RESELLER", "DEALER"]
        effective_rate = service.dealer_price if (is_dealer and getattr(service, "dealer_price", 0) > 0) else service.price
        base_price = (quantity / 1000.0) * effective_rate"""

if old_calc in text:
    text = text.replace(old_calc, new_calc, 1)
    with open("backend/app/services/order_service.py", "w", encoding="utf-8") as f:
        f.write(text)
    print("Updated order_service.py with dealer pricing successfully!")
else:
    print("Could not find old_calc in order_service.py")
