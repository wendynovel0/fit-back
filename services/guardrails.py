"""
Guardrails — Capa de seguridad ejecutada ANTES del LLM.

Funciones:
  1. detect_prompt_injection()  — bloquea patrones de jailbreak/injection
  2. classify_fitness_domain()  — determina si la consulta pertenece al dominio fitness
  3. validate_message()         — función principal: llama ambas validaciones

Si la validación falla:
  - No se llama al LLM
  - Se registra el evento en observabilidad
  - Se retorna respuesta controlada
"""

import re
import unicodedata
from typing import Tuple

# ─── Patrones de Prompt Injection / Jailbreak (inglés) ──────────────────────

INJECTION_PATTERNS: list[re.Pattern] = [
    re.compile(r"ignore\s+(previous|prior|all)\s+(instructions?|prompts?|rules?)", re.I),
    re.compile(r"reveal\s+(system\s+)?prompt", re.I),
    re.compile(r"reveal\s+hidden\s+instructions?", re.I),
    re.compile(r"\bact\s+as\b.{0,30}\b(ChatGPT|GPT|AI|assistant|bot|human)\b", re.I),
    re.compile(r"\bassume\s+the?\s+role\b", re.I),
    re.compile(r"\bpretend\s+(you\s+are|to\s+be)\b", re.I),
    re.compile(r"\bDAN\b"),
    re.compile(r"\bjailbreak\b", re.I),
    re.compile(r"do\s+anything\s+now", re.I),
    re.compile(r"bypass\s+(restrictions?|filters?|rules?|guidelines?)", re.I),
    re.compile(r"forget\s+(your\s+)?(instructions?|rules?|training)", re.I),
    re.compile(r"new\s+(instructions?|rules?|persona|identity)", re.I),
    re.compile(r"you\s+are\s+now\s+a?\s*", re.I),
    re.compile(r"developer\s+mode", re.I),
    re.compile(r"training\s+data", re.I),
    re.compile(r"system\s+message", re.I),
    re.compile(r"\[system\]", re.I),
    re.compile(r"<system>", re.I),
    re.compile(r"###\s*(instruction|system|override)", re.I),
    re.compile(r"sudo\s+", re.I),
    re.compile(r"admin\s+(mode|access|override)", re.I),
]

# ─── Patrones de Prompt Injection / Jailbreak (español) ─────────────────────
# El detector original solo cubría inglés; en producción los usuarios escriben
# en español, así que un ataque en español pasaba sin ni siquiera necesitar
# ofuscación Unicode.

INJECTION_PATTERNS_ES: list[re.Pattern] = [
    re.compile(r"ignora\s+(todas?\s+)?(las?\s+)?(instrucciones?|reglas?|prompts?)\s+(anteriores|previas|del?\s+sistema)", re.I),
    re.compile(r"olvida\s+(tus?\s+)?(instrucciones?|reglas?|entrenamiento)", re.I),
    re.compile(r"(cu[aá]l|dime|revela|muestra|comparte|cu[eé]ntame)\s+.{0,20}\b(tu\s+)?(prompt|instrucciones?)\s+de(l)?\s+sistema", re.I),
    re.compile(r"tu\s+configuraci[oó]n\s+interna", re.I),
    re.compile(r"instrucciones?\s+(ocultas?|internas?)", re.I),
    re.compile(r"act[uú]a\s+como\b.{0,30}\b(ChatGPT|GPT|IA|asistente|humano)\b", re.I),
    re.compile(r"finge\s+(que\s+eres|ser)\b", re.I),
    re.compile(r"(a\s+partir\s+de\s+ahora|desde\s+ahora)\s+eres\b", re.I),
    re.compile(r"modo\s+desarrollador", re.I),
    re.compile(r"nuevas?\s+(instrucciones?|reglas?|identidad|personalidad)", re.I),
    re.compile(r"salt[ae]te?\s+(las?\s+)?(reglas?|restricciones?|filtros?)", re.I),
    re.compile(r"modo\s+admin(istrador)?", re.I),
    re.compile(r"datos\s+de\s+entrenamiento", re.I),
]

# ─── Keywords de dominio FITNESS (permitido) ─────────────────────────────────

FITNESS_KEYWORDS: set[str] = {
    # Entrenamiento
    "rutina", "ejercicio", "entrenamiento", "workout", "gym", "gimnasio",
    "pesas", "barra", "mancuerna", "máquina", "cardio", "fuerza", "hipertrofia",
    "músculo", "musculo", "serie", "repetición", "repeticion", "rep", "set",
    "sentadilla", "press", "remo", "jalón", "jalon", "dominada", "fondos",
    "curl", "extensión", "extension", "peso muerto", "hip thrust", "lunge",
    "plancha", "abdominales", "core", "glúteos", "gluteos", "piernas", "espalda",
    "pecho", "hombros", "bíceps", "biceps", "tríceps", "triceps", "antebrazo",
    "flexibilidad", "movilidad", "stretching", "calentamiento", "enfriamiento",
    "descanso", "recuperación", "recuperacion", "deload", "periodización",
    "periodizacion", "volumen", "intensidad", "frecuencia", "rango de movimiento",
    "técnica", "tecnica", "postura", "forma", "lesión", "lesion", "alternativo",
    # Nutrición
    "proteína", "proteina", "carbohidrato", "carb", "grasa", "macro", "micro",
    "caloría", "caloria", "kcal", "déficit", "deficit", "superávit", "superavit",
    "dieta", "comida", "alimento", "nutrición", "nutricion", "meal", "plan alimenticio",
    "ayuno", "intermitente", "bulk", "cut", "recomposición", "recomposicion",
    "bmr", "tdee", "imc", "peso", "grasa corporal", "masa muscular",
    "pollo", "arroz", "avena", "huevo", "salmón", "salmon", "atún", "atun",
    "verdura", "fruta", "legumbre", "quinoa", "pasta", "pan", "lácteo",
    "suplemento", "proteína whey", "whey", "creatina", "bcaa", "cafeína",
    "vitamina", "mineral", "hierro", "calcio", "omega", "probiótico",
    # Métricas / Progreso
    "progreso", "avance", "mejora", "resultado", "medición", "medicion",
    "báscula", "bascula", "talla", "grasa", "índice", "indice", "imc", "bmi",
    "circunferencia", "pliegue", "dexa", "registro", "historial", "racha",
    # Bienestar
    "sueño", "sueno", "sleep", "hidratación", "hidratacion", "agua", "descanso",
    "estrés", "estres", "cortisol", "hormona", "testosterona", "salud", "bienestar",
    "hábito", "habito", "rutina diaria", "estilo de vida",
    # Términos generales de consulta
    "cuántas", "cuantas", "cuánto", "cuanto", "cómo", "como", "qué", "que",
    "cuál", "cual", "necesito", "quiero", "ayuda", "recomienda", "puedo",
    "debo", "tengo", "hacer", "mejorar", "perder", "ganar", "bajar", "subir",
    "empezar", "comenzar", "plan", "objetivo", "meta", "atleta", "deportista",
    "fitness", "fit", "crossfit", "yoga", "pilates", "running", "ciclismo",
    "natación", "natacion", "funcional", "hiit", "tabata", "circuito",
}

# ─── Keywords fuera del dominio (bloqueados) ────────────────────────────────

OFF_DOMAIN_KEYWORDS: set[str] = {
    "programación", "programacion", "código", "codigo", "python", "javascript",
    "react", "css", "html", "sql", "database", "api", "framework",
    "política", "politica", "elecciones", "gobierno", "presidente",
    "religión", "religion", "dios", "iglesia", "biblia",
    "finanzas", "inversión", "inversion", "bolsa", "acciones", "crypto",
    "bitcoin", "ethereum", "nft", "trading",
    "historia", "guerra", "batalla", "napoleon", "hitler",
    "matematicas", "matemáticas", "algebra", "cálculo", "calculo", "ecuación",
    "noticias", "periódico", "periodico", "noticia", "actualidad",
    "película", "pelicula", "serie", "netflix", "videojuego", "minecraft",
    "relación sentimental", "relacion sentimental", "amor", "romance",
    "psicología clínica", "psicologia clinica", "terapeuta",
    "derecho", "ley", "abogado", "contrato", "impuesto",
}


# ─── Guardrail de "confianza médica" (Sección 7 del CTO review) ─────────────
#
# No es un clasificador clínico — es un detector de lenguaje que sugiere un
# síntoma real (no una consulta de fitness normal) para derivar a un
# profesional en vez de dejar que la IA intente diagnosticar.

MEDICAL_SYMPTOM_PATTERNS: list[re.Pattern] = [
    re.compile(r"dolor\s+(persistente|fuerte|agudo|constante|que no (se )?(quita|pasa))", re.I),
    re.compile(r"me\s+(desmay|mare)[oó]", re.I),
    re.compile(r"opresi[oó]n\s+en\s+el\s+pecho", re.I),
    re.compile(r"me\s+cuesta\s+respirar", re.I),
    re.compile(r"sangr(ado|ando|e)\s", re.I),
    re.compile(r"hormigueo\s+en\s+(el\s+)?(brazo|pierna|cara)", re.I),
    re.compile(r"perd[ií]\s+el\s+conocimiento", re.I),
    re.compile(r"s[ií]ntomas?\s+de\s+", re.I),
    re.compile(r"diagn[oó]stic", re.I),
    re.compile(r"me\s+recet[eó]|qu[eé]\s+medicamento\s+debo\s+tomar", re.I),
]


def detect_medical_concern(message: str) -> Tuple[bool, str]:
    """Detecta lenguaje de síntoma médico real (no consulta de fitness normal)."""
    normalized = _normalize(message)
    for pattern in MEDICAL_SYMPTOM_PATTERNS:
        if pattern.search(normalized):
            return True, f"Patrón médico detectado: {pattern.pattern[:40]}"
    return False, ""


def _normalize(message: str) -> str:
    """Neutraliza caracteres Unicode invisibles/de control y normaliza a NFKC.

    Esto es lo que corrige el bypass tipo "zero-width space" (U+200B) del
    reporte de pentest: sin esto, un atacante puede esconder texto malicioso
    entre caracteres invisibles y ni las regex ni el clasificador por keywords
    lo detectan porque las palabras quedan "partidas" visualmente.
    """
    # Elimina caracteres de formato/control invisibles (categoría Unicode "Cf"),
    # p.ej. zero-width space, zero-width joiner, RTL/LTR override, etc.
    cleaned = "".join(ch for ch in message if unicodedata.category(ch) != "Cf")
    return unicodedata.normalize("NFKC", cleaned)


def detect_prompt_injection(message: str) -> Tuple[bool, str]:
    """
    Detecta patrones de prompt injection / jailbreak (inglés y español).

    Returns:
        (True, reason) si se detecta un ataque, (False, "") si es seguro.
    """
    normalized = _normalize(message)
    for pattern in INJECTION_PATTERNS + INJECTION_PATTERNS_ES:
        if pattern.search(normalized):
            return True, f"Patrón de injection detectado: {pattern.pattern[:40]}"
    return False, ""


def classify_fitness_domain(message: str) -> Tuple[bool, str]:
    """
    Clasifica si el mensaje pertenece al dominio fitness/nutrición.

    Estrategia:
      1. Si contiene keywords off-domain → bloquear, SIN IMPORTAR si también
         trae una palabra de fitness mezclada. Antes, agregar una sola
         palabra como "rutina" o "proteína" al final del mensaje bastaba
         para colar cualquier tema (o una inyección) porque has_fitness
         ganaba automáticamente — ese era el bypass.
      2. Si no hay off-domain y sí hay keywords de fitness → permitir.
      3. Mensajes cortos/ambiguos o sin keywords de ningún tipo → permitir
         (beneficio de la duda).

    Returns:
        (True, "") si pertenece al dominio, (False, reason) si está fuera.
    """
    msg_lower = _normalize(message).lower()

    # Mensajes muy cortos (saludos, etc.) → permitir
    if len(message.strip()) < 20:
        return True, ""

    has_fitness = any(kw in msg_lower for kw in FITNESS_KEYWORDS)
    has_off_domain = any(kw in msg_lower for kw in OFF_DOMAIN_KEYWORDS)

    if has_off_domain:
        return False, "Consulta fuera del dominio fitness/nutrición"

    if has_fitness:
        return True, ""

    # Si no tiene keywords de ningún tipo → permitir (beneficio de la duda)
    return True, ""


def validate_message(message: str) -> Tuple[bool, str, str]:
    """
    Validación completa del mensaje antes de enviarlo al LLM.

    Returns:
        (is_valid, block_reason, response_message)
        - is_valid: True si puede pasar al LLM
        - block_reason: razón del bloqueo (vacío si es válido)
        - response_message: respuesta controlada para el usuario (vacío si es válido)
    """
    DOMAIN_REJECTION = (
        "Soy un entrenador virtual especializado en fitness, nutrición y salud física. "
        "Actualmente solo puedo ayudarte con temas relacionados con entrenamiento, "
        "alimentación, composición corporal, hábitos saludables y rendimiento físico."
    )

    INJECTION_REJECTION = (
        "No puedo procesar ese tipo de instrucción. "
        "Estoy aquí para ayudarte con tus objetivos de entrenamiento y nutrición. "
        "¿En qué puedo ayudarte con tu fitness?"
    )

    MEDICAL_REFERRAL = (
        "Eso suena a algo que merece la atención de un profesional de la salud, no de una IA "
        "de fitness. No soy médico ni puedo diagnosticar — por favor consulta a un doctor, "
        "especialmente si el síntoma persiste o empeora. En cuanto estés atendido, con gusto "
        "sigo ayudándote con tu entrenamiento y nutrición."
    )

    # 1. Detectar injection
    injected, inject_reason = detect_prompt_injection(message)
    if injected:
        return False, f"prompt_injection: {inject_reason}", INJECTION_REJECTION

    # 2. Guardrail médico — antes del clasificador genérico de dominio
    medical, medical_reason = detect_medical_concern(message)
    if medical:
        return False, f"medical_referral: {medical_reason}", MEDICAL_REFERRAL

    # 3. Clasificar dominio
    in_domain, domain_reason = classify_fitness_domain(message)
    if not in_domain:
        return False, f"off_domain: {domain_reason}", DOMAIN_REJECTION

    return True, "", ""
