"""
Inicialización completa de la base de datos SQLite para FitMind.

Tablas:
  - usuarios: perfil y objetivo del usuario
  - registro_comidas: log diario de alimentos
  - registro_ejercicios: log diario de ejercicios
  - planes_semanales: planes guardados
  - rutinas: rutinas personalizadas generadas por IA
  - progreso_peso: historial de peso corporal
  - conversations: conversaciones IA con UUID (Semana 4)
  - messages: mensajes persistidos (Semana 4)
  - ai_observability_logs: métricas IA (Observabilidad)
"""

import sqlite3
import os
from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("DB_PATH", "fitmind.db")


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.executescript("""
            -- Usuarios / Auth
            CREATE TABLE IF NOT EXISTS usuarios (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                nombre          TEXT    NOT NULL,
                email           TEXT    NOT NULL UNIQUE,
                password_hash   TEXT    NOT NULL,
                edad            INTEGER,
                peso_kg         REAL,
                altura_cm       REAL,
                sexo_biologico  TEXT,
                objetivo        TEXT,
                nivel           TEXT    DEFAULT 'principiante',
                dias_semana     INTEGER DEFAULT 3,
                lugar           TEXT    DEFAULT 'gimnasio',
                preferencia_nut TEXT    DEFAULT 'balanceada',
                onboarding_done INTEGER DEFAULT 0,
                es_admin        INTEGER DEFAULT 0,
                creado_en       TEXT    DEFAULT (datetime('now'))
            );

            -- Registro de comidas
            CREATE TABLE IF NOT EXISTS registro_comidas (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
                alimento        TEXT    NOT NULL,
                cantidad_g      REAL    NOT NULL,
                calorias        REAL    NOT NULL,
                proteinas_g     REAL    DEFAULT 0,
                carbohidratos_g REAL    DEFAULT 0,
                grasas_g        REAL    DEFAULT 0,
                fecha           TEXT    DEFAULT (date('now')),
                hora            TEXT    DEFAULT (time('now'))
            );

            -- Registro de ejercicios
            CREATE TABLE IF NOT EXISTS registro_ejercicios (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id        INTEGER NOT NULL REFERENCES usuarios(id),
                ejercicio         TEXT    NOT NULL,
                series            INTEGER DEFAULT 0,
                repeticiones      INTEGER DEFAULT 0,
                duracion_min      REAL    DEFAULT 0,
                calorias_quemadas REAL    DEFAULT 0,
                peso_usado_kg     REAL    DEFAULT 0,
                notas             TEXT,
                fecha             TEXT    DEFAULT (date('now'))
            );

            -- Planes semanales (alimenticios y de entrenamiento)
            CREATE TABLE IF NOT EXISTS planes_semanales (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
                tipo        TEXT    NOT NULL,
                contenido   TEXT    NOT NULL,
                creado_en   TEXT    DEFAULT (datetime('now'))
            );

            -- Rutinas personalizadas generadas por IA
            CREATE TABLE IF NOT EXISTS rutinas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
                nombre      TEXT    NOT NULL,
                objetivo    TEXT,
                nivel       TEXT,
                duracion_min INTEGER,
                lugar       TEXT,
                contenido   TEXT    NOT NULL,
                activa      INTEGER DEFAULT 1,
                creado_en   TEXT    DEFAULT (datetime('now'))
            );

            -- Historial de peso corporal
            CREATE TABLE IF NOT EXISTS progreso_peso (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id  INTEGER NOT NULL REFERENCES usuarios(id),
                peso_kg     REAL    NOT NULL,
                grasa_pct   REAL,
                musculo_pct REAL,
                notas       TEXT,
                fecha       TEXT    DEFAULT (date('now'))
            );

            -- SEMANA 4: Conversaciones IA con UUID persistente
            CREATE TABLE IF NOT EXISTS conversations (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    NOT NULL UNIQUE,
                user_id         INTEGER NOT NULL REFERENCES usuarios(id),
                title           TEXT    DEFAULT 'Nueva conversación',
                summary         TEXT,
                created_at      TEXT    DEFAULT (datetime('now')),
                updated_at      TEXT    DEFAULT (datetime('now'))
            );

            -- SEMANA 4: Mensajes persistidos por conversación
            -- FK con ON DELETE CASCADE (antes no existía: delete_conversation()
            -- en el router ya borra los mensajes a mano antes de borrar la
            -- conversación, pero sin esta FK cualquier otro borrado directo
            -- de una conversación dejaba mensajes huérfanos apuntando a un
            -- conversation_id inexistente).
            CREATE TABLE IF NOT EXISTS messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id TEXT    NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
                role            TEXT    NOT NULL CHECK(role IN ('system','user','assistant','tool')),
                content         TEXT    NOT NULL,
                tool_name       TEXT,
                timestamp       TEXT    DEFAULT (datetime('now'))
            );

            -- MEMORY GRAPH: perfil estructurado y vivo del usuario (memoria semántica)
            CREATE TABLE IF NOT EXISTS memory_graph (
                usuario_id          INTEGER PRIMARY KEY REFERENCES usuarios(id),
                objetivos           TEXT    DEFAULT '[]',
                restricciones       TEXT    DEFAULT '[]',
                preferencias        TEXT    DEFAULT '[]',
                patrones_detectados TEXT    DEFAULT '[]',
                eventos_clave       TEXT    DEFAULT '[]',
                actualizado_en      TEXT    DEFAULT (datetime('now'))
            );

            -- Check-in diario: sueño, ánimo, estrés — base para tools analíticas de correlación
            CREATE TABLE IF NOT EXISTS checkins (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
                horas_sueno     REAL,
                calidad_sueno   TEXT,
                animo           TEXT,
                nivel_estres    INTEGER,
                notas           TEXT,
                fecha           TEXT    DEFAULT (date('now')),
                creado_en       TEXT    DEFAULT (datetime('now'))
            );

            -- Refresh tokens con rotación (seguridad — Sección 7 del CTO review)
            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
                token_hash      TEXT    NOT NULL UNIQUE,
                revocado        INTEGER DEFAULT 0,
                creado_en       TEXT    DEFAULT (datetime('now')),
                expira_en       TEXT
            );

            -- Revocación de access tokens por jti (OTC-AUTH-04): permite invalidar
            -- un access token específico antes de su expiración natural (logout,
            -- compromiso de cuenta), algo que un JWT stateless no soporta por sí solo.
            CREATE TABLE IF NOT EXISTS revoked_access_jti (
                jti         TEXT PRIMARY KEY,
                expira_en   TEXT NOT NULL,
                revocado_en TEXT DEFAULT (datetime('now'))
            );

            -- Verificación de email en registro (OTC-AUTH-01).
            -- token_hash guarda el hash del código de 6 dígitos enviado por
            -- email (no el código en claro). "intentos" cuenta intentos
            -- fallidos de ese código puntual, para poder bloquearlo tras
            -- varios fallos (el espacio de un código numérico de 6 dígitos
            -- es chequeable por fuerza bruta si no se limita).
            CREATE TABLE IF NOT EXISTS email_verification_tokens (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER NOT NULL REFERENCES usuarios(id),
                token_hash      TEXT    NOT NULL UNIQUE,
                usado           INTEGER DEFAULT 0,
                intentos        INTEGER DEFAULT 0,
                creado_en       TEXT    DEFAULT (datetime('now')),
                expira_en       TEXT    NOT NULL
            );

            -- Auditoría firmada por herramienta ejecutada (user_id + timestamp + hash de params)
            CREATE TABLE IF NOT EXISTS tool_audit_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                usuario_id      INTEGER,
                tool_name       TEXT    NOT NULL,
                params_hash     TEXT    NOT NULL,
                timestamp       TEXT    DEFAULT (datetime('now'))
            );

            -- OBSERVABILIDAD: Logs de IA
            CREATE TABLE IF NOT EXISTS ai_observability_logs (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id          TEXT,
                user_id             INTEGER,
                timestamp           TEXT    DEFAULT (datetime('now')),
                user_prompt         TEXT,
                system_response     TEXT,
                ttft_ms             REAL,
                total_latency_ms    REAL,
                tokens_per_second   REAL,
                was_blocked         INTEGER DEFAULT 0,
                blocked_reason      TEXT,
                tools_executed      TEXT    DEFAULT '[]',
                model_used          TEXT,
                tokens_input        INTEGER DEFAULT 0,
                tokens_output       INTEGER DEFAULT 0
            );
        """)

        # Migración: agrega es_admin si la tabla usuarios ya existía sin esa columna
        # (SQLite no soporta ALTER TABLE ADD COLUMN IF NOT EXISTS)
        existing_cols = [row[1] for row in cursor.execute("PRAGMA table_info(usuarios)")]
        if "es_admin" not in existing_cols:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN es_admin INTEGER DEFAULT 0")

        # Migración: verificación de email (OTC-AUTH-01). Los usuarios ya
        # existentes en una base previa a este fix se marcan como verificados
        # para no bloquear cuentas ya en uso; solo las cuentas *nuevas* pasan
        # por el flujo de verificación de aquí en adelante.
        if "email_verificado" not in existing_cols:
            cursor.execute(
                "ALTER TABLE usuarios ADD COLUMN email_verificado INTEGER DEFAULT 0"
            )
            cursor.execute("UPDATE usuarios SET email_verificado = 1")

        # Migración: contador de intentos fallidos por código de verificación,
        # para bases creadas antes de pasar de token-link a código de 6 dígitos.
        evt_cols = [row[1] for row in cursor.execute("PRAGMA table_info(email_verification_tokens)")]
        if evt_cols and "intentos" not in evt_cols:
            cursor.execute("ALTER TABLE email_verification_tokens ADD COLUMN intentos INTEGER DEFAULT 0")

        # Migración: sexo biológico opcional, usado por calcular_calorias
        # (fórmula Mifflin-St Jeor) para no asumir siempre "hombre" (+5) por
        # defecto. Ver P0-08 / correction plan — dato opcional, se declara
        # la aproximación cuando no está presente.
        if "sexo_biologico" not in existing_cols:
            cursor.execute("ALTER TABLE usuarios ADD COLUMN sexo_biologico TEXT")

        # Índices recomendados (Sección 5 del CTO review): aceleran las
        # queries más frecuentes (por usuario, por conversación, lookups de
        # tokens) y evitan full table scans a medida que crecen las tablas.
        cursor.executescript("""
            CREATE INDEX IF NOT EXISTS idx_registro_comidas_usuario_fecha ON registro_comidas(usuario_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_registro_ejercicios_usuario_fecha ON registro_ejercicios(usuario_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_progreso_peso_usuario_fecha ON progreso_peso(usuario_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_checkins_usuario_fecha ON checkins(usuario_id, fecha);
            CREATE INDEX IF NOT EXISTS idx_conversations_user_updated ON conversations(user_id, updated_at);
            CREATE INDEX IF NOT EXISTS idx_messages_conversation_id ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_hash ON refresh_tokens(token_hash);
            CREATE INDEX IF NOT EXISTS idx_revoked_jti_expira ON revoked_access_jti(expira_en);
            CREATE INDEX IF NOT EXISTS idx_email_verif_usuario ON email_verification_tokens(usuario_id, usado);
            CREATE INDEX IF NOT EXISTS idx_tool_audit_usuario ON tool_audit_log(usuario_id, timestamp);
        """)

        conn.commit()
        conn.close()
        print("✅ Base de datos FitMind inicializada correctamente.")
    except sqlite3.Error as e:
        raise RuntimeError(f"Error al inicializar la base de datos: {e}") from e
