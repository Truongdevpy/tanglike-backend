import uuid
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app
from app.models.all import User, SupportConversation, SupportMessage
from app.auth.security import create_access_token
from app.database.session import AsyncSessionLocal
from sqlalchemy import select

@pytest.mark.asyncio
async def test_ticket_bulk_actions_and_delete_all():
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        # Create an admin user
        admin = User(
            username=f"admin_bulk_{suffix}",
            email=f"admin_bulk_{suffix}@example.com",
            password_hash="fakehash",
            role="ADMIN",
            referral_code=f"ADM_{suffix}",
            token_version=1
        )
        # Create a regular user
        normal_user = User(
            username=f"user_bulk_{suffix}",
            email=f"user_bulk_{suffix}@example.com",
            password_hash="fakehash",
            role="USER",
            referral_code=f"USR_{suffix}",
            token_version=1
        )
        db.add_all([admin, normal_user])
        await db.commit()
        await db.refresh(admin)
        await db.refresh(normal_user)

        # Create 4 test conversations with messages
        convs = []
        for i in range(1, 5):
            c = SupportConversation(
                user_id=normal_user.id,
                subject=f"Ticket #{i} {suffix}",
                status="OPEN",
                customer_type="REGULAR",
                product_status="PENDING"
            )
            db.add(c)
            convs.append(c)
        await db.commit()
        for c in convs:
            await db.refresh(c)
            # Add messages
            m1 = SupportMessage(
                conversation_id=c.id,
                sender_id=normal_user.id,
                sender_name="Customer",
                sender_role="USER",
                message=f"Message in ticket {c.id}"
            )
            m2 = SupportMessage(
                conversation_id=c.id,
                sender_id=admin.id,
                sender_name="Admin",
                sender_role="ADMIN",
                message=f"Reply in ticket {c.id}"
            )
            db.add_all([m1, m2])
        await db.commit()

        admin_token = create_access_token({"sub": str(admin.id), "role": admin.role, "tv": admin.token_version})
        user_token = create_access_token({"sub": str(normal_user.id), "role": normal_user.role, "tv": normal_user.token_version})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        user_headers = {"Authorization": f"Bearer {user_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Non-admin should be forbidden (403)
        res_unauth = await client.post(
            "/api/v1/admin/tickets/bulk-delete",
            json={"ticket_ids": [convs[0].id]},
            headers=user_headers
        )
        assert res_unauth.status_code == 403

        # 2. Bulk status update for ticket 1 and 2
        res_status = await client.post(
            "/api/v1/admin/tickets/bulk-status",
            json={"ticket_ids": [convs[0].id, convs[1].id], "status": "RESOLVED", "is_hidden": True},
            headers=admin_headers
        )
        assert res_status.status_code == 200

        # 3. Bulk delete tickets 1 and 2
        res_bulk_del = await client.post(
            "/api/v1/admin/tickets/bulk-delete",
            json={"ticket_ids": [convs[0].id, convs[1].id]},
            headers=admin_headers
        )
        assert res_bulk_del.status_code == 200
        assert res_bulk_del.json()["data"] == 2

        # Verify in DB that conv 1 & 2 and their messages are deleted
        async with AsyncSessionLocal() as db:
            c_res = await db.execute(select(SupportConversation).where(SupportConversation.id.in_([convs[0].id, convs[1].id])))
            assert len(c_res.scalars().all()) == 0
            m_res = await db.execute(select(SupportMessage).where(SupportMessage.conversation_id.in_([convs[0].id, convs[1].id])))
            assert len(m_res.scalars().all()) == 0

            # Verify conv 3 & 4 still exist
            c_res2 = await db.execute(select(SupportConversation).where(SupportConversation.id.in_([convs[2].id, convs[3].id])))
            assert len(c_res2.scalars().all()) == 2

        # 4. Delete-all: should wipe all remaining support conversations
        res_del_all = await client.post(
            "/api/v1/admin/tickets/delete-all",
            headers=admin_headers
        )
        assert res_del_all.status_code == 200
        assert res_del_all.json()["success"] is True

        async with AsyncSessionLocal() as db:
            c_all = await db.execute(select(SupportConversation))
            assert len(c_all.scalars().all()) == 0
            m_all = await db.execute(select(SupportMessage))
            assert len(m_all.scalars().all()) == 0


@pytest.mark.asyncio
async def test_message_bulk_actions_and_clear_conversation():
    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as db:
        admin = User(
            username=f"admin_msg_{suffix}",
            email=f"admin_msg_{suffix}@example.com",
            password_hash="fakehash",
            role="ADMIN",
            referral_code=f"ADMM_{suffix}",
            token_version=1
        )
        normal_user = User(
            username=f"user_msg_{suffix}",
            email=f"user_msg_{suffix}@example.com",
            password_hash="fakehash",
            role="USER",
            referral_code=f"USRM_{suffix}",
            token_version=1
        )
        db.add_all([admin, normal_user])
        await db.commit()
        await db.refresh(admin)
        await db.refresh(normal_user)

        conv = SupportConversation(
            user_id=normal_user.id,
            subject=f"Ticket Msg Test {suffix}",
            status="OPEN",
            customer_type="VIP",
            product_status="PROCESSING"
        )
        db.add(conv)
        await db.commit()
        await db.refresh(conv)

        # Create 5 messages
        msgs = []
        for i in range(1, 6):
            m = SupportMessage(
                conversation_id=conv.id,
                sender_id=normal_user.id if i % 2 == 1 else admin.id,
                sender_name="User" if i % 2 == 1 else "Admin",
                sender_role="USER" if i % 2 == 1 else "ADMIN",
                message=f"Message #{i} content"
            )
            db.add(m)
            msgs.append(m)
        await db.commit()
        for m in msgs:
            await db.refresh(m)

        admin_token = create_access_token({"sub": str(admin.id), "role": admin.role, "tv": admin.token_version})
        user_token = create_access_token({"sub": str(normal_user.id), "role": normal_user.role, "tv": normal_user.token_version})
        admin_headers = {"Authorization": f"Bearer {admin_token}"}
        user_headers = {"Authorization": f"Bearer {user_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. Non-admin forbidden from bulk delete messages
        res_unauth = await client.post(
            "/api/v1/admin/tickets/messages/bulk-delete",
            json={"message_ids": [msgs[0].id]},
            headers=user_headers
        )
        assert res_unauth.status_code == 403

        # 2. Non-admin forbidden from clearing conversation
        res_clear_unauth = await client.post(
            f"/api/v1/admin/tickets/{conv.id}/clear-messages",
            headers=user_headers
        )
        assert res_clear_unauth.status_code == 403

        # 3. Bulk delete messages 1 and 2
        res_bulk_del = await client.post(
            "/api/v1/admin/tickets/messages/bulk-delete",
            json={"message_ids": [msgs[0].id, msgs[1].id]},
            headers=admin_headers
        )
        assert res_bulk_del.status_code == 200
        assert res_bulk_del.json()["data"] == 2

        # Verify messages 1 & 2 are gone, 3, 4, 5 still exist
        async with AsyncSessionLocal() as db:
            m_res = await db.execute(select(SupportMessage).where(SupportMessage.id.in_([msgs[0].id, msgs[1].id])))
            assert len(m_res.scalars().all()) == 0
            m_remain = await db.execute(select(SupportMessage).where(SupportMessage.conversation_id == conv.id))
            assert len(m_remain.scalars().all()) == 3

        # 4. Clear all remaining messages in this conversation
        res_clear = await client.post(
            f"/api/v1/admin/tickets/{conv.id}/clear-messages",
            headers=admin_headers
        )
        assert res_clear.status_code == 200
        assert res_clear.json()["data"] == 3

        # Verify conversation still exists, but messages are 0
        async with AsyncSessionLocal() as db:
            c_check = await db.execute(select(SupportConversation).where(SupportConversation.id == conv.id))
            assert c_check.scalar_one_or_none() is not None
            m_none = await db.execute(select(SupportMessage).where(SupportMessage.conversation_id == conv.id))
            assert len(m_none.scalars().all()) == 0
