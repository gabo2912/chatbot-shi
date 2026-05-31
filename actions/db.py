"""
db.py — Persistencia de progreso del chatbot educativo shipibo-konibo.

Tablas:
  progreso_vocabulario  — intentos por palabra (Historia 1)
  progreso_cuento       — fragmentos completados (Historia 2)

SQLite3 sin dependencias externas. El sender_id proviene del frontend
(UUID generado en localStorage del navegador).

Ubicación del archivo: actions/progress.db
"""

import sqlite3
import os
import logging
from typing import Optional, Tuple, List, Dict, Any

logger = logging.getLogger(__name__)

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.db")

# Emojis por categoría para el resumen de progreso
EMOJI_CAT = {
    "naturaleza": "🌿",
    "animales":   "🦜",
    "cuerpo":     "🫀",
    "colores":    "🎨",
    "objetos":    "🏺",
}

# Total de palabras por categoría (según corpus validado)
TOTAL_PALABRAS = {
    "naturaleza": 11,
    "animales":   13,
    "cuerpo":     12,
    "colores":     9,
    "objetos":     9,
}


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def init_db() -> None:
    """Crea las tablas si no existen. Se llama al importar el módulo."""
    with _conn() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS progreso_vocabulario (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id   TEXT    NOT NULL,
                categoria   TEXT    NOT NULL,
                palabra_es  TEXT    NOT NULL,
                palabra_shp TEXT    NOT NULL,
                resultado   TEXT    NOT NULL,   -- correcto | parcial | incorrecto
                intentos    INTEGER DEFAULT 1,
                fecha       TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE TABLE IF NOT EXISTS progreso_cuento (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id    TEXT    NOT NULL,
                cuento_id    TEXT    NOT NULL,
                fragmento    INTEGER NOT NULL,
                respuesta_ok INTEGER DEFAULT 0,  -- 1=correcto, 0=incorrecto/sin pregunta
                fecha        TEXT    DEFAULT (datetime('now','localtime'))
            );

            CREATE INDEX IF NOT EXISTS idx_vocab_sender
                ON progreso_vocabulario(sender_id, categoria);
            CREATE INDEX IF NOT EXISTS idx_cuento_sender
                ON progreso_cuento(sender_id, cuento_id);
        """)
    logger.info("DB lista: %s", DB_PATH)


# ── Escritura ─────────────────────────────────────────────────────────────────

def registrar_intento(
    sender_id: str,
    categoria: str,
    palabra_es: str,
    palabra_shp: str,
    resultado: str,
    intentos: int = 1,
) -> None:
    """Registra un intento de respuesta en una actividad de vocabulario."""
    try:
        with _conn() as db:
            db.execute("""
                INSERT INTO progreso_vocabulario
                    (sender_id, categoria, palabra_es, palabra_shp, resultado, intentos)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sender_id, categoria, palabra_es, palabra_shp, resultado, intentos))
    except Exception as e:
        logger.error("registrar_intento: %s", e)


def registrar_fragmento_cuento(
    sender_id: str,
    cuento_id: str,
    fragmento: int,
    respuesta_ok: bool,
) -> None:
    """Registra que un usuario completó/respondió un fragmento del cuento."""
    try:
        with _conn() as db:
            db.execute("""
                INSERT INTO progreso_cuento
                    (sender_id, cuento_id, fragmento, respuesta_ok)
                VALUES (?, ?, ?, ?)
            """, (sender_id, cuento_id, fragmento, int(respuesta_ok)))
    except Exception as e:
        logger.error("registrar_fragmento_cuento: %s", e)


# ── Lectura ───────────────────────────────────────────────────────────────────

def ultima_posicion(sender_id: str) -> Optional[Tuple[str, str]]:
    """
    Devuelve (categoria, palabra_es) del último intento del usuario,
    o None si no tiene historial.
    """
    try:
        with _conn() as db:
            row = db.execute("""
                SELECT categoria, palabra_es FROM progreso_vocabulario
                WHERE sender_id = ?
                ORDER BY fecha DESC LIMIT 1
            """, (sender_id,)).fetchone()
        return (row["categoria"], row["palabra_es"]) if row else None
    except Exception as e:
        logger.error("ultima_posicion: %s", e)
        return None


def ultima_posicion_cuento(sender_id: str) -> Optional[Tuple[str, int]]:
    """
    Devuelve (cuento_id, fragmento) del ultimo registro del usuario en cuento,
    o None si no tiene historial.
    """
    try:
        with _conn() as db:
            row = db.execute(
                """
                SELECT cuento_id, fragmento
                FROM progreso_cuento
                WHERE sender_id = ?
                ORDER BY fecha DESC, id DESC
                LIMIT 1
                """,
                (sender_id,),
            ).fetchone()
        return (row["cuento_id"], int(row["fragmento"])) if row else None
    except Exception as e:
        logger.error("ultima_posicion_cuento: %s", e)
        return None


def palabras_dominadas(sender_id: str, categoria: str) -> int:
    """Cuenta palabras con al menos un intento 'correcto' en la categoría."""
    try:
        with _conn() as db:
            row = db.execute("""
                SELECT COUNT(DISTINCT palabra_es) AS n
                FROM progreso_vocabulario
                WHERE sender_id = ? AND categoria = ? AND resultado = 'correcto'
            """, (sender_id, categoria)).fetchone()
        return row["n"] if row else 0
    except Exception as e:
        logger.error("palabras_dominadas: %s", e)
        return 0


def get_resumen_categorias(sender_id: str) -> List[Dict[str, Any]]:
    """
    Resumen de progreso por categoría para 'Mi Aprendizaje'.
    Devuelve lista de dicts con: categoria, dominadas, total, porcentaje.
    """
    resultado = []
    try:
        with _conn() as db:
            rows = db.execute("""
                SELECT
                    categoria,
                    COUNT(DISTINCT CASE WHEN resultado='correcto' THEN palabra_es END) AS dominadas,
                    COUNT(DISTINCT palabra_es) AS vistas
                FROM progreso_vocabulario
                WHERE sender_id = ?
                GROUP BY categoria
            """, (sender_id,)).fetchall()

        visto = {r["categoria"]: r for r in rows}
        for cat in ["naturaleza", "animales", "cuerpo", "colores", "objetos"]:
            r = visto.get(cat)
            dominadas = r["dominadas"] if r else 0
            total = TOTAL_PALABRAS.get(cat, 0)
            resultado.append({
                "categoria":  cat,
                "emoji":      EMOJI_CAT.get(cat, "📚"),
                "dominadas":  dominadas,
                "total":      total,
                "porcentaje": round(dominadas / total * 100) if total else 0,
            })
    except Exception as e:
        logger.error("get_resumen_categorias: %s", e)
    return resultado


def get_resumen_cuento(sender_id: str) -> List[Dict[str, Any]]:
    """Resumen de fragmentos completados por cuento."""
    try:
        with _conn() as db:
            rows = db.execute("""
                SELECT
                    cuento_id,
                    COUNT(DISTINCT fragmento) AS completados,
                    SUM(respuesta_ok)          AS correctas
                FROM progreso_cuento
                WHERE sender_id = ?
                GROUP BY cuento_id
            """, (sender_id,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error("get_resumen_cuento: %s", e)
        return []


# ── Inicialización automática ─────────────────────────────────────────────────
try:
    init_db()
except Exception as _init_err:
    logger.error("No se pudo inicializar la DB: %s", _init_err)
