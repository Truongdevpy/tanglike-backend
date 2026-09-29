from typing import Optional, List, Any
from sqlalchemy import select, update, delete, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from app.models.all import (
    User, Category, Service, Provider, Order, Transaction, 
    Payment, Notification, SupportConversation, SupportMessage, 
    Coupon, Referral, AuditLog, SystemSetting, SubSite, RefillRequest
)

class UserRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, user_id: int) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.username == username))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_by_username_or_email(self, identifier: str) -> Optional[User]:
        result = await self.db.execute(
            select(User).where((User.username == identifier) | (User.email == identifier))
        )
        return result.scalar_one_or_none()

    async def get_by_referral_code(self, code: str) -> Optional[User]:
        result = await self.db.execute(select(User).where(User.referral_code == code))
        return result.scalar_one_or_none()

    async def list_users(self, skip: int = 0, limit: int = 50, search: Optional[str] = None) -> List[User]:
        stmt = select(User).order_by(desc(User.created_at))
        if search:
            stmt = stmt.where(
                (User.username.ilike(f"%{search}%")) | 
                (User.email.ilike(f"%{search}%")) |
                (User.full_name.ilike(f"%{search}%"))
            )
        stmt = stmt.offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, user: User) -> User:
        self.db.add(user)
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def update(self, user: User) -> User:
        await self.db.commit()
        await self.db.refresh(user)
        return user

class ServiceRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, service_id: int) -> Optional[Service]:
        result = await self.db.execute(select(Service).where(Service.id == service_id))
        return result.scalar_one_or_none()

    async def list_services(
        self, 
        platform: Optional[str] = None, 
        category_id: Optional[int] = None, 
        search: Optional[str] = None,
        only_active: bool = True
    ) -> List[Service]:
        stmt = select(Service).order_by(Service.sort_order, Service.id)
        if only_active:
            stmt = stmt.where(Service.status == "ACTIVE")
        if platform:
            stmt = stmt.where(Service.platform == platform)
        if category_id:
            stmt = stmt.where(Service.category_id == category_id)
        if search:
            stmt = stmt.where(Service.name.ilike(f"%{search}%"))
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, service: Service) -> Service:
        self.db.add(service)
        await self.db.commit()
        await self.db.refresh(service)
        return service

    async def update(self, service: Service) -> Service:
        await self.db.commit()
        await self.db.refresh(service)
        return service

class CategoryRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def list_categories(self, platform: Optional[str] = None) -> List[Category]:
        stmt = select(Category).order_by(Category.sort_order, Category.id)
        if platform:
            stmt = stmt.where(Category.platform == platform)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def get_by_id(self, category_id: int) -> Optional[Category]:
        result = await self.db.execute(select(Category).where(Category.id == category_id))
        return result.scalar_one_or_none()

    async def get_by_slug(self, slug: str) -> Optional[Category]:
        result = await self.db.execute(select(Category).where(Category.slug == slug))
        return result.scalar_one_or_none()

    async def create(self, category: Category) -> Category:
        self.db.add(category)
        await self.db.commit()
        await self.db.refresh(category)
        return category

class OrderRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_id(self, order_id: int) -> Optional[Order]:
        result = await self.db.execute(select(Order).where(Order.id == order_id))
        return result.scalar_one_or_none()

    async def list_orders(
        self, 
        user_id: Optional[int] = None, 
        status: Optional[str] = None, 
        search: Optional[str] = None,
        skip: int = 0, 
        limit: int = 50
    ) -> List[Order]:
        stmt = select(Order).order_by(desc(Order.created_at))
        if user_id is not None:
            stmt = stmt.where(Order.user_id == user_id)
        if status and status != "ALL":
            stmt = stmt.where(Order.status == status)
        if search:
            stmt = stmt.where(
                (Order.link.ilike(f"%{search}%")) |
                (Order.external_order_id.ilike(f"%{search}%"))
            )
        stmt = stmt.offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

    async def create(self, order: Order) -> Order:
        self.db.add(order)
        await self.db.commit()
        await self.db.refresh(order)
        return order

    async def update(self, order: Order) -> Order:
        await self.db.commit()
        await self.db.refresh(order)
        return order

class TransactionRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def create(self, tx: Transaction) -> Transaction:
        self.db.add(tx)
        await self.db.commit()
        await self.db.refresh(tx)
        return tx

    async def list_transactions(self, user_id: Optional[int] = None, skip: int = 0, limit: int = 50) -> List[Transaction]:
        stmt = select(Transaction).order_by(desc(Transaction.created_at))
        if user_id is not None:
            stmt = stmt.where(Transaction.user_id == user_id)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())

class PaymentRepository:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_by_code(self, code: str) -> Optional[Payment]:
        result = await self.db.execute(select(Payment).where(Payment.transaction_code == code))
        return result.scalar_one_or_none()

    async def create(self, payment: Payment) -> Payment:
        self.db.add(payment)
        await self.db.commit()
        await self.db.refresh(payment)
        return payment

    async def update(self, payment: Payment) -> Payment:
        await self.db.commit()
        await self.db.refresh(payment)
        return payment

    async def list_payments(self, user_id: Optional[int] = None, skip: int = 0, limit: int = 50) -> List[Payment]:
        stmt = select(Payment).order_by(desc(Payment.created_at))
        if user_id is not None:
            stmt = stmt.where(Payment.user_id == user_id)
        stmt = stmt.offset(skip).limit(limit)
        result = await self.db.execute(stmt)
        return list(result.scalars().all())
