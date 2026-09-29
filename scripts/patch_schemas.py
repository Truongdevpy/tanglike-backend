with open("backend/app/schemas/all.py", "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update ProviderResponse
old_pr = """    status: str
    balance: float
    last_sync: Optional[datetime]"""

new_pr = """    status: str
    balance: float
    currency: Optional[str] = "USD"
    last_sync: Optional[datetime]"""

content = content.replace(old_pr, new_pr)

# 2. Update OrderResponse
old_or = """    remains: int
    price: float
    status: str"""

new_or = """    remains: int
    price: float
    provider_price: Optional[float] = None
    provider_cost: Optional[float] = None
    profit: Optional[float] = None
    status: str"""

content = content.replace(old_or, new_or)

# 3. Update DashboardStats
old_ds = """class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    total_orders: int
    completed_orders: int
    pending_orders: int
    processing_orders: int
    revenue: float
    profit: float
    open_tickets: int
    user_balance: Optional[float] = None"""

new_ds = """class DashboardStats(BaseModel):
    total_users: int
    active_users: int
    total_orders: int
    completed_orders: int
    pending_orders: int
    processing_orders: int
    revenue: float
    profit: float
    provider_cost: Optional[float] = 0.0
    profit_percent: Optional[float] = 0.0
    markup_percent: Optional[float] = 0.0
    open_tickets: int
    user_balance: Optional[float] = None

# TTC Action Schemas
class TTCBoostRequest(BaseModel):
    service_id: str
    orders: Dict[str, Any]

class TTCMultiStatusRequest(BaseModel):
    order_ids: List[str]

class TTCMultiCancelRequest(BaseModel):
    order_ids: List[str]

class TTCLoginRequest(BaseModel):
    access_token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None"""

content = content.replace(old_ds, new_ds)

with open("backend/app/schemas/all.py", "w", encoding="utf-8") as f:
    f.write(content)

print("Updated backend/app/schemas/all.py successfully!")