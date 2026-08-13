"""
Herramientas IA de FitMind — 18 tools especializados en fitness y nutrición.

Todos los tools retornan dict con:
  {"ok": True/False, "data": {...}, "mensaje": "..."}
"""

import sqlite3
from datetime import date, timedelta
from database.connection import get_connection


# ─── Helpers ─────────────────────────────────────────────────────────────────

def _get_user(usuario_id: int) -> dict | None:
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT * FROM usuarios WHERE id = ?", (usuario_id,)
        ).fetchone()
        conn.close()
        return dict(row) if row else None
    except Exception:
        return None


def _ok(data: dict, mensaje: str = "") -> dict:
    return {"ok": True, "data": data, "mensaje": mensaje}


def _err(msg: str) -> dict:
    return {"ok": False, "data": {}, "mensaje": msg}


def _f(user: dict | None, key: str, default):
    """Como dict.get(key, default), pero también aplica el default cuando la
    columna existe en la fila pero está en NULL (perfil incompleto). Con
    dict.get() puro, un usuario con peso_kg=NULL en DB hacía que las tools de
    cálculo (calcular_calorias, generar rutinas, etc.) explotaran con un
    TypeError al multiplicar contra None, en vez de usar un valor razonable
    por defecto."""
    if not user:
        return default
    value = user.get(key)
    return value if value is not None else default


def _safe_err(context: str, e: Exception) -> dict:
    """Como _err, pero para excepciones capturadas: el detalle real (str(e))
    SOLO se loguea en el servidor y nunca se devuelve al modelo/cliente
    (hallazgo OTC-LLM-01 — fuga de tracebacks internos vía SSE)."""
    print(f"[Tool Error] {context}: {e!r}")
    return {"ok": False, "data": {}, "mensaje": f"No se pudo completar: {context}. Intenta de nuevo."}


# ─── 1. Crear rutina personalizada ───────────────────────────────────────────

def crear_rutina_personalizada(
    usuario_id: int,
    objetivo: str,
    dias_semana: int = 3,
    nivel: str = "principiante",
    lugar: str = "gimnasio",
    duracion_min: int = 60,
) -> dict:
    """Genera y guarda una rutina personalizada basada en el perfil del usuario."""
    user = _get_user(usuario_id)
    if not user:
        return _err("Usuario no encontrado.")

    peso = _f(user, "peso_kg", 70)
    altura = _f(user, "altura_cm", 170)

    rutinas_base = {
        "bajar peso": {
            "tipo": "pérdida de grasa",
            "estructura": "HIIT + Fuerza compuesta",
            "ejercicios": [
                {"nombre": "Sentadilla goblet", "series": 4, "reps": "15", "descanso": "45s"},
                {"nombre": "Press banca", "series": 4, "reps": "12", "descanso": "60s"},
                {"nombre": "Remo con barra", "series": 4, "reps": "12", "descanso": "60s"},
                {"nombre": "Zancadas alternas", "series": 3, "reps": "12 c/lado", "descanso": "45s"},
                {"nombre": "Plancha abdominal", "series": 3, "reps": "45s", "descanso": "30s"},
                {"nombre": "Burpees", "series": 4, "reps": "10", "descanso": "60s"},
            ],
            "cardio": "15 min HIIT al final (30s sprint / 30s descanso)",
        },
        "ganar músculo": {
            "tipo": "hipertrofia",
            "estructura": "Volumen progresivo (Push/Pull/Legs o Full Body)",
            "ejercicios": [
                {"nombre": "Press banca con barra", "series": 5, "reps": "8-10", "descanso": "90s"},
                {"nombre": "Sentadilla trasera", "series": 5, "reps": "8-10", "descanso": "90s"},
                {"nombre": "Peso muerto", "series": 4, "reps": "6-8", "descanso": "120s"},
                {"nombre": "Press militar", "series": 4, "reps": "10-12", "descanso": "75s"},
                {"nombre": "Dominadas o jalón al pecho", "series": 4, "reps": "8-10", "descanso": "90s"},
                {"nombre": "Curl bíceps con barra", "series": 3, "reps": "12-15", "descanso": "60s"},
                {"nombre": "Extensión tríceps polea", "series": 3, "reps": "12-15", "descanso": "60s"},
            ],
            "cardio": "10 min caminata inclinada como calentamiento",
        },
        "mejorar resistencia": {
            "tipo": "resistencia cardiovascular",
            "estructura": "Circuito funcional + Cardio progresivo",
            "ejercicios": [
                {"nombre": "Jumping jacks", "series": 3, "reps": "30", "descanso": "30s"},
                {"nombre": "Sentadilla con salto", "series": 3, "reps": "15", "descanso": "45s"},
                {"nombre": "Mountain climbers", "series": 3, "reps": "20 c/lado", "descanso": "30s"},
                {"nombre": "Push-ups", "series": 3, "reps": "15", "descanso": "45s"},
                {"nombre": "Box step-ups", "series": 3, "reps": "12 c/lado", "descanso": "30s"},
                {"nombre": "Plancha dinámica", "series": 3, "reps": "30s", "descanso": "30s"},
            ],
            "cardio": "20-30 min cardio continuo (zona 2: 60-70% FCmax)",
        },
        "mantener peso": {
            "tipo": "mantenimiento y wellness",
            "estructura": "Fuerza moderada + Movilidad",
            "ejercicios": [
                {"nombre": "Sentadilla con mancuernas", "series": 3, "reps": "12", "descanso": "60s"},
                {"nombre": "Press banca mancuernas", "series": 3, "reps": "12", "descanso": "60s"},
                {"nombre": "Remo con mancuerna", "series": 3, "reps": "12 c/lado", "descanso": "60s"},
                {"nombre": "Hip thrust", "series": 3, "reps": "15", "descanso": "60s"},
                {"nombre": "Plancha lateral", "series": 3, "reps": "30s c/lado", "descanso": "30s"},
            ],
            "cardio": "15 min caminata + 10 min stretching",
        },
    }

    objetivo_norm = objetivo.lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o").replace("ú","u")
    mapping = {
        "bajar": "bajar peso", "perder": "bajar peso", "adelgazar": "bajar peso",
        "ganar": "ganar músculo", "musculo": "ganar músculo", "hipertrofia": "ganar músculo", "masa": "ganar músculo",
        "resistencia": "mejorar resistencia", "cardio": "mejorar resistencia",
        "mantener": "mantener peso", "wellness": "mantener peso",
    }
    objetivo_key = "ganar músculo"
    for k, v in mapping.items():
        if k in objetivo_norm:
            objetivo_key = v
            break

    base = rutinas_base.get(objetivo_key, rutinas_base["ganar músculo"])

    nombre = f"Rutina {base['tipo'].title()} — {dias_semana}x/semana"
    contenido_json = {
        "objetivo": objetivo_key,
        "nivel": nivel,
        "lugar": lugar,
        "dias_semana": dias_semana,
        "duracion_min": duracion_min,
        "estructura": base["estructura"],
        "ejercicios": base["ejercicios"],
        "cardio": base["cardio"],
        "notas": f"Rutina adaptada para nivel {nivel}. Peso actual: {peso}kg, altura: {altura}cm.",
        "progresion": "Aumenta el peso un 5% cuando puedas completar todas las series con buena técnica.",
    }

    import json
    try:
        conn = get_connection()
        cur = conn.execute(
            """
            INSERT INTO rutinas (usuario_id, nombre, objetivo, nivel, duracion_min, lugar, contenido)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (usuario_id, nombre, objetivo_key, nivel, duracion_min, lugar, json.dumps(contenido_json)),
        )
        rutina_id = cur.lastrowid
        conn.commit()
        conn.close()
    except Exception as e:
        return _safe_err("guardar rutina", e)

    return _ok({"rutina_id": rutina_id, "nombre": nombre, **contenido_json},
               f"✅ Rutina '{nombre}' creada exitosamente.")


# ─── 2. Ajustar rutina existente ─────────────────────────────────────────────

def ajustar_rutina(
    usuario_id: int,
    ajuste: str,
    rutina_id: int | None = None,
) -> dict:
    """Modifica la rutina activa del usuario según el ajuste solicitado."""
    import json
    try:
        conn = get_connection()
        if rutina_id:
            row = conn.execute(
                "SELECT * FROM rutinas WHERE id = ? AND usuario_id = ?",
                (rutina_id, usuario_id),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM rutinas WHERE usuario_id = ? AND activa = 1 ORDER BY id DESC LIMIT 1",
                (usuario_id,),
            ).fetchone()
        conn.close()

        if not row:
            return _err("No se encontró rutina activa para ajustar.")

        rutina = dict(row)
        contenido = json.loads(rutina["contenido"])
        contenido["ajuste_aplicado"] = ajuste
        contenido["notas"] = f"{contenido.get('notas', '')} | Ajuste: {ajuste}"

        conn = get_connection()
        conn.execute(
            "UPDATE rutinas SET contenido = ? WHERE id = ?",
            (json.dumps(contenido), rutina["id"]),
        )
        conn.commit()
        conn.close()

        return _ok({"rutina_id": rutina["id"], "ajuste": ajuste, "contenido": contenido},
                   f"✅ Rutina ajustada: {ajuste}")
    except Exception as e:
        return _safe_err("ajustar rutina", e)


# ─── 3. Generar plan alimenticio ─────────────────────────────────────────────

def generar_plan_alimenticio(
    usuario_id: int,
    objetivo: str = "ganar músculo",
    preferencia: str = "balanceada",
    calorias_objetivo: int | None = None,
) -> dict:
    """Genera un plan alimenticio semanal personalizado."""
    user = _get_user(usuario_id)
    peso = _f(user, "peso_kg", 70)

    # Calcular calorías si no se proporcionan
    if not calorias_objetivo:
        tmb = 1600 + (peso * 10)
        multiplicadores = {"bajar peso": 0.85, "ganar músculo": 1.15, "mantener": 1.0}
        mult = next((v for k, v in multiplicadores.items() if k in objetivo.lower()), 1.0)
        calorias_objetivo = int(tmb * mult)

    proteina_g = round(peso * 2.0)
    grasas_g = round(calorias_objetivo * 0.25 / 9)
    carbs_g = round((calorias_objetivo - proteina_g * 4 - grasas_g * 9) / 4)

    plan = {
        "calorias_diarias": calorias_objetivo,
        "macros": {"proteina_g": proteina_g, "carbohidratos_g": carbs_g, "grasas_g": grasas_g},
        "comidas_dia": [
            {
                "nombre": "Desayuno (7:00-8:00)",
                "alimentos": ["Avena con leche (80g)", f"Proteína whey (1 scoop)", "Plátano (1 mediano)", "Nueces (20g)"],
                "calorias_aprox": round(calorias_objetivo * 0.25),
            },
            {
                "nombre": "Almuerzo (12:00-13:00)",
                "alimentos": [f"Pechuga pollo (180g)", "Arroz integral (100g cocido)", "Brócoli al vapor (200g)", "Aceite oliva (1 cdta)"],
                "calorias_aprox": round(calorias_objetivo * 0.35),
            },
            {
                "nombre": "Merienda pre-entreno (16:00)",
                "alimentos": ["Yogur griego (200g)", "Frutas del bosque (100g)", "Copos de avena (40g)"],
                "calorias_aprox": round(calorias_objetivo * 0.15),
            },
            {
                "nombre": "Cena (20:00)",
                "alimentos": [f"Salmón o merluza (150g)", "Patata asada (200g)", "Ensalada verde variada", "Aguacate (½)"],
                "calorias_aprox": round(calorias_objetivo * 0.25),
            },
        ],
        "hidratacion": f"{round(peso * 0.035, 1)}L de agua al día",
        "notas": f"Plan para {objetivo}. Preferencia: {preferencia}. Ajusta porciones según hambre.",
    }

    import json
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO planes_semanales (usuario_id, tipo, contenido) VALUES (?, ?, ?)",
            (usuario_id, "alimenticio", json.dumps(plan)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass

    return _ok(plan, f"✅ Plan alimenticio generado: {calorias_objetivo} kcal/día.")


# ─── 4. Calcular calorías (TDEE) ─────────────────────────────────────────────

def calcular_calorias(
    usuario_id: int,
    nivel_actividad: str = "moderado",
) -> dict:
    """Calcula el TDEE (gasto calórico total diario) del usuario.

    CORRECCIÓN (OTC-LLM-02 / correction plan): antes, si peso/altura/edad
    venían NULL en el perfil, se rellenaban en silencio con valores
    genéricos (70kg/170cm/25a) y el resultado se presentaba como si fuera
    el TDEE real del usuario — un número fabricado que parece preciso pero
    no corresponde a nadie. Ahora se exige perfil completo: si falta un
    dato, se devuelve un error explícito en vez de inventar un número.
    """
    user = _get_user(usuario_id)
    if not user:
        return _err("Usuario no encontrado.")

    peso = user.get("peso_kg")
    altura = user.get("altura_cm")
    edad = user.get("edad")
    faltantes = [campo for campo, valor in
                 (("peso", peso), ("altura", altura), ("edad", edad)) if valor is None]
    if faltantes:
        return {
            "ok": False,
            "data": {"error_code": "PROFILE_INCOMPLETE", "campos_faltantes": faltantes},
            "mensaje": (
                "Para calcular tu TDEE con precisión me falta completar tu perfil "
                f"({', '.join(faltantes)}). Compartímelos o actualizá tu perfil y lo calculo."
            ),
        }

    objetivo = _f(user, "objetivo", "mantener peso")
    sexo = (user.get("sexo_biologico") or "").strip().lower()

    # Fórmula Mifflin-St Jeor: el término constante depende del sexo
    # biológico (+5 hombres, -161 mujeres). Si el usuario no lo indicó en su
    # perfil (campo opcional), se promedia entre ambas constantes en vez de
    # asumir "hombre" por defecto, y se declara explícitamente que es una
    # aproximación en el mensaje — nunca se presenta como un dato exacto que
    # no es.
    if sexo in ("masculino", "hombre", "m"):
        constante = 5
        aproximado = False
    elif sexo in ("femenino", "mujer", "f"):
        constante = -161
        aproximado = False
    else:
        constante = (5 + -161) / 2  # -78
        aproximado = True

    tmb = 10 * peso + 6.25 * altura - 5 * edad + constante

    factores = {
        "sedentario": 1.2,
        "ligero": 1.375,
        "moderado": 1.55,
        "activo": 1.725,
        "muy activo": 1.9,
    }
    factor = factores.get(nivel_actividad.lower(), 1.55)
    tdee = round(tmb * factor)

    ajuste = {"bajar": -500, "perder": -500, "adelgazar": -500,
              "ganar": 300, "musculo": 300, "aumentar": 300}.get(
        next((k for k in ["bajar", "perder", "adelgazar", "ganar", "musculo", "aumentar"]
              if k in objetivo.lower()), ""), 0)

    mensaje = f"✅ Tu TDEE es {tdee} kcal/día. Con tu objetivo: {tdee + ajuste} kcal/día."
    if aproximado:
        mensaje += (
            " (Aproximado: no tengo tu sexo biológico en el perfil, así que promedié "
            "la fórmula. Agrégalo en tu perfil para un cálculo más preciso.)"
        )

    return _ok({
        "tmb": round(tmb),
        "tdee": tdee,
        "calorias_objetivo": tdee + ajuste,
        "ajuste": ajuste,
        "nivel_actividad": nivel_actividad,
        "peso_kg": peso,
        "altura_cm": altura,
        "edad": edad,
        "aproximado": aproximado,
    }, mensaje)


# ─── 5. Calcular macros ───────────────────────────────────────────────────────

def calcular_macros(
    usuario_id: int,
    calorias: int | None = None,
    objetivo: str | None = None,
) -> dict:
    """Calcula la distribución óptima de macronutrientes."""
    user = _get_user(usuario_id)
    peso = _f(user, "peso_kg", 70)
    obj = objetivo or _f(user, "objetivo", "mantener peso")
    cal = calorias or 2200

    distribuciones = {
        "ganar músculo": {"proteina_pct": 30, "carbs_pct": 45, "grasas_pct": 25},
        "bajar peso": {"proteina_pct": 35, "carbs_pct": 35, "grasas_pct": 30},
        "mejorar resistencia": {"proteina_pct": 25, "carbs_pct": 50, "grasas_pct": 25},
        "mantener peso": {"proteina_pct": 30, "carbs_pct": 40, "grasas_pct": 30},
    }

    obj_key = next((k for k in distribuciones if any(w in obj.lower() for w in k.split())), "mantener peso")
    dist = distribuciones[obj_key]

    proteina_g = round((cal * dist["proteina_pct"] / 100) / 4)
    carbs_g = round((cal * dist["carbs_pct"] / 100) / 4)
    grasas_g = round((cal * dist["grasas_pct"] / 100) / 9)
    proteina_por_kg = round(proteina_g / peso, 1)

    return _ok({
        "calorias": cal,
        "objetivo": obj_key,
        "macros": {
            "proteina_g": proteina_g,
            "carbohidratos_g": carbs_g,
            "grasas_g": grasas_g,
        },
        "distribucion_pct": dist,
        "proteina_por_kg": proteina_por_kg,
        "fuentes_recomendadas": {
            "proteina": "Pollo, salmón, atún, huevos, yogur griego, whey",
            "carbohidratos": "Arroz integral, avena, patata, quinoa, frutas",
            "grasas": "Aguacate, aceite oliva, nueces, salmón, huevos",
        },
    }, f"✅ Macros: {proteina_g}g proteína / {carbs_g}g carbs / {grasas_g}g grasas.")


# ─── 6. Analizar progreso ─────────────────────────────────────────────────────

def analizar_progreso(usuario_id: int, dias: int = 30) -> dict:
    """Analiza el progreso del usuario en los últimos N días."""
    try:
        conn = get_connection()
        fecha_inicio = (date.today() - timedelta(days=dias)).isoformat()

        pesos = conn.execute(
            """
            SELECT peso_kg, fecha FROM progreso_peso
            WHERE usuario_id = ? AND fecha >= ?
            ORDER BY fecha ASC
            """,
            (usuario_id, fecha_inicio),
        ).fetchall()

        ejercicios = conn.execute(
            """
            SELECT COUNT(*) as total, SUM(calorias_quemadas) as cal_quemadas,
                   COUNT(DISTINCT fecha) as dias_entrenados
            FROM registro_ejercicios
            WHERE usuario_id = ? AND fecha >= ?
            """,
            (usuario_id, fecha_inicio),
        ).fetchone()

        comidas = conn.execute(
            """
            SELECT AVG(calorias) as cal_prom, AVG(proteinas_g) as prot_prom
            FROM registro_comidas
            WHERE usuario_id = ? AND fecha >= ?
            """,
            (usuario_id, fecha_inicio),
        ).fetchone()

        conn.close()

        pesos_list = [dict(p) for p in pesos]
        cambio_peso = 0
        if len(pesos_list) >= 2:
            cambio_peso = round(pesos_list[-1]["peso_kg"] - pesos_list[0]["peso_kg"], 1)

        return _ok({
            "periodo_dias": dias,
            "peso": {
                "registros": len(pesos_list),
                "cambio_kg": cambio_peso,
                "ultimo_peso": pesos_list[-1]["peso_kg"] if pesos_list else None,
            },
            "entrenamiento": {
                "dias_entrenados": ejercicios["dias_entrenados"] or 0,
                "sesiones_totales": ejercicios["total"] or 0,
                "calorias_quemadas": round(ejercicios["cal_quemadas"] or 0),
                "adherencia_pct": round((ejercicios["dias_entrenados"] or 0) / dias * 100),
            },
            "nutricion": {
                "calorias_promedio": round(comidas["cal_prom"] or 0),
                "proteina_promedio_g": round(comidas["prot_prom"] or 0),
            },
        }, f"✅ Análisis de {dias} días completado.")
    except Exception as e:
        return _safe_err("analizar progreso", e)


# ─── 7. Detectar estancamiento ────────────────────────────────────────────────

def detectar_estancamiento(usuario_id: int) -> dict:
    """Detecta si el usuario está en un plateau y sugiere estrategias."""
    try:
        conn = get_connection()
        pesos = conn.execute(
            """
            SELECT peso_kg FROM progreso_peso
            WHERE usuario_id = ?
            ORDER BY fecha DESC LIMIT 8
            """,
            (usuario_id,),
        ).fetchall()
        conn.close()

        if len(pesos) < 4:
            return _ok({"estancado": False, "razon": "Pocos datos para determinar estancamiento."},
                       "Necesitas más registros de peso para detectar un plateau.")

        pesos_vals = [p["peso_kg"] for p in pesos]
        variacion = max(pesos_vals) - min(pesos_vals)

        estancado = variacion < 0.5

        estrategias = []
        if estancado:
            estrategias = [
                "Implementa un deload de 1 semana (reducir volumen 40-60%)",
                "Ajusta calorías: ±200 kcal según tu objetivo actual",
                "Cambia el orden de ejercicios o introduce variantes nuevas",
                "Revisa la calidad del sueño (objetivo: 7-9 horas)",
                "Aumenta el cardio de baja intensidad (zona 2)",
                "Asegura hidratación adecuada (≥2.5L/día)",
            ]

        return _ok({
            "estancado": estancado,
            "variacion_kg": round(variacion, 2),
            "registros_analizados": len(pesos_vals),
            "estrategias": estrategias,
        }, "✅ Detección de estancamiento completada." if not estancado
           else "⚠️ Posible plateau detectado. Se recomiendan ajustes.")
    except Exception as e:
        return _safe_err("detectar estancamiento", e)


# ─── 8. Recomendar descanso ───────────────────────────────────────────────────

def recomendar_descanso(usuario_id: int) -> dict:
    """Analiza la carga de entrenamiento y recomienda estrategias de recuperación."""
    try:
        conn = get_connection()
        ejercicios_semana = conn.execute(
            """
            SELECT COUNT(DISTINCT fecha) as dias, SUM(calorias_quemadas) as calorias
            FROM registro_ejercicios
            WHERE usuario_id = ? AND fecha >= date('now', '-7 days')
            """,
            (usuario_id,),
        ).fetchone()
        conn.close()

        dias = ejercicios_semana["dias"] or 0
        cal = ejercicios_semana["calorias"] or 0

        nivel_fatiga = "bajo" if dias <= 3 else "moderado" if dias <= 5 else "alto"

        recomendaciones = {
            "bajo": [
                "Puedes entrenar mañana con normalidad.",
                "Aprovecha para trabajar movilidad y técnica.",
                "Hidratación: 2.5-3L de agua hoy.",
            ],
            "moderado": [
                "Toma 1 día de descanso activo (caminata 30 min).",
                "Prioriza 8h de sueño esta noche.",
                "Come suficiente proteína para recuperación muscular (≥1.8g/kg).",
                "Considera masaje o foam roller en grupos musculares entrenados.",
            ],
            "alto": [
                "¡Descansa hoy! Tu sistema nervioso necesita recuperación.",
                "Si entrenas, máximo movilidad o caminata suave.",
                "Aumenta calorías en 10-15% los próximos 2 días.",
                "Prioriza sueño: mínimo 8.5h.",
                "Evalúa un deload esta semana (40% menos volumen).",
            ],
        }

        return _ok({
            "dias_entrenados_semana": dias,
            "calorias_quemadas_semana": round(cal),
            "nivel_fatiga": nivel_fatiga,
            "recomendaciones": recomendaciones[nivel_fatiga],
            "sueno_recomendado_h": 8 if nivel_fatiga == "bajo" else 8.5 if nivel_fatiga == "moderado" else 9,
            "hidratacion_litros": round(0.035 * 70, 1),
        }, f"✅ Nivel de fatiga: {nivel_fatiga.upper()}.")
    except Exception as e:
        return _safe_err("recomendar descanso", e)


# ─── 9. Resolver duda fitness (RAG-backed) ────────────────────────────────────

def resolver_duda_fitness(pregunta: str) -> dict:
    """Señal para que el agente use RAG. Retorna la pregunta para procesamiento."""
    return _ok(
        {"pregunta": pregunta, "usar_rag": True},
        "Consultando base de conocimiento especializada en fitness y nutrición.",
    )


# ─── 10. Explicar ejercicio ──────────────────────────────────────────────────

def explicar_ejercicio(nombre_ejercicio: str) -> dict:
    """Retorna descripción detallada de un ejercicio: técnica, músculos, errores."""
    ejercicios = {
        "sentadilla": {
            "nombre": "Sentadilla (Squat)",
            "musculos_principales": ["Cuádriceps", "Glúteos", "Isquiotibiales"],
            "musculos_secundarios": ["Core", "Erectores espinales", "Aductores"],
            "tecnica": [
                "Pies a ancho de hombros, puntas ligeramente hacia afuera (15-30°)",
                "Bar sobre trampas (sentadilla alta) o posterior (baja)",
                "Descender manteniendo el pecho arriba y la espalda recta",
                "Rodillas siguen la dirección de los dedos del pie",
                "Bajar hasta paralelo o más profundo si la movilidad lo permite",
                "Empujar el suelo para subir, no elevar la cadera primero",
            ],
            "errores_comunes": ["Rodillas colapsando hacia adentro (valgo)", "Talones levantando del suelo", "Espalda redondeada", "Profundidad insuficiente"],
            "variantes": ["Goblet squat", "Front squat", "Box squat", "Bulgarian split squat"],
        },
        "press banca": {
            "nombre": "Press de banca (Bench Press)",
            "musculos_principales": ["Pectoral mayor", "Pectoral menor"],
            "musculos_secundarios": ["Deltoides anterior", "Tríceps braquial"],
            "tecnica": [
                "Agarre a 1.5x anchura de hombros",
                "Retracción y depresión escapular (hombros hacia abajo y atrás)",
                "Barra sobre el pecho (clavícula-nipples), no sobre la cara",
                "Descender controlado tocando ligeramente el pecho",
                "Empujar en trayectoria ligeramente diagonal (hacia arriba y atrás)",
                "Pies apoyados en el suelo durante todo el movimiento",
            ],
            "errores_comunes": ["Hombros en elevación", "Rebotar la barra en el pecho", "Codos a 90°", "Pérdida de retracción escapular"],
            "variantes": ["Incline press", "Decline press", "Dumbbell press", "Machine press"],
        },
        "peso muerto": {
            "nombre": "Peso muerto (Deadlift)",
            "musculos_principales": ["Isquiotibiales", "Glúteos", "Erectores espinales"],
            "musculos_secundarios": ["Trapecios", "Romboides", "Core", "Cuádriceps"],
            "tecnica": [
                "Barra sobre el mediopié, separación de cadera",
                "Agarre doble supino o mixto, hombros ligeramente frente a la barra",
                "Bajar caderas hasta que espinillas toquen la barra",
                "Empujar el suelo, no tirar de la barra — imagina 'separar el suelo'",
                "Espalda neutra (no redondeada ni hiperlordótica) durante todo el rango",
                "La barra roza el cuerpo en todo momento",
            ],
            "errores_comunes": ["Redondeo lumbar", "Barra separada del cuerpo", "Caderas subiendo primero", "Hiperextensión al final"],
            "variantes": ["Romanian deadlift", "Sumo deadlift", "Trap bar deadlift", "Stiff-leg deadlift"],
        },
    }

    nombre_norm = nombre_ejercicio.lower().replace("á","a").replace("é","e").replace("í","i").replace("ó","o")
    ejercicio = None
    for key, val in ejercicios.items():
        if key in nombre_norm or nombre_norm in key:
            ejercicio = val
            break

    if not ejercicio:
        ejercicio = {
            "nombre": nombre_ejercicio,
            "nota": "Ejercicio no encontrado en la base local. Pregunta al entrenador IA para una explicación detallada.",
            "musculos_principales": [],
            "musculos_secundarios": [],
            "tecnica": [],
            "errores_comunes": [],
            "variantes": [],
        }

    return _ok(ejercicio, f"✅ Explicación de {nombre_ejercicio} lista.")


# ─── 11. Recomendar ejercicio alternativo ────────────────────────────────────

def recomendar_alternativo(
    ejercicio_original: str,
    razon: str = "no disponible",
) -> dict:
    """Sugiere ejercicios alternativos según el original y la razón."""
    alternativas = {
        "sentadilla": ["Leg press", "Hack squat", "Goblet squat", "Step-ups con mancuernas"],
        "press banca": ["Press mancuernas", "Press máquina", "Flexiones", "Cable crossover"],
        "peso muerto": ["Romanian deadlift", "Hip thrust", "Good morning", "Extensiones de espalda"],
        "dominadas": ["Jalón al pecho", "Remo en máquina", "Remo con mancuerna", "Pullover"],
        "press militar": ["Press Arnold", "Elevaciones laterales", "Press máquina hombros", "Pike push-ups"],
        "curl": ["Curl máquina", "Curl cable", "Curl martillo", "Curl concentrado"],
    }

    nombre_norm = ejercicio_original.lower()
    alts = next((v for k, v in alternativas.items() if k in nombre_norm), [
        "Pregunta al coach IA para alternativas específicas de tu equipo disponible"
    ])

    return _ok({
        "ejercicio_original": ejercicio_original,
        "razon": razon,
        "alternativas": alts,
        "nota": "Selecciona el alternativo que tenga el mismo patrón de movimiento.",
    }, f"✅ Alternativas para {ejercicio_original} según disponibilidad.")


# ─── 12. Generar rutina para casa ────────────────────────────────────────────

def generar_rutina_casa(
    usuario_id: int,
    objetivo: str = "ganar músculo",
    nivel: str = "principiante",
    duracion_min: int = 45,
) -> dict:
    """Genera rutina sin equipamiento para entrenar en casa."""
    return crear_rutina_personalizada(
        usuario_id=usuario_id,
        objetivo=objetivo,
        dias_semana=3,
        nivel=nivel,
        lugar="casa",
        duracion_min=duracion_min,
    )


# ─── 13. Generar rutina para gimnasio ────────────────────────────────────────

def generar_rutina_gimnasio(
    usuario_id: int,
    objetivo: str = "ganar músculo",
    nivel: str = "intermedio",
    dias_semana: int = 4,
) -> dict:
    """Genera rutina completa para gimnasio con equipamiento."""
    return crear_rutina_personalizada(
        usuario_id=usuario_id,
        objetivo=objetivo,
        dias_semana=dias_semana,
        nivel=nivel,
        lugar="gimnasio",
        duracion_min=75,
    )


# ─── 14. Analizar historial de entrenamiento ─────────────────────────────────

def analizar_historial_entrenamiento(usuario_id: int, semanas: int = 4) -> dict:
    """Analiza el historial de entrenamiento: volumen, frecuencia, tendencias."""
    try:
        conn = get_connection()
        dias = semanas * 7
        fecha_inicio = (date.today() - timedelta(days=dias)).isoformat()

        rows = conn.execute(
            """
            SELECT ejercicio, fecha, series, repeticiones, peso_usado_kg,
                   calorias_quemadas
            FROM registro_ejercicios
            WHERE usuario_id = ? AND fecha >= ?
            ORDER BY fecha DESC
            """,
            (usuario_id, fecha_inicio),
        ).fetchall()
        conn.close()

        ejercicios = [dict(r) for r in rows]
        if not ejercicios:
            return _ok({"mensaje": "No hay entrenamientos registrados en el período.",
                        "periodo_semanas": semanas, "total_sesiones": 0},
                       "Sin datos de entrenamiento registrados aún.")

        from collections import Counter
        nombres = Counter(e["ejercicio"] for e in ejercicios)
        top_ejercicios = [{"ejercicio": k, "veces": v}
                          for k, v in nombres.most_common(5)]

        dias_unicos = len(set(e["fecha"] for e in ejercicios))
        cal_total = sum(e.get("calorias_quemadas") or 0 for e in ejercicios)

        return _ok({
            "periodo_semanas": semanas,
            "total_sesiones": dias_unicos,
            "total_ejercicios_registrados": len(ejercicios),
            "sesiones_por_semana": round(dias_unicos / semanas, 1),
            "calorias_quemadas_total": round(cal_total),
            "ejercicios_mas_frecuentes": top_ejercicios,
            "consistencia_pct": round(dias_unicos / (semanas * 3) * 100),
        }, f"✅ Análisis de {semanas} semanas completado.")
    except Exception as e:
        return _safe_err("analizar historial", e)


# ─── 15. Generar recomendaciones semanales ───────────────────────────────────

def generar_recomendaciones_semanales(usuario_id: int) -> dict:
    """Genera un plan de recomendaciones completo para la semana."""
    user = _get_user(usuario_id)
    objetivo = _f(user, "objetivo", "ganar músculo")
    dias = _f(user, "dias_semana", 3)

    distribucion = {
        2: ["Lunes: Full Body", "Jueves: Full Body"],
        3: ["Lunes: Full Body A", "Miércoles: Full Body B", "Viernes: Full Body C"],
        4: ["Lunes: Pecho/Hombros", "Martes: Espalda/Bíceps", "Jueves: Piernas", "Viernes: Core/Cardio"],
        5: ["Lunes: Pecho", "Martes: Espalda", "Miércoles: Piernas", "Jueves: Hombros", "Viernes: Brazos/Core"],
    }

    plan_semanal = distribucion.get(dias, distribucion[3])

    return _ok({
        "objetivo_semana": objetivo,
        "dias_entrenamiento": dias,
        "plan_semanal": plan_semanal,
        "recomendaciones_nutricion": [
            f"Mantén un consumo de proteína de 1.8-2.2g/kg de peso corporal",
            "Hidratación: mínimo 2.5-3L de agua al día",
            "Prioriza carbohidratos alrededor del entrenamiento (pre y post)",
            "No olvides las grasas saludables (aguacate, aceite oliva, nueces)",
        ],
        "recomendaciones_recuperacion": [
            "7-9 horas de sueño cada noche",
            "Foam roller o stretching 10 min post-entrenamiento",
            "Al menos 1 día completo de descanso por semana",
        ],
        "foco_semana": "Progresión de carga: aumenta peso o volumen en al menos un ejercicio",
    }, "✅ Recomendaciones semanales generadas.")


# ─── 16. Predecir peso futuro (BONUS) ────────────────────────────────────────

def predecir_peso_futuro(
    usuario_id: int,
    semanas: int = 12,
) -> dict:
    """Proyecta el peso futuro basado en déficit/superávit calórico histórico."""
    user = _get_user(usuario_id)
    peso_actual = _f(user, "peso_kg", 70)
    objetivo = _f(user, "objetivo", "mantener peso")

    # 0.5kg/semana pérdida o ganancia conservadora
    if "bajar" in objetivo.lower() or "perder" in objetivo.lower():
        cambio_semanal = -0.5
    elif "ganar" in objetivo.lower() or "musculo" in objetivo.lower():
        cambio_semanal = 0.25
    else:
        cambio_semanal = 0

    proyeccion = []
    peso = peso_actual
    for i in range(1, semanas + 1):
        peso = round(peso + cambio_semanal, 1)
        proyeccion.append({"semana": i, "peso_proyectado_kg": peso})

    peso_final = proyeccion[-1]["peso_proyectado_kg"]

    return _ok({
        "peso_actual": peso_actual,
        "peso_proyectado_semana": semanas,
        "peso_final_kg": peso_final,
        "cambio_total_kg": round(peso_final - peso_actual, 1),
        "cambio_semanal_kg": cambio_semanal,
        "proyeccion": proyeccion,
        "nota": "Proyección basada en tasa conservadora. Varía según adherencia real.",
    }, f"✅ Proyección para {semanas} semanas: {peso_final}kg.")


# ─── 17. Recomendar suplementos (BONUS) ──────────────────────────────────────

def recomendar_suplementos(usuario_id: int) -> dict:
    """Recomienda suplementos según el objetivo del usuario."""
    user = _get_user(usuario_id)
    objetivo = _f(user, "objetivo", "ganar músculo")
    peso = _f(user, "peso_kg", 70)

    base = [
        {"nombre": "Proteína Whey", "dosis": f"{round(peso * 0.3)}g post-entreno",
         "beneficio": "Recuperación muscular y síntesis proteica", "prioridad": "Alta"},
        {"nombre": "Creatina monohidrato", "dosis": "5g/día con agua",
         "beneficio": "Fuerza, potencia y recuperación muscular", "prioridad": "Alta"},
        {"nombre": "Vitamina D3", "dosis": "2000 UI/día con comida grasa",
         "beneficio": "Salud ósea, hormonal e inmune", "prioridad": "Media"},
        {"nombre": "Omega-3 (EPA/DHA)", "dosis": "2-3g/día con comidas",
         "beneficio": "Antiinflamatorio, salud cardiovascular y articular", "prioridad": "Media"},
    ]

    if "bajar" in objetivo.lower() or "perder" in objetivo.lower():
        base.append({"nombre": "Cafeína", "dosis": "200mg 30 min pre-entreno",
                     "beneficio": "Rendimiento, termogénesis y oxidación de grasas", "prioridad": "Media"})

    return _ok({
        "objetivo": objetivo,
        "suplementos": base,
        "advertencia": "Los suplementos complementan, no reemplazan una dieta equilibrada. Consulta a un médico si tienes condiciones de salud.",
    }, "✅ Recomendaciones de suplementación generadas.")


# ─── 18. Recomendar hidratación (BONUS) ──────────────────────────────────────

def recomendar_hidratacion(usuario_id: int) -> dict:
    """Calcula necesidades de hidratación personalizadas."""
    user = _get_user(usuario_id)
    peso = _f(user, "peso_kg", 70)

    try:
        conn = get_connection()
        hoy = date.today().isoformat()
        cal_quemadas = conn.execute(
            """
            SELECT COALESCE(SUM(calorias_quemadas), 0) as total
            FROM registro_ejercicios
            WHERE usuario_id = ? AND fecha = ?
            """,
            (usuario_id, hoy),
        ).fetchone()["total"]
        conn.close()
    except Exception:
        cal_quemadas = 0

    agua_base_L = round(peso * 0.035, 1)
    agua_ejercicio_L = round(cal_quemadas / 1000, 1)
    agua_total_L = round(agua_base_L + agua_ejercicio_L, 1)

    return _ok({
        "peso_kg": peso,
        "agua_base_L": agua_base_L,
        "agua_ejercicio_extra_L": agua_ejercicio_L,
        "agua_total_recomendada_L": agua_total_L,
        "vasos_equivalentes": round(agua_total_L / 0.25),
        "tips": [
            "Bebe 500ml al despertar antes de cualquier comida",
            "Toma 300-500ml 30 min antes de entrenar",
            "Durante el entreno: 150-200ml cada 15-20 min",
            "Orina de color amarillo claro = hidratación correcta",
        ],
    }, f"✅ Necesitas aprox. {agua_total_L}L de agua hoy.")


# ─── Registro de comidas y ejercicios (heredado del sistema original) ─────────

def registrar_comida(
    usuario_id: int,
    alimento: str,
    cantidad_g: float,
    calorias: float,
    proteinas_g: float = 0,
    carbohidratos_g: float = 0,
    grasas_g: float = 0,
) -> dict:
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO registro_comidas
            (usuario_id, alimento, cantidad_g, calorias, proteinas_g, carbohidratos_g, grasas_g)
            VALUES (?,?,?,?,?,?,?)
            """,
            (usuario_id, alimento, cantidad_g, calorias, proteinas_g, carbohidratos_g, grasas_g),
        )
        conn.commit()
        conn.close()
        return _ok({"alimento": alimento, "calorias": calorias}, f"✅ {alimento} registrado.")
    except Exception as e:
        return _safe_err("registrar comida", e)


def consultar_calorias_hoy(usuario_id: int) -> dict:
    try:
        conn = get_connection()
        row = conn.execute(
            """
            SELECT COALESCE(SUM(calorias),0) as total_cal,
                   COALESCE(SUM(proteinas_g),0) as total_prot,
                   COALESCE(SUM(carbohidratos_g),0) as total_carbs,
                   COALESCE(SUM(grasas_g),0) as total_grasas,
                   COUNT(*) as registros
            FROM registro_comidas
            WHERE usuario_id = ? AND fecha = date('now')
            """,
            (usuario_id,),
        ).fetchone()
        conn.close()
        return _ok(dict(row), f"✅ Calorías de hoy: {round(row['total_cal'])} kcal.")
    except Exception as e:
        return _safe_err("consultar calorías de hoy", e)


def registrar_ejercicio(
    usuario_id: int,
    ejercicio: str,
    series: int = 0,
    repeticiones: int = 0,
    duracion_min: float = 0,
    calorias_quemadas: float = 0,
    peso_usado_kg: float = 0,
) -> dict:
    try:
        conn = get_connection()
        conn.execute(
            """
            INSERT INTO registro_ejercicios
            (usuario_id, ejercicio, series, repeticiones, duracion_min, calorias_quemadas, peso_usado_kg)
            VALUES (?,?,?,?,?,?,?)
            """,
            (usuario_id, ejercicio, series, repeticiones, duracion_min, calorias_quemadas, peso_usado_kg),
        )
        conn.commit()
        conn.close()
        return _ok({"ejercicio": ejercicio}, f"✅ {ejercicio} registrado.")
    except Exception as e:
        return _safe_err("registrar ejercicio", e)


def consultar_ejercicios_hoy(usuario_id: int) -> dict:
    try:
        conn = get_connection()
        rows = conn.execute(
            """
            SELECT ejercicio, series, repeticiones, duracion_min,
                   calorias_quemadas, peso_usado_kg
            FROM registro_ejercicios
            WHERE usuario_id = ? AND fecha = date('now')
            """,
            (usuario_id,),
        ).fetchall()
        conn.close()
        ejercicios = [dict(r) for r in rows]
        return _ok({"ejercicios": ejercicios, "total": len(ejercicios)},
                   f"✅ {len(ejercicios)} ejercicios registrados hoy.")
    except Exception as e:
        return _safe_err("consultar ejercicios de hoy", e)


def registrar_peso(usuario_id: int, peso_kg: float, grasa_pct: float = None, notas: str = None) -> dict:
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO progreso_peso (usuario_id, peso_kg, grasa_pct, notas) VALUES (?,?,?,?)",
            (usuario_id, peso_kg, grasa_pct, notas),
        )
        conn.commit()
        conn.close()
        return _ok({"peso_kg": peso_kg}, f"✅ Peso registrado: {peso_kg}kg.")
    except Exception as e:
        return _safe_err("registrar peso", e)


# ─── Memory Graph — memoria semántica estructurada ───────────────────────────

def actualizar_memoria_usuario(usuario_id: int, campo: str, valor: str) -> dict:
    """
    Escribe un dato detectado en conversación al Memory Graph del usuario.

    Usar SIEMPRE que el usuario mencione, sin que se lo pidas explícitamente:
    un objetivo nuevo ('quiero perder grasa'), una restricción ('me duele el
    hombro derecho', 'soy intolerante a la lactosa'), una preferencia
    ('entreno mejor en la mañana', 'odio el cardio'), un patrón que detectes
    tú mismo ('salta el desayuno los lunes'), o un evento clave ('bajé 3kg
    este mes', 'dejé la rutina en abril'). campo debe ser uno de:
    'objetivos', 'restricciones', 'preferencias', 'patrones_detectados', 'eventos_clave'.
    """
    from services.memory import update_memory_graph
    campos_validos = {"objetivos", "restricciones", "preferencias", "patrones_detectados", "eventos_clave"}
    if campo not in campos_validos:
        return _err(f"Campo inválido. Usa uno de: {', '.join(campos_validos)}")
    result = update_memory_graph(usuario_id, campo, valor)
    if not result.get("ok"):
        # update_memory_graph ya devuelve un código interno seguro (nunca
        # str(e) crudo — ver OTC-LLM-05) en "error"; igual no se reenvía tal
        # cual, se traduce a un mensaje genérico consistente con el resto
        # de las tools.
        return _err("No se pudo actualizar la memoria. Intenta de nuevo.")
    return _ok({"campo": campo, "valor": valor}, f"🧠 Memoria actualizada: {campo} → {valor}")


# ─── Check-in diario (sueño, ánimo, estrés) ──────────────────────────────────

def registrar_checkin_diario(
    usuario_id: int,
    horas_sueno: float = None,
    calidad_sueno: str = None,
    animo: str = None,
    nivel_estres: int = None,
    notas: str = None,
) -> dict:
    """Registra el check-in diario de bienestar: sueño, ánimo y estrés reportado."""
    try:
        conn = get_connection()
        conn.execute(
            """INSERT INTO checkins (usuario_id, horas_sueno, calidad_sueno, animo, nivel_estres, notas)
               VALUES (?,?,?,?,?,?)""",
            (usuario_id, horas_sueno, calidad_sueno, animo, nivel_estres, notas),
        )
        conn.commit()
        conn.close()
        return _ok(
            {"horas_sueno": horas_sueno, "animo": animo},
            "✅ Check-in registrado. Gracias por contármelo.",
        )
    except Exception as e:
        return _safe_err("registrar check-in diario", e)


# ─── Tool analítica: correlaciones cruzadas (Capa 1 — Orchestrator) ──────────

def analizar_correlaciones(usuario_id: int, dias: int = 14) -> dict:
    """
    Cruza sueño × nutrición × entrenamiento × peso de los últimos N días y
    detecta patrones/correlaciones simples. Es la tool 'detective de patrones
    invisibles' — úsala cuando el usuario pregunte algo tipo '¿por qué subí de
    peso?', '¿por qué duermo mal?', '¿qué está afectando mi progreso?'.
    """
    try:
        desde = (date.today() - timedelta(days=dias)).isoformat()
        conn = get_connection()

        checkins = [dict(r) for r in conn.execute(
            "SELECT * FROM checkins WHERE usuario_id=? AND fecha>=? ORDER BY fecha",
            (usuario_id, desde),
        ).fetchall()]
        comidas = [dict(r) for r in conn.execute(
            "SELECT * FROM registro_comidas WHERE usuario_id=? AND fecha>=? ORDER BY fecha",
            (usuario_id, desde),
        ).fetchall()]
        ejercicios = [dict(r) for r in conn.execute(
            "SELECT * FROM registro_ejercicios WHERE usuario_id=? AND fecha>=? ORDER BY fecha",
            (usuario_id, desde),
        ).fetchall()]
        pesos = [dict(r) for r in conn.execute(
            "SELECT * FROM progreso_peso WHERE usuario_id=? AND fecha>=? ORDER BY fecha",
            (usuario_id, desde),
        ).fetchall()]
        conn.close()

        insights = []

        # Correlación: entrenamiento tarde vs sueño corto la misma noche
        entrenos_tarde_dias = {
            e["fecha"] for e in ejercicios
            if e.get("duracion_min", 0) and e.get("fecha")
        }
        sueno_por_fecha = {c["fecha"]: c.get("horas_sueno") for c in checkins if c.get("horas_sueno") is not None}
        cortos_post_entreno = [
            f for f in entrenos_tarde_dias
            if sueno_por_fecha.get(f) is not None and sueno_por_fecha.get(f) < 6.5
        ]
        if len(cortos_post_entreno) >= 2:
            insights.append(
                f"Detectamos {len(cortos_post_entreno)} noches con menos de 6.5h de sueño "
                f"en días que entrenaste — podría estar afectando tu recuperación."
            )

        # Tendencia de peso
        if len(pesos) >= 2:
            delta = pesos[-1]["peso_kg"] - pesos[0]["peso_kg"]
            if abs(delta) >= 0.3:
                direccion = "subiste" if delta > 0 else "bajaste"
                insights.append(f"En los últimos {dias} días {direccion} {abs(delta):.1f}kg.")

        # Calorías promedio vs objetivo implícito
        if comidas:
            total_dias = len({c["fecha"] for c in comidas}) or 1
            cal_prom = sum(c.get("calorias", 0) for c in comidas) / total_dias
            insights.append(f"Promedio de {cal_prom:.0f} kcal/día registradas en este período.")

        # Frecuencia de entrenamiento
        dias_entrenados = len({e["fecha"] for e in ejercicios})
        insights.append(f"Entrenaste {dias_entrenados} de los últimos {dias} días.")

        if not insights:
            insights.append("Aún no hay suficientes datos registrados para detectar patrones. Sigue registrando comidas, entrenamientos, peso y check-ins diarios.")

        data = {
            "periodo_dias": dias,
            "insights": insights,
            "checkins_registrados": len(checkins),
            "dias_entrenados": dias_entrenados,
            "registros_peso": len(pesos),
        }
        return _ok(data, "🔎 " + " ".join(insights))
    except Exception as e:
        return _safe_err("analizar correlaciones", e)


# ─── Tool analítica: riesgo de abandono (churn) ──────────────────────────────

def calcular_riesgo_abandono(usuario_id: int) -> dict:
    """
    Calcula un score simple de riesgo de abandono (churn) basado en días sin
    registrar actividad y caída de frecuencia de entrenamiento. Úsala para
    decidir si ajustar el tono (menos exigente, más motivacional) en vez de
    exponerla directamente al usuario como un número frío.
    """
    try:
        conn = get_connection()
        ultimo_ejercicio = conn.execute(
            "SELECT MAX(fecha) as f FROM registro_ejercicios WHERE usuario_id=?",
            (usuario_id,),
        ).fetchone()
        ultimo_checkin = conn.execute(
            "SELECT MAX(fecha) as f FROM checkins WHERE usuario_id=?",
            (usuario_id,),
        ).fetchone()

        semana_actual = conn.execute(
            "SELECT COUNT(DISTINCT fecha) as n FROM registro_ejercicios "
            "WHERE usuario_id=? AND fecha >= date('now','-7 days')",
            (usuario_id,),
        ).fetchone()["n"]
        semana_previa = conn.execute(
            "SELECT COUNT(DISTINCT fecha) as n FROM registro_ejercicios "
            "WHERE usuario_id=? AND fecha >= date('now','-14 days') AND fecha < date('now','-7 days')",
            (usuario_id,),
        ).fetchone()["n"]
        conn.close()

        dias_sin_entrenar = 999
        if ultimo_ejercicio and ultimo_ejercicio["f"]:
            dias_sin_entrenar = (date.today() - date.fromisoformat(ultimo_ejercicio["f"])).days

        score = 0
        if dias_sin_entrenar >= 10:
            score += 40
        elif dias_sin_entrenar >= 5:
            score += 20
        if semana_previa > 0 and semana_actual < semana_previa:
            score += 30
        if semana_actual == 0:
            score += 20

        nivel = "bajo" if score < 30 else "medio" if score < 60 else "alto"

        return _ok(
            {
                "score": min(score, 100),
                "nivel": nivel,
                "dias_sin_entrenar": dias_sin_entrenar,
                "entrenos_semana_actual": semana_actual,
                "entrenos_semana_previa": semana_previa,
            },
            f"Riesgo de abandono: {nivel} ({min(score, 100)}/100).",
        )
    except Exception as e:
        return _safe_err("calcular riesgo de abandono", e)
