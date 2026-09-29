import sys

with open("backend/app/models/all.py", "r", encoding="utf-8") as f:
    c = f.read()

c = c.replace(
    '    services = relationship("Service", back_populates="provider")',
    '    services = relationship("Service", back_populates="provider")\n    mappings = relationship("ProviderServiceMapping", back_populates="provider", cascade="all, delete-orphan")\n    price_logs = relationship("PriceSyncLog", back_populates="provider", cascade="all, delete-orphan")'
)

c = c.replace(
    '    price = Column(Float, nullable=False)  # Customer price per 1000\n    provider_price = Column(Float, default=0.0, nullable=False)',
    '    price = Column(Float, nullable=False)  # Customer price per 1000\n    dealer_price = Column(Float, default=0.0, nullable=False)  # Reseller price per 1000\n    provider_price = Column(Float, default=0.0, nullable=False)'
)

c = c.replace(
    '    category = relationship("Category", back_populates="services")\n    provider = relationship("Provider", back_populates="services")\n    orders = relationship("Order", back_populates="service")',
    '    category = relationship("Category", back_populates="services")\n    provider = relationship("Provider", back_populates="services")\n    orders = relationship("Order", back_populates="service")\n    mapping = relationship("ProviderServiceMapping", back_populates="service", uselist=False, cascade="all, delete-orphan")\n    price_logs = relationship("PriceSyncLog", back_populates="service", cascade="all, delete-orphan")'
)

mapping_cls = """

class ProviderServiceMapping(Base):
    __tablename__ = "provider_service_mappings"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), index=True, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), index=True, nullable=True)
    external_service_id = Column(String(100), index=True, nullable=False)
    external_name = Column(String(255), nullable=True)
    external_category = Column(String(255), nullable=True)
    original_rate = Column(Float, default=0.0, nullable=False)

    markup_type = Column(String(20), default="PERCENT", nullable=False)  # PERCENT or FIXED
    markup_value = Column(Float, default=30.0, nullable=False)
    dealer_markup_type = Column(String(20), default="PERCENT", nullable=False)
    dealer_markup_value = Column(Float, default=15.0, nullable=False)

    auto_round = Column(Boolean, default=True, nullable=False)
    round_type = Column(String(20), default="nearest", nullable=False)  # nearest, ceil, floor
    round_unit = Column(Float, default=10.0, nullable=False)  # 10, 50, 100, 1000

    selling_price = Column(Float, default=0.0, nullable=False)
    dealer_price = Column(Float, default=0.0, nullable=False)

    min_quantity = Column(Integer, default=50, nullable=False)
    max_quantity = Column(Integer, default=10000, nullable=False)
    refill_enabled = Column(Boolean, default=False, nullable=False)
    cancel_enabled = Column(Boolean, default=False, nullable=False)
    sync_status = Column(String(30), default="ACTIVE", nullable=False)  # ACTIVE, DISABLED, PRICE_CHANGED
    last_synced_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    provider = relationship("Provider", back_populates="mappings")
    service = relationship("Service", back_populates="mapping")


class PriceSyncLog(Base):
    __tablename__ = "price_sync_logs"

    id = Column(Integer, primary_key=True, index=True)
    provider_id = Column(Integer, ForeignKey("providers.id"), index=True, nullable=False)
    service_id = Column(Integer, ForeignKey("services.id"), index=True, nullable=True)
    external_service_id = Column(String(100), index=True, nullable=False)
    service_name = Column(String(255), nullable=False)
    old_rate = Column(Float, default=0.0, nullable=False)
    new_rate = Column(Float, default=0.0, nullable=False)
    old_price = Column(Float, default=0.0, nullable=False)
    new_price = Column(Float, default=0.0, nullable=False)
    change_percent = Column(Float, default=0.0, nullable=False)
    alert_triggered = Column(Boolean, default=False, nullable=False)
    status = Column(String(50), default="UPDATED", nullable=False)  # UPDATED, WARNING_PRICE_SPIKE, DISABLED, UNCHANGED
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, index=True, nullable=False)

    provider = relationship("Provider", back_populates="price_logs")
    service = relationship("Service", back_populates="price_logs")
"""

if "class ProviderServiceMapping(Base):" not in c:
    c += mapping_cls

with open("backend/app/models/all.py", "w", encoding="utf-8") as f:
    f.write(c)

with open("backend/app/models/__init__.py", "r", encoding="utf-8") as f:
    init_c = f.read()

if "ProviderServiceMapping" not in init_c:
    init_c = init_c.replace(
        "    RefillRequest,\n)",
        "    RefillRequest,\n    ProviderServiceMapping,\n    PriceSyncLog,\n)"
    )
    init_c = init_c.replace(
        '    "RefillRequest",\n]',
        '    "RefillRequest",\n    "ProviderServiceMapping",\n    "PriceSyncLog",\n]'
    )
    with open("backend/app/models/__init__.py", "w", encoding="utf-8") as f:
        f.write(init_c)

print("Models updated successfully")
