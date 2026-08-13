"""
Routers secundarios — FitMind.
  /workouts      — CRUD rutinas
  /nutrition     — Registro y consulta de comidas
  /progress      — Métricas de progreso (peso, rendimiento)
  /observability — Dashboard admin de logs IA
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional
import json

from routers.auth import get_current_user, require_admin
from database.connection import get_connection
from services.observability import get_observability_stats


# ════════════════════════════════════════════════════════════════
# WORKOUTS
# ════════════════════════════════════════════════════════════════

workouts_router = APIRouter(prefix="/workouts", tags=["Workouts"])


@workouts_router.get("/")
def list_workouts(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM rutinas WHERE usuario_id = ? ORDER BY creado_en DESC",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    rutinas = []
    for r in rows:
        d = dict(r)
        try:
            d["contenido"] = json.loads(d["contenido"])
        except Exception:
            pass
        rutinas.append(d)
    return rutinas


@workouts_router.get("/active")
def get_active_workout(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM rutinas WHERE usuario_id = ? AND activa = 1 ORDER BY id DESC LIMIT 1",
        (current_user["id"],),
    ).fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    try:
        d["contenido"] = json.loads(d["contenido"])
    except Exception:
        pass
    return d


@workouts_router.get("/{workout_id}")
def get_workout(workout_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM rutinas WHERE id = ? AND usuario_id = ?",
        (workout_id, current_user["id"]),
    ).fetchone()
    conn.close()
    if not row:
        raise HTTPException(status_code=404, detail="Rutina no encontrada.")
    d = dict(row)
    try:
        d["contenido"] = json.loads(d["contenido"])
    except Exception:
        pass
    return d


@workouts_router.delete("/{workout_id}")
def delete_workout(workout_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    conn.execute(
        "DELETE FROM rutinas WHERE id = ? AND usuario_id = ?",
        (workout_id, current_user["id"]),
    )
    conn.commit()
    conn.close()
    return {"message": "Rutina eliminada."}


# Log de ejercicio
class ExerciseLog(BaseModel):
    ejercicio: str
    series: Optional[int] = 0
    repeticiones: Optional[int] = 0
    duracion_min: Optional[float] = 0
    calorias_quemadas: Optional[float] = 0
    peso_usado_kg: Optional[float] = 0
    notas: Optional[str] = None


@workouts_router.post("/log")
def log_exercise(body: ExerciseLog, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO registro_ejercicios
        (usuario_id, ejercicio, series, repeticiones, duracion_min, calorias_quemadas, peso_usado_kg, notas)
        VALUES (?,?,?,?,?,?,?,?)
        """,
        (current_user["id"], body.ejercicio, body.series, body.repeticiones,
         body.duracion_min, body.calorias_quemadas, body.peso_usado_kg, body.notas),
    )
    conn.commit()
    conn.close()
    return {"message": f"{body.ejercicio} registrado."}


@workouts_router.get("/log/today")
def get_today_log(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM registro_ejercicios WHERE usuario_id = ? AND fecha = date('now')",
        (current_user["id"],),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════
# NUTRITION
# ════════════════════════════════════════════════════════════════

nutrition_router = APIRouter(prefix="/nutrition", tags=["Nutrition"])


class FoodLog(BaseModel):
    alimento: str
    cantidad_g: float
    calorias: float
    proteinas_g: Optional[float] = 0
    carbohidratos_g: Optional[float] = 0
    grasas_g: Optional[float] = 0


@nutrition_router.get("/today")
def get_today_nutrition(current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM registro_comidas WHERE usuario_id = ? AND fecha = date('now') ORDER BY hora",
        (current_user["id"],),
    ).fetchall()
    totals = conn.execute(
        """
        SELECT COALESCE(SUM(calorias),0) as calorias,
               COALESCE(SUM(proteinas_g),0) as proteinas_g,
               COALESCE(SUM(carbohidratos_g),0) as carbohidratos_g,
               COALESCE(SUM(grasas_g),0) as grasas_g
        FROM registro_comidas
        WHERE usuario_id = ? AND fecha = date('now')
        """,
        (current_user["id"],),
    ).fetchone()
    conn.close()
    return {
        "comidas": [dict(r) for r in rows],
        "totales": dict(totals),
    }


@nutrition_router.post("/log", status_code=201)
def log_food(body: FoodLog, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    conn.execute(
        """
        INSERT INTO registro_comidas
        (usuario_id, alimento, cantidad_g, calorias, proteinas_g, carbohidratos_g, grasas_g)
        VALUES (?,?,?,?,?,?,?)
        """,
        (current_user["id"], body.alimento, body.cantidad_g, body.calorias,
         body.proteinas_g, body.carbohidratos_g, body.grasas_g),
    )
    conn.commit()
    conn.close()
    return {"message": f"{body.alimento} registrado."}


@nutrition_router.delete("/log/{entry_id}")
def delete_food_log(entry_id: int, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    conn.execute(
        "DELETE FROM registro_comidas WHERE id = ? AND usuario_id = ?",
        (entry_id, current_user["id"]),
    )
    conn.commit()
    conn.close()
    return {"message": "Registro eliminado."}


@nutrition_router.get("/history")
def get_nutrition_history(
    days: int = 7,
    current_user: dict = Depends(get_current_user),
):
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT fecha,
               SUM(calorias) as calorias,
               SUM(proteinas_g) as proteinas_g,
               SUM(carbohidratos_g) as carbohidratos_g,
               SUM(grasas_g) as grasas_g
        FROM registro_comidas
        WHERE usuario_id = ? AND fecha >= date('now', ? || ' days')
        GROUP BY fecha
        ORDER BY fecha ASC
        """,
        (current_user["id"], f"-{days}"),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ════════════════════════════════════════════════════════════════
# PROGRESS
# ════════════════════════════════════════════════════════════════

progress_router = APIRouter(prefix="/progress", tags=["Progress"])


class WeightLog(BaseModel):
    peso_kg: float
    grasa_pct: Optional[float] = None
    musculo_pct: Optional[float] = None
    notas: Optional[str] = None


@progress_router.get("/")
def get_progress(
    days: int = 90,
    current_user: dict = Depends(get_current_user),
):
    conn = get_connection()

    pesos = conn.execute(
        """
        SELECT peso_kg, grasa_pct, musculo_pct, notas, fecha
        FROM progreso_peso
        WHERE usuario_id = ? AND fecha >= date('now', ? || ' days')
        ORDER BY fecha ASC
        """,
        (current_user["id"], f"-{days}"),
    ).fetchall()

    ejercicios_semana = conn.execute(
        """
        SELECT COUNT(DISTINCT fecha) as dias,
               SUM(calorias_quemadas) as cal_quemadas
        FROM registro_ejercicios
        WHERE usuario_id = ? AND fecha >= date('now', '-7 days')
        """,
        (current_user["id"],),
    ).fetchone()

    conn.close()

    pesos_list = [dict(p) for p in pesos]
    cambio = 0.0
    if len(pesos_list) >= 2:
        cambio = round(pesos_list[-1]["peso_kg"] - pesos_list[0]["peso_kg"], 1)

    return {
        "historial_peso": pesos_list,
        "cambio_peso_kg": cambio,
        "dias_entrenados_semana": ejercicios_semana["dias"] or 0,
        "calorias_quemadas_semana": round(ejercicios_semana["cal_quemadas"] or 0),
    }


@progress_router.post("/weight", status_code=201)
def log_weight(body: WeightLog, current_user: dict = Depends(get_current_user)):
    conn = get_connection()
    conn.execute(
        "INSERT INTO progreso_peso (usuario_id, peso_kg, grasa_pct, musculo_pct, notas) VALUES (?,?,?,?,?)",
        (current_user["id"], body.peso_kg, body.grasa_pct, body.musculo_pct, body.notas),
    )
    # Actualizar peso en perfil
    conn.execute(
        "UPDATE usuarios SET peso_kg = ? WHERE id = ?",
        (body.peso_kg, current_user["id"]),
    )
    conn.commit()
    conn.close()
    return {"message": f"Peso {body.peso_kg}kg registrado."}


@progress_router.get("/summary")
def get_summary(current_user: dict = Depends(get_current_user)):
    conn = get_connection()

    last_peso = conn.execute(
        "SELECT peso_kg, fecha FROM progreso_peso WHERE usuario_id = ? ORDER BY fecha DESC LIMIT 1",
        (current_user["id"],),
    ).fetchone()

    total_workouts = conn.execute(
        "SELECT COUNT(DISTINCT fecha) as total FROM registro_ejercicios WHERE usuario_id = ?",
        (current_user["id"],),
    ).fetchone()

    streak = conn.execute(
        """
        SELECT COUNT(DISTINCT fecha) as racha
        FROM registro_ejercicios
        WHERE usuario_id = ? AND fecha >= date('now', '-7 days')
        """,
        (current_user["id"],),
    ).fetchone()

    conn.close()

    return {
        "peso_actual": dict(last_peso) if last_peso else None,
        "total_entrenamientos": total_workouts["total"] if total_workouts else 0,
        "racha_semanal": streak["racha"] if streak else 0,
    }


# ════════════════════════════════════════════════════════════════
# OBSERVABILIDAD
# ════════════════════════════════════════════════════════════════

observability_router = APIRouter(prefix="/observability", tags=["Observability"])


@observability_router.get("/logs")
def get_logs(
    limit: int = 100,
    current_user: dict = Depends(require_admin),
):
    data = get_observability_stats(limit=limit)
    return data


@observability_router.get("/stats")
def get_stats(current_user: dict = Depends(require_admin)):
    data = get_observability_stats(limit=1000)
    return {
        "stats": data["stats"],
        "top_tools": data["top_tools"],
    }
