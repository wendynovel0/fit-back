"""
Servicio de Memoria Persistente — Semana 4.

Implementa:
  - Sliding Window: mantiene System Prompt + Resumen + últimos N mensajes
  - Summarization: cuando el historial es largo, resume mensajes antiguos
  - Resiliencia: errores registrados, conversación nunca se corrompe
"""

import os
import json
import uuid
import asyncio
from typing import Optional
from datetime import datetime
from database.connection import get_connection

MAX_HISTORY = int(os.getenv("MAX_HISTORY_MESSAGES", "20"))
SUMMARY_THRESHOLD = int(os.getenv("SUMMARY_THRESHOLD", "30"))


# ─── Gestión de conversaciones ────────────────────────────────────────────────

def create_conversation(user_id: int, title: str = "Nueva conversación") -> str:
    """Crea una nueva conversación y retorna su UUID.

    Antes fallaba en silencio (solo loggeaba) y devolvía igual el UUID
    generado en memoria, aunque el INSERT nunca se hubiera confirmado en
    DB — el caller seguía adelante con un conversation_id fantasma que
    nunca podía usarse para guardar mensajes. Ahora se relanza la
    excepción: el router ya tiene manejo global de errores (500 genérico,
    sin leak) para este caso.
    """
    conversation_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO conversations (conversation_id, user_id, title)
            VALUES (?, ?, ?)
            """,
            (conversation_id, user_id, title),
        )
        conn.commit()
    except Exception as e:
        print(f"[Memory] Error al crear conversación: {e}")
        raise
    finally:
        conn.close()
    return conversation_id


def get_conversations(user_id: int) -> list[dict]:
    """Retorna todas las conversaciones de un usuario."""
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT conversation_id, title, summary, created_at, updated_at
            FROM conversations
            WHERE user_id = ?
            ORDER BY updated_at DESC
            """,
            (user_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[Memory] Error al obtener conversaciones: {e}")
        return []


def get_or_create_active_conversation(user_id: int) -> str:
    """Reutiliza la conversación vacía más reciente del usuario, o crea una nueva.

    Mitigación de OTC-AUTH-02: antes, cada request de chat sin `conversation_id`
    explícito (comportamiento normal del frontend en un chat nuevo) insertaba
    una fila nueva en `conversations`, permitiendo inflar la tabla de un
    usuario sin límite real con solo omitir el parámetro repetidamente. Ahora
    solo se crea una fila nueva si el usuario no tiene ya una conversación sin
    mensajes esperando ser usada.
    """
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT c.conversation_id
            FROM conversations c
            WHERE c.user_id = ?
              AND NOT EXISTS (SELECT 1 FROM messages m WHERE m.conversation_id = c.conversation_id)
            ORDER BY c.created_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        conn.close()
        if row:
            return row["conversation_id"]
    except Exception as e:
        print(f"[Memory] Error al buscar conversación activa: {e}")
    return create_conversation(user_id, title="Nueva conversación")


def get_conversation(conversation_id: str, user_id: int) -> Optional[dict]:
    """Retorna una conversación específica si pertenece al usuario."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM conversations WHERE conversation_id = ? AND user_id = ?",
            (conversation_id, user_id),
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def update_conversation_timestamp(conversation_id: str) -> None:
    """Actualiza updated_at de la conversación."""
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE conversations SET updated_at = datetime('now') WHERE conversation_id = ?",
            (conversation_id,),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Memory] Error al actualizar timestamp: {e}")


# ─── Gestión de mensajes ─────────────────────────────────────────────────────

def save_message(
    conversation_id: str,
    role: str,
    content: str,
    tool_name: Optional[str] = None,
) -> None:
    """Persiste un mensaje en la tabla messages.

    Falla en silencio a propósito (a diferencia de create_conversation): en
    medio de un turno de chat ya en curso, no tiene sentido tumbar la
    respuesta al usuario porque un INSERT de auditoría/historial falló — el
    error queda logueado para investigar, pero la conversación sigue.
    """
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO messages (conversation_id, role, content, tool_name)
            VALUES (?, ?, ?, ?)
            """,
            (conversation_id, role, content, tool_name),
        )
        conn.commit()
        conn.close()
        update_conversation_timestamp(conversation_id)
    except Exception as e:
        print(f"[Memory] Error al guardar mensaje: {e}")


def get_messages(conversation_id: str) -> list[dict]:
    """Retorna todos los mensajes de una conversación.

    Incluye 'id' (antes no se seleccionaba): sin él, maybe_summarize() no
    podía identificar qué filas borrar tras resumirlas, así que los
    mensajes antiguos nunca se eliminaban (bug funcional — la tabla
    `messages` crecía sin límite y la "memoria" nunca se compactaba).
    """
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT id, role, content, tool_name, timestamp
            FROM messages
            WHERE conversation_id = ?
            ORDER BY id ASC
            """,
            (conversation_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        print(f"[Memory] Error al obtener mensajes: {e}")
        return []


def count_messages(conversation_id: str) -> int:
    """Cuenta los mensajes de una conversación."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) as cnt FROM messages WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        conn.close()
        return row["cnt"] if row else 0
    except Exception:
        return 0


# ─── Memory Graph — memoria semántica estructurada (Capa 3 del CTO review) ───
#
# A diferencia de la memoria episódica de arriba (mensajes + resumen), esto
# es un perfil "vivo" del usuario: objetivos, restricciones, preferencias,
# patrones detectados y eventos clave. Se actualiza por:
#   (a) extracción automática — la IA llama a la tool `actualizar_memoria_usuario`
#       cuando detecta un dato relevante en la conversación.
#   (b) confirmación explícita — onboarding / ajustes de perfil.

MEMORY_GRAPH_FIELDS = [
    "objetivos", "restricciones", "preferencias",
    "patrones_detectados", "eventos_clave",
]


def get_memory_graph(user_id: int) -> dict:
    """Retorna el Memory Graph del usuario (crea uno vacío si no existe)."""
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM memory_graph WHERE usuario_id = ?", (user_id,)
        ).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO memory_graph (usuario_id) VALUES (?)", (user_id,)
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM memory_graph WHERE usuario_id = ?", (user_id,)
            ).fetchone()
        conn.close()
        graph = dict(row)
        for field in MEMORY_GRAPH_FIELDS:
            try:
                graph[field] = json.loads(graph.get(field) or "[]")
            except (json.JSONDecodeError, TypeError):
                graph[field] = []
        return graph
    except Exception as e:
        print(f"[MemoryGraph] Error al obtener grafo: {e}")
        return {f: [] for f in MEMORY_GRAPH_FIELDS}


def update_memory_graph(user_id: int, field: str, value: str) -> dict:
    """
    Agrega (o actualiza) un dato en el Memory Graph del usuario.

    field debe ser uno de MEMORY_GRAPH_FIELDS. Evita duplicados exactos.
    """
    if field not in MEMORY_GRAPH_FIELDS:
        return {"ok": False, "error": f"Campo inválido: {field}"}

    graph = get_memory_graph(user_id)
    items = graph.get(field, [])
    if value not in items:
        items.append(value)

    try:
        conn = get_connection()
        conn.execute(
            f"UPDATE memory_graph SET {field} = ?, actualizado_en = datetime('now') "
            f"WHERE usuario_id = ?",
            (json.dumps(items, ensure_ascii=False), user_id),
        )
        conn.commit()
        conn.close()
        return {"ok": True, field: items}
    except Exception as e:
        # SEGURIDAD (OTC-LLM-05 / patrón OTC-LLM-01): antes str(e) se devolvía
        # tal cual en "error", y actualizar_memoria_usuario lo pasaba directo
        # a _err() sin sanitizar — el traceback/detalle SQL terminaba en la
        # respuesta del LLM al usuario. Ahora se loguea completo server-side
        # y se devuelve un código interno genérico y seguro.
        print(f"[MemoryGraph] Error al actualizar {field} de usuario {user_id}: {e!r}")
        return {"ok": False, "error": "MEMORY_UPDATE_FAILED"}


def replace_memory_graph_field(user_id: int, field: str, values: list[str]) -> dict:
    """
    Reemplaza por completo un campo del Memory Graph (a diferencia de
    update_memory_graph, que solo agrega).

    Se usa cuando el cambio viene de una fuente "de verdad" explícita —p.ej.
    el usuario edita su objetivo en Perfil/Onboarding— y NO tiene sentido
    seguir arrastrando el objetivo viejo junto al nuevo en la memoria que lee
    la IA (antes, cambiar de objetivo en el perfil dejaba el objetivo
    anterior colgado para siempre en memory_graph.objetivos, porque solo
    existía la vía de "agregar"; el system prompt terminaba mostrándole al
    modelo dos objetivos contradictorios).
    """
    if field not in MEMORY_GRAPH_FIELDS:
        return {"ok": False, "error": f"Campo inválido: {field}"}
    get_memory_graph(user_id)  # asegura que exista la fila
    try:
        conn = get_connection()
        conn.execute(
            f"UPDATE memory_graph SET {field} = ?, actualizado_en = datetime('now') "
            f"WHERE usuario_id = ?",
            (json.dumps(values, ensure_ascii=False), user_id),
        )
        conn.commit()
        conn.close()
        return {"ok": True, field: values}
    except Exception as e:
        print(f"[MemoryGraph] Error al reemplazar {field} de usuario {user_id}: {e!r}")
        return {"ok": False, "error": "MEMORY_UPDATE_FAILED"}


def build_memory_graph_prompt(user_id: int) -> str:
    """Construye el bloque de texto del Memory Graph para inyectar en el system prompt."""
    graph = get_memory_graph(user_id)
    if not any(graph.get(f) for f in MEMORY_GRAPH_FIELDS):
        return ""

    lines = ["\n\nMEMORIA VIVA DEL USUARIO (perfil estructurado detectado a través del tiempo):"]
    labels = {
        "objetivos": "Objetivos",
        "restricciones": "Restricciones (lesiones, alergias, límites)",
        "preferencias": "Preferencias",
        "patrones_detectados": "Patrones detectados",
        "eventos_clave": "Eventos clave",
    }
    for field, label in labels.items():
        values = graph.get(field, [])
        if values:
            lines.append(f"- {label}: {', '.join(values)}")
    lines.append(
        "Usa esta memoria para personalizar tus respuestas sin que el usuario "
        "tenga que repetir esta información."
    )
    return "\n".join(lines)


# ─── Context Window con Sliding Window ───────────────────────────────────────

def build_context_window(
    conversation_id: str,
    user_profile: Optional[dict] = None,
) -> list[dict]:
    """
    Construye el contexto para el LLM usando Sliding Window.

    Retorna lista de mensajes en formato OpenAI/Groq:
      [system, ...historial_o_resumen..., últimos_N_mensajes]
    """
    messages = get_messages(conversation_id)

    # Obtener resumen si existe
    summary = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT summary FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        conn.close()
        if row:
            summary = row["summary"]
    except Exception:
        pass

    # Sliding window: últimos MAX_HISTORY mensajes
    recent = messages[-MAX_HISTORY:] if len(messages) > MAX_HISTORY else messages

    # Construir lista para el LLM
    context: list[dict] = []

    # Añadir resumen si existe y hay mensajes truncados
    if summary and len(messages) > MAX_HISTORY:
        context.append({
            "role": "user",
            "content": f"[Resumen de conversación anterior]\n{summary}",
        })
        context.append({
            "role": "assistant",
            "content": "Entendido. Tengo en cuenta el resumen de nuestra conversación anterior.",
        })

    # Añadir mensajes recientes
    for msg in recent:
        if msg["role"] in ("user", "assistant"):
            context.append({"role": msg["role"], "content": msg["content"]})

    return context


# ─── Summarization ────────────────────────────────────────────────────────────

async def maybe_summarize(
    conversation_id: str,
    groq_client,
    model: str,
) -> None:
    """
    Si el número de mensajes supera SUMMARY_THRESHOLD, resume los mensajes
    antiguos y guarda el resumen en la conversación.

    Falla en silencio: nunca interrumpe el flujo principal.
    """
    try:
        total = count_messages(conversation_id)
        if total < SUMMARY_THRESHOLD:
            return

        messages = get_messages(conversation_id)
        # Tomar mensajes antiguos (todos excepto los últimos MAX_HISTORY)
        old_messages = messages[:-MAX_HISTORY]
        if not old_messages:
            return

        # Construir texto para resumir
        transcript = "\n".join(
            f"{m['role'].upper()}: {m['content']}"
            for m in old_messages
            if m["role"] in ("user", "assistant")
        )

        # El SDK de Groq es síncrono. Esta función corre como
        # asyncio.create_task() en paralelo al request principal (ver
        # stream_chat) — llamarlo directamente bloquearía el event loop
        # completo (todas las demás requests concurrentes) durante la
        # llamada de red. asyncio.to_thread lo saca a un hilo aparte.
        response = await asyncio.to_thread(
            groq_client.chat.completions.create,
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Eres un asistente de fitness. Resume la siguiente conversación "
                        "de manera concisa (máximo 200 palabras), preservando los datos "
                        "clave del usuario (peso, objetivo, rutinas, progreso). "
                        "Responde SOLO con el resumen, sin introducción."
                    ),
                },
                {"role": "user", "content": transcript},
            ],
            max_tokens=300,
        )

        summary = response.choices[0].message.content or ""

        # Guardar resumen
        conn = get_connection()
        conn.execute(
            "UPDATE conversations SET summary = ? WHERE conversation_id = ?",
            (summary, conversation_id),
        )
        conn.commit()
        conn.close()

        # Marcar mensajes antiguos como resumidos eliminándolos. get_messages()
        # ahora sí selecciona 'id' (antes no lo hacía, así que old_ids
        # siempre quedaba vacío y estas filas nunca se borraban de verdad).
        conn = get_connection()
        old_ids = [m.get("id") for m in old_messages if m.get("id")]
        if old_ids:
            placeholders = ",".join("?" * len(old_ids))
            conn.execute(
                f"DELETE FROM messages WHERE id IN ({placeholders})", old_ids
            )
            conn.commit()
        conn.close()

    except Exception as e:
        print(f"[Memory] Error en summarization (no crítico): {e}")
