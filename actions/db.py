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

# Emojis por categoría para el resumen de progreso.
# Sumar entradas aquí cuando se agregue una categoría nueva al corpus.
EMOJI_CAT = {
    "naturaleza": "🌿",
    "animales":   "🦜",
    "cuerpo":     "🫀",
    "colores":    "🎨",
    "objetos":    "🏺",
    "números":    "🔢",
}

# Total de palabras por categoría — DINÁMICO desde corpus_loader.
# Antes era una constante hardcoded que se desfasaba al agregar/quitar palabras.
# Ahora se computa al cargar el módulo desde el Excel real, evitando datos viejos.
try:
    from corpus_loader import total_por_categoria as _total_por_categoria
    TOTAL_PALABRAS = _total_por_categoria()
except Exception as _e:
    logger.warning(
        "db.py: no se pudo cargar total_por_categoria desde corpus_loader (%s). "
        "Usando valores fallback estáticos.", _e
    )
    TOTAL_PALABRAS = {
        "naturaleza": 11, "animales": 13, "cuerpo": 12,
        "colores": 6, "objetos": 9, "números": 10,
    }


def _conn() -> sqlite3.Connection:
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def _columna_existe(db, tabla: str, columna: str) -> bool:
    """Devuelve True si la columna existe en la tabla."""
    rows = db.execute(f"PRAGMA table_info({tabla})").fetchall()
    return any(r["name"] == columna for r in rows)


def _migrar_columna(db, tabla: str, columna: str, definicion: str) -> None:
    """Agrega la columna si no existe (migración segura para BDs ya creadas)."""
    if not _columna_existe(db, tabla, columna):
        db.execute(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
        logger.info("DB: agregada columna %s.%s", tabla, columna)


def init_db() -> None:
    """Crea las tablas si no existen y migra columnas faltantes."""
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

        # Migración Capa 1 + 2: columnas nivel y criterios_evaluados.
        # nivel:                0=no_logra, 1=logra_con_esfuerzo, 2=logra_primer_intento
        # criterios_evaluados:  string CSV con criterios de la matriz pedagógica
        #                       (ej. "uso,ortografia" para vocabulario;
        #                            "uso,comprension" para cuento)
        _migrar_columna(db, "progreso_vocabulario", "nivel",
                        "INTEGER DEFAULT 2")
        _migrar_columna(db, "progreso_vocabulario", "criterios_evaluados",
                        "TEXT DEFAULT ''")
        _migrar_columna(db, "progreso_cuento", "nivel",
                        "INTEGER DEFAULT 2")
        _migrar_columna(db, "progreso_cuento", "criterios_evaluados",
                        "TEXT DEFAULT ''")
    logger.info("DB lista: %s", DB_PATH)


# ── Escritura ─────────────────────────────────────────────────────────────────

def registrar_intento(
    sender_id: str,
    categoria: str,
    palabra_es: str,
    palabra_shp: str,
    resultado: str,
    intentos: int = 1,
    nivel: int = 2,
    criterios: str = "uso,ortografia",
) -> None:
    """Registra un intento de respuesta en una actividad de vocabulario.

    nivel:     2=logra al primer intento, 1=logra con esfuerzo, 0=no logra
    criterios: criterios de la matriz pedagógica que el intento ejercita
               (por defecto 'uso,ortografia' para vocabulario)
    """
    try:
        with _conn() as db:
            db.execute("""
                INSERT INTO progreso_vocabulario
                    (sender_id, categoria, palabra_es, palabra_shp,
                     resultado, intentos, nivel, criterios_evaluados)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (sender_id, categoria, palabra_es, palabra_shp,
                  resultado, intentos, nivel, criterios))
    except Exception as e:
        logger.error("registrar_intento: %s", e)


def registrar_fragmento_cuento(
    sender_id: str,
    cuento_id: str,
    fragmento: int,
    respuesta_ok: bool,
    nivel: int = 2,
    criterios: str = "uso,comprension",
) -> None:
    """Registra un fragmento completado del cuento interactivo.

    nivel:     2=logra al primer intento, 1=logra con esfuerzo, 0=no logra
    criterios: criterios de la matriz pedagógica que el fragmento ejercita
               (por defecto 'uso,comprension' para cuento)
    """
    try:
        with _conn() as db:
            db.execute("""
                INSERT INTO progreso_cuento
                    (sender_id, cuento_id, fragmento, respuesta_ok,
                     nivel, criterios_evaluados)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (sender_id, cuento_id, fragmento, int(respuesta_ok),
                  nivel, criterios))
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
    Devuelve lista de dicts con: categoria, dominadas, total, porcentaje,
    y desglose por nivel: niveles = {"2": N, "1": N, "0": N}.

    El nivel se calcula sobre el MEJOR resultado por palabra (no por intento):
    si la misma palabra se evaluó varias veces, se guarda el nivel más alto
    alcanzado por el usuario.
    """
    resultado = []
    try:
        with _conn() as db:
            # Dominadas y vistas (igual que antes)
            rows = db.execute("""
                SELECT
                    categoria,
                    COUNT(DISTINCT CASE WHEN resultado='correcto' THEN palabra_es END) AS dominadas,
                    COUNT(DISTINCT palabra_es) AS vistas
                FROM progreso_vocabulario
                WHERE sender_id = ?
                GROUP BY categoria
            """, (sender_id,)).fetchall()

            # Mejor nivel por palabra (subquery), luego agrupado por categoría
            niveles_rows = db.execute("""
                SELECT
                    categoria,
                    SUM(CASE WHEN max_nivel=2 THEN 1 ELSE 0 END) AS nivel_2,
                    SUM(CASE WHEN max_nivel=1 THEN 1 ELSE 0 END) AS nivel_1,
                    SUM(CASE WHEN max_nivel=0 THEN 1 ELSE 0 END) AS nivel_0
                FROM (
                    SELECT categoria, palabra_es, MAX(nivel) AS max_nivel
                    FROM progreso_vocabulario
                    WHERE sender_id = ?
                    GROUP BY categoria, palabra_es
                )
                GROUP BY categoria
            """, (sender_id,)).fetchall()

        visto = {r["categoria"]: r for r in rows}
        niveles = {r["categoria"]: r for r in niveles_rows}

        # Iteramos sobre las categorías reales del corpus (incluyendo nuevas como "números")
        for cat in TOTAL_PALABRAS.keys():
            r = visto.get(cat)
            n = niveles.get(cat)
            dominadas = r["dominadas"] if r else 0
            total = TOTAL_PALABRAS.get(cat, 0)
            resultado.append({
                "categoria":  cat,
                "emoji":      EMOJI_CAT.get(cat, "📚"),
                "dominadas":  dominadas,
                "total":      total,
                "porcentaje": round(dominadas / total * 100) if total else 0,
                "niveles": {
                    "2": (n["nivel_2"] if n else 0) or 0,
                    "1": (n["nivel_1"] if n else 0) or 0,
                    "0": (n["nivel_0"] if n else 0) or 0,
                },
            })
    except Exception as e:
        logger.error("get_resumen_categorias: %s", e)
    return resultado


def get_resumen_cuento(sender_id: str) -> List[Dict[str, Any]]:
    """Resumen de fragmentos completados por cuento, con desglose por nivel."""
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

            niveles_rows = db.execute("""
                SELECT
                    cuento_id,
                    SUM(CASE WHEN max_nivel=2 THEN 1 ELSE 0 END) AS nivel_2,
                    SUM(CASE WHEN max_nivel=1 THEN 1 ELSE 0 END) AS nivel_1,
                    SUM(CASE WHEN max_nivel=0 THEN 1 ELSE 0 END) AS nivel_0
                FROM (
                    SELECT cuento_id, fragmento, MAX(nivel) AS max_nivel
                    FROM progreso_cuento
                    WHERE sender_id = ?
                    GROUP BY cuento_id, fragmento
                )
                GROUP BY cuento_id
            """, (sender_id,)).fetchall()

        niveles = {r["cuento_id"]: r for r in niveles_rows}
        resultado = []
        for r in rows:
            cid = r["cuento_id"]
            n = niveles.get(cid)
            d = dict(r)
            d["niveles"] = {
                "2": (n["nivel_2"] if n else 0) or 0,
                "1": (n["nivel_1"] if n else 0) or 0,
                "0": (n["nivel_0"] if n else 0) or 0,
            }
            resultado.append(d)
        return resultado
    except Exception as e:
        logger.error("get_resumen_cuento: %s", e)
        return []


# ── Inicialización automática ─────────────────────────────────────────────────
try:
    init_db()
except Exception as _init_err:
    logger.error("No se pudo inicializar la DB: %s", _init_err)