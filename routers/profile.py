"""
Router de perfil de usuario — FitMind.

Endpoints:
  GET  /profile        — Obtener perfil completo
  PUT  /profile        — Actualizar perfil
  PUT  /profile/onboarding — Completar onboarding inicial
"""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from typing import Optional

from routers.auth import get_current_user
from database.connection import get_connection
from services.memory import replace_memory_graph_field

router = APIRouter(prefix="/profile", tags=["Profile"])


class ProfileUpdate(BaseModel):
    nombre: Optional[str] = None
    edad: Optional[int] = None
    peso_kg: Optional[float] = None
    altura_cm: Optional[float] = None
    sexo_biologico: Optional[str] = None
    objetivo: Optional[str] = None
    nivel: Optional[str] = None
    dias_semana: Optional[int] = None
    lugar: Optional[str] = None
    preferencia_nut: Optional[str] = None

    @field_validator("sexo_biologico")
    @classmethod
    def _validar_sexo(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        permitidos = {"masculino", "femenino", "prefiero_no_decir"}
        if v not in permitidos:
            raise ValueError(f"sexo_biologico debe ser uno de: {', '.join(permitidos)}")
        return v


class OnboardingRequest(BaseModel):
    objetivo: str
    nivel: str
    edad: int
    peso_kg: float
    altura_cm: float
    dias_semana: int
    lugar: str
    preferencia_nut: str
    sexo_biologico: Optional[str] = None

    @field_validator("sexo_biologico")
    @classmethod
    def _validar_sexo(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        v = v.strip().lower()
        permitidos = {"masculino", "femenino", "prefiero_no_decir"}
        if v not in permitidos:
            raise ValueError(f"sexo_biologico debe ser uno de: {', '.join(permitidos)}")
        return v


@router.get("/")
def get_profile(current_user: dict = Depends(get_current_user)):
    user = dict(current_user)
    user.pop("password_hash", None)

    # Último peso registrado
    conn = get_connection()
    ultimo_peso = conn.execute(
        "SELECT peso_kg, fecha FROM progreso_peso WHERE usuario_id = ? ORDER BY fecha DESC LIMIT 1",
        (user["id"],),
    ).fetchone()

    # Calorías de hoy
    calorias_hoy = conn.execute(
        """
        SELECT COALESCE(SUM(calorias), 0) as total
        FROM registro_comidas
        WHERE usuario_id = ? AND fecha = date('now')
        """,
        (user["id"],),
    ).fetchone()

    # Días entrenados esta semana
    dias_semana = conn.execute(
        """
        SELECT COUNT(DISTINCT fecha) as dias
        FROM registro_ejercicios
        WHERE usuario_id = ? AND fecha >= date('now', '-7 days')
        """,
        (user["id"],),
    ).fetchone()

    conn.close()

    return {
        **user,
        "ultimo_peso": dict(ultimo_peso) if ultimo_peso else None,
        "calorias_hoy": calorias_hoy["total"] if calorias_hoy else 0,
        "dias_entrenados_semana": dias_semana["dias"] if dias_semana else 0,
    }


@router.put("/")
def update_profile(
    body: ProfileUpdate,
    current_user: dict = Depends(get_current_user),
):
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="No se proporcionaron campos para actualizar.")

    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [current_user["id"]]

    conn = get_connection()
    conn.execute(f"UPDATE usuarios SET {set_clause} WHERE id = ?", values)
    conn.commit()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (current_user["id"],)).fetchone()
    conn.close()

    # Si el usuario cambió su objetivo explícitamente en Perfil, esa es la
    # fuente de verdad: reemplaza (no agrega a) la lista de objetivos que la
    # IA lee del Memory Graph, así no queda un objetivo viejo contradiciendo
    # al nuevo en el system prompt.
    if "objetivo" in fields:
        replace_memory_graph_field(current_user["id"], "objetivos", [fields["objetivo"]])

    user = dict(row)
    user.pop("password_hash", None)
    return user


@router.put("/onboarding")
def complete_onboarding(
    body: OnboardingRequest,
    current_user: dict = Depends(get_current_user),
):
    conn = get_connection()
    conn.execute(
        """
        UPDATE usuarios
        SET objetivo = ?, nivel = ?, edad = ?, peso_kg = ?, altura_cm = ?,
            dias_semana = ?, lugar = ?, preferencia_nut = ?, sexo_biologico = ?,
            onboarding_done = 1
        WHERE id = ?
        """,
        (body.objetivo, body.nivel, body.edad, body.peso_kg, body.altura_cm,
         body.dias_semana, body.lugar, body.preferencia_nut, body.sexo_biologico,
         current_user["id"]),
    )
    # Registrar peso inicial
    conn.execute(
        "INSERT INTO progreso_peso (usuario_id, peso_kg, notas) VALUES (?, ?, ?)",
        (current_user["id"], body.peso_kg, "Peso inicial en onboarding"),
    )
    conn.commit()
    row = conn.execute("SELECT * FROM usuarios WHERE id = ?", (current_user["id"],)).fetchone()
    conn.close()

    # Mismo motivo que en update_profile: el objetivo elegido en onboarding
    # es la fuente de verdad inicial del Memory Graph, sin arrastrar nada previo.
    replace_memory_graph_field(current_user["id"], "objetivos", [body.objetivo])

    user = dict(row)
    user.pop("password_hash", None)
    return {"message": "Onboarding completado.", "user": user}
