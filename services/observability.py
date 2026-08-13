"""
Servicio de Observabilidad IA — FitMind.

Registra en ai_observability_logs:
  - session_id, user_id
  - user_prompt, system_response
  - ttft_ms (time to first token)
  - total_latency_ms
  - tokens_per_second
  - was_blocked, blocked_reason
  - tools_executed (JSON array)
  - model_used, tokens_input, tokens_output
"""

import json
import time
from typing import Optional
from database.connection import get_connection


def log_ai_event(
    user_prompt: str,
    system_response: str,
    total_latency_ms: float,
    was_blocked: bool = False,
    blocked_reason: Optional[str] = None,
    tools_executed: Optional[list] = None,
    ttft_ms: Optional[float] = None,
    tokens_per_second: Optional[float] = None,
    session_id: Optional[str] = None,
    user_id: Optional[int] = None,
    model_used: str = "openai/gpt-oss-120b",
    tokens_input: int = 0,
    tokens_output: int = 0,
) -> None:
    """
    Registra un evento de IA en la tabla ai_observability_logs.
    No lanza excepciones — falla en silencio para no interrumpir el flujo.
    """
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO ai_observability_logs (
                session_id, user_id, user_prompt, system_response,
                ttft_ms, total_latency_ms, tokens_per_second,
                was_blocked, blocked_reason, tools_executed,
                model_used, tokens_input, tokens_output
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                session_id,
                user_id,
                user_prompt[:2000],  # Truncar prompts muy largos
                system_response[:4000],
                ttft_ms,
                total_latency_ms,
                tokens_per_second,
                1 if was_blocked else 0,
                blocked_reason,
                json.dumps(tools_executed or [], ensure_ascii=False),
                model_used,
                tokens_input,
                tokens_output,
            ),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[Observability] Error al registrar evento: {e}")


def get_observability_stats(limit: int = 100) -> dict:
    """
    Retorna estadísticas agregadas para el dashboard de observabilidad.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()

        # Últimos N logs
        rows = cur.execute(
            """
            SELECT id, session_id, user_id, timestamp, user_prompt,
                   system_response, ttft_ms, total_latency_ms,
                   tokens_per_second, was_blocked, blocked_reason,
                   tools_executed, model_used, tokens_input, tokens_output
            FROM ai_observability_logs
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        logs = [dict(r) for r in rows]

        # Estadísticas globales
        stats_row = cur.execute(
            """
            SELECT
                COUNT(*) as total_requests,
                SUM(was_blocked) as total_blocked,
                AVG(total_latency_ms) as avg_latency_ms,
                AVG(ttft_ms) as avg_ttft_ms,
                AVG(tokens_per_second) as avg_tps,
                MIN(total_latency_ms) as min_latency_ms,
                MAX(total_latency_ms) as max_latency_ms
            FROM ai_observability_logs
            """
        ).fetchone()

        stats = dict(stats_row) if stats_row else {}

        # Herramientas más usadas
        all_tools_rows = cur.execute(
            "SELECT tools_executed FROM ai_observability_logs WHERE tools_executed != '[]'"
        ).fetchall()

        tool_counts: dict[str, int] = {}
        for row in all_tools_rows:
            try:
                tools = json.loads(row["tools_executed"])
                for t in tools:
                    tool_counts[t] = tool_counts.get(t, 0) + 1
            except Exception:
                pass

        top_tools = sorted(tool_counts.items(), key=lambda x: x[1], reverse=True)[:10]

        conn.close()
        return {
            "logs": logs,
            "stats": stats,
            "top_tools": [{"tool": k, "count": v} for k, v in top_tools],
        }
    except Exception as e:
        # Aunque este endpoint es solo para admin (ver require_admin en el
        # router), se mantiene la misma regla del resto del backend: nunca
        # reenviar str(e) crudo — se loguea completo server-side.
        print(f"[Observability] Error al construir stats: {e!r}")
        return {"logs": [], "stats": {}, "top_tools": [], "error": "OBSERVABILITY_QUERY_FAILED"}
