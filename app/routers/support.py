import uuid
import re
from app.auth.security import decode_token
from datetime import datetime
from typing import List, Dict, Optional
from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect, status, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from pydantic import BaseModel

from app.database.session import get_db, AsyncSessionLocal
from app.models.all import User, SupportConversation, SupportMessage
from app.schemas.all import (
    ApiResponse, SupportConversationCreate, SupportMessageCreate,
    SupportConversationResponse, SupportMessageResponse
)
from app.auth.security import get_current_user, get_optional_current_user
from app.notifications.telegram import TelegramNotifier

router = APIRouter(prefix="/support", tags=["Support & Live Chat"])

class ConnectionManager:
    def __init__(self):
        # map conversation_id -> list of WebSockets
        self.active_connections: Dict[int, List[WebSocket]] = {}

    async def connect(self, conversation_id: int, websocket: WebSocket):
        await websocket.accept()
        if conversation_id not in self.active_connections:
            self.active_connections[conversation_id] = []
        self.active_connections[conversation_id].append(websocket)

    def disconnect(self, conversation_id: int, websocket: WebSocket):
        if conversation_id in self.active_connections:
            if websocket in self.active_connections[conversation_id]:
                self.active_connections[conversation_id].remove(websocket)
            if not self.active_connections[conversation_id]:
                del self.active_connections[conversation_id]

    async def broadcast(self, conversation_id: int, message_dict: dict):
        if conversation_id in self.active_connections:
            for connection in self.active_connections[conversation_id]:
                try:
                    await connection.send_json(message_dict)
                except Exception:
                    pass

ws_manager = ConnectionManager()

@router.get("/conversations", response_model=ApiResponse[List[SupportConversationResponse]])
async def list_conversations(
    all_tickets: bool = Query(False, alias="all"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    stmt = select(SupportConversation).order_by(desc(SupportConversation.updated_at))
    # Strict isolation: regular users can NEVER view other customers' conversations
    if not (all_tickets and current_user.role in ["ADMIN", "SUPPORT"]):
        stmt = stmt.where(
            SupportConversation.user_id == current_user.id,
            (SupportConversation.is_hidden == False) | (SupportConversation.is_hidden == None),
            SupportConversation.status != "HIDDEN"
        )
    
    res = await db.execute(stmt)
    convs = res.scalars().all()
    
    output = []
    for c in convs:
        # Get last message
        m_stmt = select(SupportMessage).where(SupportMessage.conversation_id == c.id).order_by(desc(SupportMessage.created_at)).limit(1)
        m_res = await db.execute(m_stmt)
        last_m = m_res.scalar_one_or_none()
        
        # User details
        user_obj = None
        if c.user_id:
            u_res = await db.execute(select(User).where(User.id == c.user_id))
            user_obj = u_res.scalar_one_or_none()
        
        output.append(SupportConversationResponse(
            id=c.id,
            user_id=c.user_id,
            username=user_obj.username if user_obj else c.guest_name,
            user_name=user_obj.full_name or user_obj.username if user_obj else c.guest_name,
            subject=c.subject,
            status=c.status,
            assigned_admin=c.assigned_admin,
            created_at=c.created_at,
            updated_at=c.updated_at,
            last_message=last_m.message if last_m else None
        ))
    return ApiResponse(data=output)

@router.post("/conversations", response_model=ApiResponse[SupportConversationResponse])
async def create_conversation(
    payload: SupportConversationCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    conv = SupportConversation(
        user_id=current_user.id,
        subject=payload.subject.strip(),
        status="OPEN",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    db.add(conv)
    await db.flush()

    msg = SupportMessage(
        conversation_id=conv.id,
        sender_id=current_user.id,
        sender_name=current_user.full_name or current_user.username,
        sender_role=current_user.role,
        message=payload.initial_message.strip(),
        created_at=datetime.utcnow()
    )
    db.add(msg)
    await db.commit()
    await db.refresh(conv)

    await TelegramNotifier.notify_new_ticket(current_user.username, conv.subject)

    return ApiResponse(
        data=SupportConversationResponse(
            id=conv.id,
            user_id=conv.user_id,
            username=current_user.username,
            subject=conv.subject,
            status=conv.status,
            assigned_admin=conv.assigned_admin,
            created_at=conv.created_at,
            updated_at=conv.updated_at,
            last_message=msg.message
        ),
        message="Tạo ticket hỗ trợ thành công."
    )

@router.get("/conversations/{id}/messages", response_model=ApiResponse[List[SupportMessageResponse]])
async def get_messages(
    id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    c_res = await db.execute(select(SupportConversation).where(SupportConversation.id == id))
    conv = c_res.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại.")
    # Strict isolation: only creator or Admin/Support can read conversation messages
    if current_user.role not in ["ADMIN", "SUPPORT"] and conv.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền truy cập ticket này.")

    stmt = select(SupportMessage).where(SupportMessage.conversation_id == id)
    if current_user.role not in ["ADMIN", "SUPPORT"]:
        stmt = stmt.where((SupportMessage.is_hidden == False) | (SupportMessage.is_hidden == None))
    stmt = stmt.order_by(SupportMessage.created_at)
    res = await db.execute(stmt)
    messages = res.scalars().all()
    return ApiResponse(data=[SupportMessageResponse.model_validate(m) for m in messages])

@router.post("/conversations/{id}/messages", response_model=ApiResponse[SupportMessageResponse])
async def send_message(
    id: int,
    payload: SupportMessageCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    c_res = await db.execute(select(SupportConversation).where(SupportConversation.id == id))
    conv = c_res.scalar_one_or_none()
    if not conv:
        raise HTTPException(status_code=404, detail="Cuộc trò chuyện không tồn tại.")
    # Strict isolation: only creator or Admin/Support can post messages
    if current_user.role not in ["ADMIN", "SUPPORT"] and conv.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Không có quyền truy cập ticket này.")

    msg = SupportMessage(
        conversation_id=id,
        sender_id=current_user.id,
        sender_name=current_user.full_name or current_user.username,
        sender_role=current_user.role,
        message=payload.message.strip(),
        attachment=payload.attachment,
        created_at=datetime.utcnow()
    )
    db.add(msg)
    conv.updated_at = datetime.utcnow()
    if current_user.role in ["ADMIN", "SUPPORT"]:
        conv.status = "WAITING"
    else:
        conv.status = "OPEN"
    await db.commit()
    await db.refresh(msg)

    # Broadcast message via WebSocket
    msg_data = {
        "id": msg.id,
        "conversation_id": msg.conversation_id,
        "sender_id": msg.sender_id,
        "sender_name": msg.sender_name,
        "sender_role": msg.sender_role,
        "message": msg.message,
        "attachment": msg.attachment,
        "created_at": msg.created_at.isoformat()
    }
    await ws_manager.broadcast(id, msg_data)

    return ApiResponse(data=SupportMessageResponse.model_validate(msg))

@router.websocket("/ws/{conversation_id}")
async def websocket_support_chat(websocket: WebSocket, conversation_id: int):
    # Authenticate WebSocket via query parameter or header
    token = websocket.query_params.get("token")
    if not token:
        auth_header = websocket.headers.get("authorization")
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header[7:]

    if not token:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    try:
        payload = decode_token(token, is_refresh=False)
        user_id = int(payload.get("sub", 0))
    except Exception:
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return

    async with AsyncSessionLocal() as db:
        u_res = await db.execute(select(User).where(User.id == user_id, User.status == "ACTIVE", User.is_deleted == False))
        current_user = u_res.scalar_one_or_none()
        if not current_user or payload.get("tv") is None or payload.get("tv") != current_user.token_version:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        c_res = await db.execute(select(SupportConversation).where(SupportConversation.id == conversation_id))
        conv = c_res.scalar_one_or_none()
        if not conv:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

        # Strict isolation: only conversation owner or Admin/Support can connect to this socket
        if current_user.role not in ["ADMIN", "SUPPORT"] and conv.user_id != current_user.id:
            await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
            return

    await ws_manager.connect(conversation_id, websocket)
    try:
        while True:
            data = await websocket.receive_json()
            msg_text = data.get("message")
            if msg_text and str(msg_text).strip():
                clean_msg = str(msg_text).strip()[:5000]
                async with AsyncSessionLocal() as db:
                    c_check = await db.execute(select(SupportConversation).where(SupportConversation.id == conversation_id))
                    conv_obj = c_check.scalar_one_or_none()
                    if not conv_obj:
                        break

                    # Enforce identity from authenticated token, never trust client-spoofed fields
                    msg = SupportMessage(
                        conversation_id=conversation_id,
                        sender_id=current_user.id,
                        sender_name=current_user.full_name or current_user.username,
                        sender_role=current_user.role,
                        message=clean_msg,
                        created_at=datetime.utcnow()
                    )
                    db.add(msg)
                    conv_obj.updated_at = datetime.utcnow()
                    if current_user.role in ["ADMIN", "SUPPORT"]:
                        conv_obj.status = "WAITING"
                    else:
                        conv_obj.status = "OPEN"
                    await db.commit()
                    await db.refresh(msg)

                    out_data = {
                        "id": msg.id,
                        "conversation_id": conversation_id,
                        "sender_id": current_user.id,
                        "sender_name": current_user.full_name or current_user.username,
                        "sender_role": current_user.role,
                        "message": msg.message,
                        "attachment": None,
                        "created_at": msg.created_at.isoformat()
                    }
                    await ws_manager.broadcast(conversation_id, out_data)
    except WebSocketDisconnect:
        ws_manager.disconnect(conversation_id, websocket)

class LiveChatRequest(BaseModel):
    message: str
    sender_name: Optional[str] = "Khách hàng"
    email: Optional[str] = None
    balance: Optional[float] = None
    guest_session_id: Optional[str] = None

@router.get("/live-chat/messages", response_model=ApiResponse[List[SupportMessageResponse]])
async def get_live_chat_messages(
    guest_name: Optional[str] = Query(None),
    guest_session_id: Optional[str] = Query(None),
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    conv = None
    if current_user:
        # Strictly query this authenticated user's own live chat
        c_stmt = (
            select(SupportConversation)
            .where(
                SupportConversation.user_id == current_user.id,
                SupportConversation.subject.in_([
                    "Live Chat",
                    "Hỗ trợ trực tuyến (Live Chat)",
                    "Hỗ trợ trực tuyến",
                    "Hỗ trợ trực tuyến (Live Chat)",
                    "Hỗ trợ trực tuyến"
                ])
            )
            .order_by(desc(SupportConversation.updated_at))
            .limit(1)
        )
        c_res = await db.execute(c_stmt)
        conv = c_res.scalar_one_or_none()
    elif guest_session_id and guest_session_id.strip():
        # Match STRICTLY by unique guest_session_id token to prevent cross-customer message leaks
        clean_sid = guest_session_id.strip()
        if not re.match(r"^[a-zA-Z0-9_\-]{6,64}$", clean_sid):
            raise HTTPException(status_code=400, detail="Mã phiên khách không hợp lệ.")
        c_stmt = (
            select(SupportConversation)
            .where(
                (SupportConversation.user_id == None) | (SupportConversation.user_id == 0),
                SupportConversation.tags.like(f"%session:{clean_sid}%")
            )
            .order_by(desc(SupportConversation.updated_at))
            .limit(1)
        )
        c_res = await db.execute(c_stmt)
        conv = c_res.scalar_one_or_none()
    # Strictly do not match solely by guest_name to prevent cross-customer message leaks
    else:
        return ApiResponse(data=[])

    if not conv:
        return ApiResponse(data=[])

    m_stmt = (
        select(SupportMessage)
        .where(
            SupportMessage.conversation_id == conv.id,
            (SupportMessage.is_hidden == False) | (SupportMessage.is_hidden == None)
        )
        .order_by(SupportMessage.created_at)
    )
    m_res = await db.execute(m_stmt)
    messages = m_res.scalars().all()
    return ApiResponse(data=[SupportMessageResponse.model_validate(m) for m in messages])

@router.post("/live-chat", response_model=ApiResponse[dict])
async def handle_live_chat(
    payload: LiveChatRequest,
    current_user: Optional[User] = Depends(get_optional_current_user),
    db: AsyncSession = Depends(get_db)
):
    clean_msg = payload.message.strip() if payload.message else ""
    if not clean_msg:
        raise HTTPException(status_code=400, detail="Vui lòng nhập nội dung tin nhắn.")
    
    sender = (current_user.full_name or current_user.username) if current_user else (payload.sender_name or "Khách hàng")
    email = current_user.email if current_user else payload.email
    balance = current_user.balance if current_user else None

    conversation_id = None
    if current_user:
        c_stmt = (
            select(SupportConversation)
            .where(
                SupportConversation.user_id == current_user.id,
                SupportConversation.subject.in_([
                    "Live Chat",
                    "Hỗ trợ trực tuyến (Live Chat)",
                    "Hỗ trợ trực tuyến",
                    "Hỗ trợ trực tuyến (Live Chat)",
                    "Hỗ trợ trực tuyến"
                ])
            )
            .order_by(desc(SupportConversation.updated_at))
            .limit(1)
        )
        c_res = await db.execute(c_stmt)
        conv = c_res.scalar_one_or_none()
        cust_type = "VIP" if (current_user.balance or 0) >= 1000000 else "REGULAR"
        if not conv:
            conv = SupportConversation(
                user_id=current_user.id,
                subject="Live Chat",
                status="OPEN",
                customer_type=cust_type,
                product_status="PENDING",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(conv)
            await db.flush()
        else:
            conv.updated_at = datetime.utcnow()
            conv.status = "OPEN"

        msg = SupportMessage(
            conversation_id=conv.id,
            sender_id=current_user.id,
            sender_name=sender,
            sender_role=current_user.role,
            message=clean_msg,
            created_at=datetime.utcnow()
        )
        db.add(msg)
        await db.commit()
        conversation_id = conv.id
    else:
        raw_sid = payload.guest_session_id.strip() if payload.guest_session_id else ""
        if raw_sid and not re.match(r"^[a-zA-Z0-9_\-]{6,64}$", raw_sid):
            raise HTTPException(status_code=400, detail="Mã phiên khách không hợp lệ.")
        clean_sid = raw_sid if raw_sid else f"guest_{uuid.uuid4().hex[:12]}"
        guest_sender = payload.sender_name.strip() if payload.sender_name else "Khách hàng"
        conv = None
        if clean_sid:
            c_stmt = (
                select(SupportConversation)
                .where(
                    (SupportConversation.user_id == None) | (SupportConversation.user_id == 0),
                    SupportConversation.tags.like(f"%session:{clean_sid}%")
                )
                .order_by(desc(SupportConversation.updated_at))
                .limit(1)
            )
            c_res = await db.execute(c_stmt)
            conv = c_res.scalar_one_or_none()

        tag_sid = f"session:{clean_sid}" if clean_sid else ""
        if not conv:
            conv = SupportConversation(
                user_id=0,
                guest_name=guest_sender,
                guest_email=email,
                subject=f"Live Chat - {guest_sender}",
                status="OPEN",
                customer_type="GUEST",
                product_status="PENDING",
                tags=tag_sid,
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            db.add(conv)
            await db.flush()
        else:
            conv.updated_at = datetime.utcnow()
            conv.status = "OPEN"
            if clean_sid and tag_sid not in (conv.tags or ""):
                conv.tags = f"{conv.tags or ''},{tag_sid}".strip(",")

        msg = SupportMessage(
            conversation_id=conv.id,
            sender_id=0,
            sender_name=guest_sender,
            sender_role="GUEST",
            message=clean_msg,
            created_at=datetime.utcnow()
        )
        db.add(msg)
        await db.commit()
        conversation_id = conv.id

    await TelegramNotifier.notify_live_chat(
        sender=sender,
        message=clean_msg,
        email=email,
        balance=balance
    )
    
    return ApiResponse(
        data={
            "sent": True,
            "conversation_id": conversation_id,
            "timestamp": datetime.utcnow().isoformat()
        },
        message="Tin nhắn của bạn đã được gửi thành công!"
    )

class BulkMessageDeleteRequest(BaseModel):
    message_ids: List[int]

@router.post('/live-chat/clear-messages', response_model=ApiResponse[dict])
async def clear_live_chat_messages(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import delete
    c_stmt = (
        select(SupportConversation.id)
        .where(
            SupportConversation.user_id == current_user.id,
            SupportConversation.subject.in_([
                'Live Chat',
                'Hỗ trợ trực tuyến (Live Chat)',
                'Hỗ trợ trực tuyến'
            ])
        )
    )
    c_res = await db.execute(c_stmt)
    conv_ids = c_res.scalars().all()
    if not conv_ids:
        return ApiResponse(data={'deleted_count': 0}, message='Không có tin nhắn nào để xóa.')
    
    del_stmt = delete(SupportMessage).where(SupportMessage.conversation_id.in_(conv_ids))
    del_res = await db.execute(del_stmt)
    await db.commit()
    return ApiResponse(
        data={'deleted_count': del_res.rowcount},
        message=f'Đã xóa toàn bộ {del_res.rowcount} tin nhắn trong cuộc trò chuyện.'
    )

@router.post('/live-chat/messages/bulk-delete', response_model=ApiResponse[dict])
async def bulk_delete_live_chat_messages(
    payload: BulkMessageDeleteRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    from sqlalchemy import delete
    if not payload.message_ids:
        return ApiResponse(data={'deleted_count': 0}, message='Không có tin nhắn nào được chọn.')

    c_stmt = select(SupportConversation.id).where(SupportConversation.user_id == current_user.id)
    c_res = await db.execute(c_stmt)
    user_conv_ids = set(c_res.scalars().all())

    del_stmt = (
        delete(SupportMessage)
        .where(
            SupportMessage.id.in_(payload.message_ids),
            SupportMessage.conversation_id.in_(user_conv_ids)
        )
    )
    del_res = await db.execute(del_stmt)
    await db.commit()
    return ApiResponse(
        data={'deleted_count': del_res.rowcount},
        message=f'Đã xóa thành công {del_res.rowcount} tin nhắn đã chọn.'
    )
