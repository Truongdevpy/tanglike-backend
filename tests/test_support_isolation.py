import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
import sys
import os

sys.path.insert(0, os.path.abspath('backend'))

from app.main import app
from app.database.session import engine, Base

@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"

@pytest_asyncio.fixture(scope="session", autouse=True)
async def setup_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from app.main import seed_initial_data
    await seed_initial_data()
    yield

async def get_or_create_user(ac: AsyncClient, username: str, email: str, password: str = "Password123!"):
    login_res = await ac.post("/api/v1/auth/login", json={"username": username, "password": password})
    if login_res.status_code == 200:
        return login_res.json()["data"]["access_token"]
    reg_res = await ac.post("/api/v1/auth/register", json={
        "username": username,
        "email": email,
        "password": password,
        "confirm_password": password,
        "full_name": username.title()
    })
    assert reg_res.status_code == 200, f"Registration failed: {reg_res.text}"
    return reg_res.json()["data"]["access_token"]

@pytest.mark.asyncio
async def test_support_ticket_and_message_isolation_between_users():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a = await get_or_create_user(ac, "user_alice", "alice@example.com")
        token_b = await get_or_create_user(ac, "user_bob", "bob@example.com")

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 1. User A creates a ticket with confidential information
        ticket_res_a = await ac.post("/api/v1/support/conversations", json={
            "subject": "Private Ticket From Alice",
            "initial_message": "Confidential Alice Secret 12345"
        }, headers=headers_a)
        assert ticket_res_a.status_code == 200
        alice_conv_id = ticket_res_a.json()["data"]["id"]

        # 2. User B lists conversations -> Alice's ticket MUST NOT appear
        list_b = await ac.get("/api/v1/support/conversations", headers=headers_b)
        assert list_b.status_code == 200
        conv_ids_b = [c["id"] for c in list_b.json()["data"]]
        assert alice_conv_id not in conv_ids_b

        # 3. User B tries to directly read Alice's messages -> MUST BE 403 FORBIDDEN
        read_b = await ac.get(f"/api/v1/support/conversations/{alice_conv_id}/messages", headers=headers_b)
        assert read_b.status_code == 403

        # 4. User B tries to inject/send a message into Alice's ticket -> MUST BE 403 FORBIDDEN
        send_b = await ac.post(f"/api/v1/support/conversations/{alice_conv_id}/messages", json={
            "message": "Bob trying to hack Alice ticket"
        }, headers=headers_b)
        assert send_b.status_code == 403

@pytest.mark.asyncio
async def test_live_chat_isolation_between_users():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a = await get_or_create_user(ac, "user_alice", "alice@example.com")
        token_b = await get_or_create_user(ac, "user_bob", "bob@example.com")

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 1. User A sends live chat message
        chat_a = await ac.post("/api/v1/support/live-chat", json={
            "message": "Alice secret inquiry to admin: How to deposit 10M?"
        }, headers=headers_a)
        assert chat_a.status_code == 200

        # 2. User A retrieves live chat messages -> Should contain Alice's message
        msgs_a = await ac.get("/api/v1/support/live-chat/messages", headers=headers_a)
        assert msgs_a.status_code == 200
        alice_texts = [m["message"] for m in msgs_a.json()["data"]]
        assert any("Alice secret inquiry" in t for t in alice_texts)

        # 3. User B retrieves live chat messages -> MUST NOT contain Alice's message
        msgs_b = await ac.get("/api/v1/support/live-chat/messages", headers=headers_b)
        assert msgs_b.status_code == 200
        bob_texts = [m["message"] for m in msgs_b.json()["data"]]
        assert not any("Alice secret inquiry" in t for t in bob_texts)

        # 4. User B sends their own live chat message
        chat_b = await ac.post("/api/v1/support/live-chat", json={
            "message": "Bob secret inquiry to admin: Can I get discount?"
        }, headers=headers_b)
        assert chat_b.status_code == 200

        # 5. User B re-checks -> Sees Bob's message only
        msgs_b_after = await ac.get("/api/v1/support/live-chat/messages", headers=headers_b)
        assert msgs_b_after.status_code == 200
        bob_texts_after = [m["message"] for m in msgs_b_after.json()["data"]]
        assert any("Bob secret inquiry" in t for t in bob_texts_after)
        assert not any("Alice secret inquiry" in t for t in bob_texts_after)

@pytest.mark.asyncio
async def test_admin_oversight_and_cross_user_isolation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a = await get_or_create_user(ac, "user_alice", "alice@example.com")
        token_b = await get_or_create_user(ac, "user_bob", "bob@example.com")
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        # 1. Alice creates a ticket
        ticket_res = await ac.post("/api/v1/support/conversations", json={
            "subject": "Alice Help Needed",
            "initial_message": "Need help with my account"
        }, headers=headers_a)
        conv_id = ticket_res.json()["data"]["id"]

        # 2. Admin views tickets in admin panel -> Sees Alice's ticket
        admin_list = await ac.get("/api/v1/admin/tickets", headers=headers_admin)
        assert admin_list.status_code == 200
        assert any(t["id"] == conv_id for t in admin_list.json()["data"])

        # 3. Admin replies to Alice's ticket
        reply_res = await ac.post(f"/api/v1/support/conversations/{conv_id}/messages", json={
            "message": "Admin reply: We have checked your account and fixed it."
        }, headers=headers_admin)
        assert reply_res.status_code == 200

        # 4. Alice sees Admin reply
        alice_msgs = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_a)
        assert alice_msgs.status_code == 200
        msg_texts = [m["message"] for m in alice_msgs.json()["data"]]
        assert any("Admin reply: We have checked your account" in t for t in msg_texts)

        # 5. Bob tries to access this ticket -> STILL 403 Forbidden!
        bob_attempt = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_b)
        assert bob_attempt.status_code == 403

@pytest.mark.asyncio
async def test_guest_chat_and_admin_status_tagging():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        headers_admin = {"Authorization": f"Bearer {admin_token}"}

        # 1. Guest sends live chat message
        guest_res = await ac.post("/api/v1/support/live-chat", json={
            "message": "Toi la khach vang lai can tu van mua sub TikTok",
            "sender_name": "Nguyen Khach",
            "email": "khach@gmail.com"
        })
        assert guest_res.status_code == 200
        conv_id = guest_res.json()["data"]["conversation_id"]
        assert conv_id is not None

        # 2. Admin sees guest conversation in tickets
        admin_tickets = await ac.get("/api/v1/admin/tickets", headers=headers_admin)
        assert admin_tickets.status_code == 200
        found = next((t for t in admin_tickets.json()["data"] if t["id"] == conv_id), None)
        assert found is not None
        assert "Nguyen Khach" in (found["user_name"] or found["guest_name"] or "")

        # 3. Admin updates customer_type to VIP, product_status to PROCESSING, and adds tags
        update_res = await ac.put(f"/api/v1/admin/tickets/{conv_id}/status", json={
            "status": "WAITING",
            "customer_type": "VIP",
            "product_status": "PROCESSING",
            "tags": "tiktok,sub,vip_deal"
        }, headers=headers_admin)
        assert update_res.status_code == 200

        # 4. Filter by customer_type=VIP
        vip_list = await ac.get("/api/v1/admin/tickets?customer_type=VIP", headers=headers_admin)
        assert vip_list.status_code == 200
        assert any(t["id"] == conv_id for t in vip_list.json()["data"])

        # 5. Filter by product_status=PROCESSING
        proc_list = await ac.get("/api/v1/admin/tickets?product_status=PROCESSING", headers=headers_admin)
        assert proc_list.status_code == 200
        assert any(t["id"] == conv_id for t in proc_list.json()["data"])


@pytest.mark.asyncio
async def test_guest_session_isolation_between_guests():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        sid_1 = "guest_session_token_aaa_111"
        sid_2 = "guest_session_token_bbb_222"

        # 1. Guest 1 sends a secret message with sid_1
        g1_send = await ac.post("/api/v1/support/live-chat", json={
            "message": "Guest 1 secret message: order #9999",
            "sender_name": "Khách hàng",
            "guest_session_id": sid_1
        })
        assert g1_send.status_code == 200

        # 2. Guest 1 retrieves their messages using sid_1 -> gets the message
        g1_read = await ac.get(f"/api/v1/support/live-chat/messages?guest_session_id={sid_1}")
        assert g1_read.status_code == 200
        g1_msgs = [m["message"] for m in g1_read.json()["data"]]
        assert any("Guest 1 secret message" in t for t in g1_msgs)

        # 3. Guest 2 with a different sid_2 retrieves messages -> MUST NOT see Guest 1's messages
        g2_read = await ac.get(f"/api/v1/support/live-chat/messages?guest_session_id={sid_2}")
        assert g2_read.status_code == 200
        assert len(g2_read.json()["data"]) == 0

        # 4. Anonymous request with no session ID -> MUST NOT see any messages
        anon_read = await ac.get("/api/v1/support/live-chat/messages")
        assert anon_read.status_code == 200
        assert len(anon_read.json()["data"]) == 0


@pytest.mark.asyncio
async def test_admin_hide_and_delete_tickets_and_messages():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        admin_login = await ac.post("/api/v1/auth/login", json={"username": "admin", "password": "admin123456"})
        admin_token = admin_login.json()["data"]["access_token"]
        user_token = await get_or_create_user(ac, "client_hidedel", "client_hidedel@tanglike.com")

        headers_admin = {"Authorization": f"Bearer {admin_token}"}
        headers_user = {"Authorization": f"Bearer {user_token}"}

        # 1. User creates a conversation
        conv_res = await ac.post("/api/v1/support/conversations", json={
            "subject": "Ticket for testing hide and delete",
            "initial_message": "First message in ticket"
        }, headers=headers_user)
        assert conv_res.status_code == 200
        conv_id = conv_res.json()["data"]["id"]

        # 2. Get the message created
        user_msgs_res = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_user)
        assert user_msgs_res.status_code == 200
        msgs = user_msgs_res.json()["data"]
        assert len(msgs) >= 1
        msg_id = msgs[0]["id"]

        # 3. Admin hides the message
        hide_msg_res = await ac.put(f"/api/v1/admin/tickets/messages/{msg_id}/toggle-hide", headers=headers_admin)
        assert hide_msg_res.status_code == 200
        assert hide_msg_res.json()["data"] is True

        # 4. User should NOT see the hidden message
        user_msgs_after_hide = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_user)
        assert user_msgs_after_hide.status_code == 200
        assert not any(m["id"] == msg_id for m in user_msgs_after_hide.json()["data"])

        # 5. Admin STILL sees the message, marked as is_hidden
        admin_msgs_res = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_admin)
        assert admin_msgs_res.status_code == 200
        hidden_msg = next((m for m in admin_msgs_res.json()["data"] if m["id"] == msg_id), None)
        assert hidden_msg is not None
        assert hidden_msg["is_hidden"] is True

        # 6. Admin toggles unhide
        unhide_msg_res = await ac.put(f"/api/v1/admin/tickets/messages/{msg_id}/toggle-hide", headers=headers_admin)
        assert unhide_msg_res.status_code == 200
        assert unhide_msg_res.json()["data"] is False

        # 7. User now sees the message again
        user_msgs_after_unhide = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_user)
        assert user_msgs_after_unhide.status_code == 200
        assert any(m["id"] == msg_id for m in user_msgs_after_unhide.json()["data"])

        # 8. Admin hides the conversation
        hide_conv_res = await ac.put(f"/api/v1/admin/tickets/{conv_id}/toggle-hide", headers=headers_admin)
        assert hide_conv_res.status_code == 200
        assert hide_conv_res.json()["data"] is True

        # 9. User should NOT see the hidden conversation in their list
        user_convs = await ac.get("/api/v1/support/conversations", headers=headers_user)
        assert user_convs.status_code == 200
        assert not any(c["id"] == conv_id for c in user_convs.json()["data"])

        # 10. Admin can filter by status=HIDDEN and find the ticket
        admin_hidden_list = await ac.get("/api/v1/admin/tickets?status=HIDDEN", headers=headers_admin)
        assert admin_hidden_list.status_code == 200
        assert any(c["id"] == conv_id for c in admin_hidden_list.json()["data"])

        # 11. Admin deletes the message
        del_msg_res = await ac.delete(f"/api/v1/admin/tickets/messages/{msg_id}", headers=headers_admin)
        assert del_msg_res.status_code == 200

        # Verify message is gone
        admin_msgs_after_del = await ac.get(f"/api/v1/support/conversations/{conv_id}/messages", headers=headers_admin)
        assert admin_msgs_after_del.status_code == 200
        assert not any(m["id"] == msg_id for m in admin_msgs_after_del.json()["data"])

        # 12. Admin deletes the conversation
        del_conv_res = await ac.delete(f"/api/v1/admin/tickets/{conv_id}", headers=headers_admin)
        assert del_conv_res.status_code == 200

        # Verify conversation is gone
        admin_all_after_del = await ac.get("/api/v1/admin/tickets?status=ALL", headers=headers_admin)
        assert admin_all_after_del.status_code == 200
        assert not any(c["id"] == conv_id for c in admin_all_after_del.json()["data"])


@pytest.mark.asyncio
async def test_live_chat_clear_messages_and_bulk_delete_with_isolation():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        token_a = await get_or_create_user(ac, "user_alice", "alice@example.com")
        token_b = await get_or_create_user(ac, "user_bob", "bob@example.com")

        headers_a = {"Authorization": f"Bearer {token_a}"}
        headers_b = {"Authorization": f"Bearer {token_b}"}

        # 1. Alice sends 2 messages
        await ac.post("/api/v1/support/live-chat", json={"message": "Alice msg 1"}, headers=headers_a)
        await ac.post("/api/v1/support/live-chat", json={"message": "Alice msg 2"}, headers=headers_a)

        # 2. Bob sends 1 message
        await ac.post("/api/v1/support/live-chat", json={"message": "Bob msg 1"}, headers=headers_b)

        # 3. Get messages for Alice and Bob
        a_res = await ac.get("/api/v1/support/live-chat/messages", headers=headers_a)
        b_res = await ac.get("/api/v1/support/live-chat/messages", headers=headers_b)
        assert a_res.status_code == 200
        assert b_res.status_code == 200
        a_msgs = a_res.json()["data"]
        b_msgs = b_res.json()["data"]
        assert len(a_msgs) >= 2
        assert len(b_msgs) >= 1

        a_msg_id = a_msgs[0]["id"]
        b_msg_id = b_msgs[0]["id"]

        # 4. Alice attempts to delete Bob's message (IDOR attack) -> must NOT delete Bob's message
        del_attempt = await ac.post("/api/v1/support/live-chat/messages/bulk-delete", json={"message_ids": [b_msg_id]}, headers=headers_a)
        assert del_attempt.status_code == 200
        assert del_attempt.json()["data"]["deleted_count"] == 0

        # Verify Bob still has his message
        b_check = await ac.get("/api/v1/support/live-chat/messages", headers=headers_b)
        assert any(m["id"] == b_msg_id for m in b_check.json()["data"])

        # 5. Alice deletes her own message -> success
        del_own = await ac.post("/api/v1/support/live-chat/messages/bulk-delete", json={"message_ids": [a_msg_id]}, headers=headers_a)
        assert del_own.status_code == 200
        assert del_own.json()["data"]["deleted_count"] == 1

        # 6. Alice clears all her messages -> Bob's message is still intact
        clear_res = await ac.post("/api/v1/support/live-chat/clear-messages", headers=headers_a)
        assert clear_res.status_code == 200

        # Alice has no messages left
        a_empty = await ac.get("/api/v1/support/live-chat/messages", headers=headers_a)
        assert len(a_empty.json()["data"]) == 0

        # Bob STILL has his message!
        b_still = await ac.get("/api/v1/support/live-chat/messages", headers=headers_b)
        assert len(b_still.json()["data"]) >= 1
