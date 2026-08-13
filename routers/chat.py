"""
Router de IA Chat — FitMind.

Endpoints:
  POST /chat                          — Mensaje síncrono
  GET  /chat/stream                   — Streaming SSE (Semana 5)
  GET  /chat/conversations            — Listar conversaciones del usuario
  POST /chat/conversations            — Crear nueva conversación (UUID)
  GET  /chat/conversations/{id}/messages — Historial de mensajes
  DELETE /chat/conversations/{id}     — Eliminar conversación
"""

import time
import uuid
from collections import defaultdict, deque
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from typing import Optional

from routers.auth import get_current_user
from services.ai_service import process_chat, stream_chat
from services.memory import (
    create_conversation, get_conversations, get_conversation, get_messages,
    get_or_create_active_conversation,
)
from database.connection import get_connection

router = APIRouter(prefix="/chat", tags=["Chat & IA"])

# Límite de longitud del mensaje enviado por GET ?message= en /chat/stream.
# nginx (proxy delante del backend) ya corta la URL en ~5-6KB (OTC-LLM-04);
# este límite server-side es la fuente de verdad real, independiente del
# proxy, y evita cargar todo el string en memoria/DB si alguien lo saltea.
# 1200 caracteres ≈ 200-250 palabras: de sobra para una pregunta de fitness
# real, y bastante más chico que antes (4000) para reducir la superficie de
# ataques de "context stuffing" (ver COVERAGE-GAP-R08 del pentest) y el
# costo/latencia de mensajes abusivamente largos.
MAX_MESSAGE_LENGTH = 1200
MAX_TITLE_LENGTH = 200


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_MESSAGE_LENGTH)
    conversation_id: Optional[str] = None
    session_id: Optional[str] = None


class NewConversationRequest(BaseModel):
    title: Optional[str] = Field(default="Nueva conversación", max_length=MAX_TITLE_LENGTH)

    @field_validator("title")
    @classmethod
    def _clean_title(cls, v: Optional[str]) -> str:
        v = (v or "").strip()
        return v[:MAX_TITLE_LENGTH] or "Nueva conversación"


# ─── Rate-limit de creación de conversaciones, por usuario (OTC-AUTH-02) ──────
# El rate-limit global de main.py es por IP y cubre todo el prefijo /chat a
# 60 req/60s, pensado para el volumen normal de mensajes de chat. Crear
# conversaciones es una acción más "cara" (fila nueva en DB) y varios
# usuarios legítimos pueden compartir la misma IP (NAT, oficina, VPN), así
# que además de eso se aplica un límite propio, por usuario autenticado.
_CONV_CREATE_LIMIT = 10       # conversaciones
_CONV_CREATE_WINDOW_S = 300   # por 5 minutos
_conv_create_log: dict[int, deque] = defaultdict(deque)


def _check_conversation_rate_limit(user_id: int) -> None:
    now = time.time()
    log = _conv_create_log[user_id]
    while log and now - log[0] > _CONV_CREATE_WINDOW_S:
        log.popleft()
    if len(log) >= _CONV_CREATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Estás creando demasiadas conversaciones nuevas. Espera unos minutos.",
        )
    log.append(now)


def _get_rag_db():
    """Intenta cargar ChromaDB. Retorna None si no está disponible."""
    try:
        import os
        from langchain_chroma import Chroma
        from langchain_huggingface import HuggingFaceEmbeddings
        chroma_path = os.getenv("CHROMA_DB_PATH", "chroma_db")
        if not os.path.exists(chroma_path):
            return None
        embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        return Chroma(persist_directory=chroma_path, embedding_function=embeddings)
    except Exception:
        return None


# ─── Conversaciones ──────────────────────────────────────────────────────────

@router.get("/conversations")
def list_conversations(current_user: dict = Depends(get_current_user)):
    return get_conversations(current_user["id"])


@router.post("/conversations", status_code=201)
def new_conversation(
    body: NewConversationRequest,
    current_user: dict = Depends(get_current_user),
):
    _check_conversation_rate_limit(current_user["id"])
    conv_id = create_conversation(current_user["id"], title=body.title or "Nueva conversación")
    return {"conversation_id": conv_id, "title": body.title}


@router.get("/conversations/{conversation_id}/messages")
def get_conversation_messages(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
):
    conv = get_conversation(conversation_id, current_user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")
    messages = get_messages(conversation_id)
    return {"conversation_id": conversation_id, "messages": messages}


@router.delete("/conversations/{conversation_id}")
def delete_conversation(
    conversation_id: str,
    current_user: dict = Depends(get_current_user),
):
    conv = get_conversation(conversation_id, current_user["id"])
    if not conv:
        raise HTTPException(status_code=404, detail="Conversación no encontrada.")

    conn = get_connection()
    try:
        conn.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
        cur = conn.execute(
            "DELETE FROM conversations WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, current_user["id"]),
        )
        if cur.rowcount == 0:
            # No debería pasar (ya validamos ownership arriba), pero si pasa
            # es mejor abortar todo el borrado que dejar mensajes huérfanos
            # sin conversación, o borrar una conversación sin sus mensajes.
            conn.rollback()
            raise HTTPException(status_code=404, detail="Conversación no encontrada.")
        conn.commit()
    finally:
        conn.close()
    return {"message": "Conversación eliminada."}


# ─── Chat síncrono ────────────────────────────────────────────────────────────

@router.post("/")
def chat(
    body: ChatRequest,
    current_user: dict = Depends(get_current_user),
):
    user_id = current_user["id"]

    # Reusar conversación activa si no se especifica una (OTC-AUTH-02): antes,
    # cada llamada sin conversation_id creaba una fila nueva en `conversations`,
    # lo que permitía "inflar" la tabla del usuario sin límite real con solo
    # omitir el parámetro. Ahora se reutiliza la conversación vacía más
    # reciente del usuario si existe, y solo se crea una nueva si hace falta.
    conv_id = body.conversation_id
    if not conv_id:
        conv_id = get_or_create_active_conversation(user_id)
    else:
        conv = get_conversation(conv_id, user_id)
        if not conv:
            conv_id = get_or_create_active_conversation(user_id)

    # Perfil para personalizar la respuesta
    user_profile = dict(current_user)
    user_profile.pop("password_hash", None)

    rag_db = _get_rag_db()
    session_id = body.session_id or str(uuid.uuid4())

    response, tools, ui_directive = process_chat(
        message=body.message,
        user_id=user_id,
        conversation_id=conv_id,
        user_profile=user_profile,
        rag_db=rag_db,
        session_id=session_id,
    )

    return {
        "response": response,
        "conversation_id": conv_id,
        "tools_executed": tools,
        "ui_directive": ui_directive,
        "session_id": session_id,
    }


# ─── Streaming SSE (Semana 5) ─────────────────────────────────────────────────

@router.get("/stream")
async def chat_stream(
    message: str,
    conversation_id: Optional[str] = None,
    session_id: Optional[str] = None,
    current_user: dict = Depends(get_current_user),
):
    if len(message) > MAX_MESSAGE_LENGTH:
        raise HTTPException(
            status_code=413,
            detail=f"Mensaje demasiado largo (máximo {MAX_MESSAGE_LENGTH} caracteres).",
        )

    user_id = current_user["id"]

    # Ver comentario equivalente en chat() más arriba (OTC-AUTH-02).
    conv_id = conversation_id
    if not conv_id:
        conv_id = get_or_create_active_conversation(user_id)
    else:
        conv = get_conversation(conv_id, user_id)
        if not conv:
            conv_id = get_or_create_active_conversation(user_id)

    user_profile = dict(current_user)
    user_profile.pop("password_hash", None)

    rag_db = _get_rag_db()
    sid = session_id or str(uuid.uuid4())

    async def event_generator():
        async for chunk in stream_chat(
            message=message,
            user_id=user_id,
            conversation_id=conv_id,
            user_profile=user_profile,
            rag_db=rag_db,
            session_id=sid,
        ):
            yield chunk

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Conversation-ID": conv_id,
            "X-Session-ID": sid,
            "Access-Control-Expose-Headers": "X-Conversation-ID, X-Session-ID",
        },
    )
