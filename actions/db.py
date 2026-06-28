"""
db.py — Persistencia de progreso del chatbot educativo shipibo-konibo.

REESCRITURA HITO 1 (junio 2026):
  - SQLAlchemy ORM (antes sqlite3 directo)
  - Soporta SQLite (dev local) y PostgreSQL (producción AWS RDS)
  - Lee DATABASE_URL desde .env (con dotenv)
  - Mantiene 100% la interfaz pública existente: actions.py NO se modifica
  - Agrega tablas y funciones nuevas:
      * usuarios          — códigos de acceso PSH-NNN
      * sesiones          — duración de cada sesión por usuario
      * intentos          — registro detallado con modo y uso_pista
        (las tablas viejas progreso_vocabulario y progreso_cuento se
        conservan con columnas extendidas; intentos es vista unificada)
  - Sistema de scoring ponderado por modo (producción × 2, receptivo × 1)
  - Funciones nuevas para gestión de códigos y métricas de aprendizaje

Tablas:
  usuarios              — códigos de acceso pre-asignados (PSH-NNN)
  sesiones              — sesiones de uso (entrada/salida)
  progreso_vocabulario  — intentos en módulo Vocabulario (Historia 1)
  progreso_cuento       — fragmentos completados en cuento (Historia 2)

Variables de entorno (ver .env.example):
  DATABASE_URL              — sqlite:/// o postgresql://
  SCORING_PESO_PRODUCCION   — peso modo es→shp (default 2.0)
  SCORING_PESO_RECEPTIVO    — peso modo shp→es (default 1.0)
  SCORING_PENALIZA_PISTA    — descuento por pedir pista (default 0.5)
  SCORING_PENALIZA_ERROR    — descuento por error (default 1.0)
  SCORING_UMBRAL_DOMINADO   — score >= este valor → "dominado" (default 71)
  SCORING_UMBRAL_APRENDIENDO — score >= este valor → "aprendiendo" (default 31)
"""

import os
import re
import logging
import unicodedata
from datetime import datetime
from typing import Optional, Tuple, List, Dict, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, Text, DateTime,
    ForeignKey, Index, func, select, text, inspect,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

logger = logging.getLogger(__name__)

# ── Carga de .env (si existe) ────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    # Busca .env en la raíz del proyecto (un nivel arriba de actions/)
    _project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    load_dotenv(os.path.join(_project_root, ".env"))
except ImportError:
    logger.warning("python-dotenv no instalado; se usarán solo variables de entorno del sistema")


# ── Configuración leída de variables de entorno ─────────────────────────────
def _default_sqlite_url():
    """Path absoluto al SQLite local, para mantener compatibilidad con código viejo."""
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "progress.db")
    return f"sqlite:///{db_path}"

DATABASE_URL = os.getenv("DATABASE_URL", _default_sqlite_url())

# Pesos del sistema de scoring (con defaults razonables)
PESO_PRODUCCION   = float(os.getenv("SCORING_PESO_PRODUCCION", "2.0"))
PESO_RECEPTIVO    = float(os.getenv("SCORING_PESO_RECEPTIVO", "1.0"))
PENALIZA_PISTA    = float(os.getenv("SCORING_PENALIZA_PISTA", "0.5"))
PENALIZA_ERROR    = float(os.getenv("SCORING_PENALIZA_ERROR", "1.0"))
UMBRAL_DOMINADO   = int(os.getenv("SCORING_UMBRAL_DOMINADO", "71"))
UMBRAL_APRENDIENDO = int(os.getenv("SCORING_UMBRAL_APRENDIENDO", "31"))


# ── Emojis y totales (sin cambios respecto a versión anterior) ──────────────
EMOJI_CAT = {
    "naturaleza": "🌿",
    "animales":   "🦜",
    "cuerpo":     "🫀",
    "colores":    "🎨",
    "objetos":    "🏺",
    "números":    "🔢",
    "personas":   "👨‍👩‍👧",
}

# Carga dinámica del total por categoría desde el corpus
try:
    from corpus_loader import total_por_categoria as _total_por_categoria
    TOTAL_PALABRAS = _total_por_categoria()
except Exception as _e:
    logger.warning(
        "db.py: no se pudo cargar total_por_categoria desde corpus_loader (%s). "
        "Usando valores fallback estáticos.", _e
    )
    TOTAL_PALABRAS = {
        "naturaleza": 12, "animales": 19, "cuerpo": 12,
        "colores": 6, "objetos": 11, "números": 10, "personas": 5,
    }


# ── Engine y sesiones SQLAlchemy ─────────────────────────────────────────────
def _crear_engine():
    """Crea el engine; SQLite necesita argumentos especiales."""
    kwargs = {"future": True}
    if DATABASE_URL.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
    return create_engine(DATABASE_URL, **kwargs)

engine = _crear_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)
Base = declarative_base()


# ─────────────────────────────────────────────────────────────────────────────
# Modelos ORM
# ─────────────────────────────────────────────────────────────────────────────

class Usuario(Base):
    """Códigos de acceso pre-asignados. El código es la llave estable usada
    como sender_id en Rasa."""
    __tablename__ = "usuarios"

    id_usuario              = Column(Integer, primary_key=True, autoincrement=True)
    codigo_acceso           = Column(String(20), unique=True, nullable=False, index=True)
    nombre                  = Column(String(100), nullable=True)
    fecha_creacion          = Column(DateTime, nullable=False, default=datetime.now)
    fecha_primer_acceso     = Column(DateTime, nullable=True)
    ultimo_acceso           = Column(DateTime, nullable=True)
    activo                  = Column(Boolean, nullable=False, default=False)
    notas_investigador      = Column(Text, nullable=True)
    modo_practica_preferido = Column(String(10), nullable=True)


class Sesion(Base):
    """Una sesión de uso: cuándo entró, cuándo salió, duración."""
    __tablename__ = "sesiones"

    id_sesion       = Column(Integer, primary_key=True, autoincrement=True)
    codigo_acceso   = Column(String(20), ForeignKey("usuarios.codigo_acceso"),
                             nullable=False, index=True)
    fecha_inicio    = Column(DateTime, nullable=False, default=datetime.now)
    fecha_fin       = Column(DateTime, nullable=True)
    duracion_seg    = Column(Integer, nullable=True)


class ProgresoVocabulario(Base):
    """Intentos en módulo Vocabulario. Extiende la tabla original con
    columnas para scoring ponderado: modo, uso_pista, tiempo_ms."""
    __tablename__ = "progreso_vocabulario"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    sender_id           = Column(String(50), nullable=False, index=True)
    categoria           = Column(String(20), nullable=False)
    palabra_es          = Column(String(50), nullable=False)
    palabra_shp         = Column(String(50), nullable=False)
    resultado           = Column(String(20), nullable=False)  # correcto|parcial|incorrecto
    intentos            = Column(Integer, default=1)
    fecha               = Column(DateTime, nullable=False, default=datetime.now)
    nivel               = Column(Integer, default=2)
    criterios_evaluados = Column(Text, default="")
    # ── Columnas nuevas (hito 1) ──
    modo                = Column(String(10), nullable=True)   # es_a_shp | shp_a_es
    uso_pista           = Column(Boolean, default=False)
    tiempo_ms           = Column(Integer, nullable=True)


class ProgresoCuento(Base):
    """Fragmentos completados en cuento interactivo."""
    __tablename__ = "progreso_cuento"

    id                  = Column(Integer, primary_key=True, autoincrement=True)
    sender_id           = Column(String(50), nullable=False, index=True)
    cuento_id           = Column(String(50), nullable=False)
    fragmento           = Column(Integer, nullable=False)
    respuesta_ok        = Column(Integer, default=0)
    fecha               = Column(DateTime, nullable=False, default=datetime.now)
    nivel               = Column(Integer, default=2)
    criterios_evaluados = Column(Text, default="")
    # ── Columnas nuevas (hito 1) ──
    uso_pista           = Column(Boolean, default=False)
    tiempo_ms           = Column(Integer, nullable=True)


# Índices compuestos extra
Index("idx_vocab_sender_cat",  ProgresoVocabulario.sender_id, ProgresoVocabulario.categoria)
Index("idx_cuento_sender_cid", ProgresoCuento.sender_id, ProgresoCuento.cuento_id)


# ─────────────────────────────────────────────────────────────────────────────
# Inicialización
# ─────────────────────────────────────────────────────────────────────────────

def _columna_existe(conn, tabla: str, columna: str) -> bool:
    """True si la columna existe en la tabla. Soporta SQLite y PostgreSQL."""
    insp = inspect(conn)
    try:
        cols = {c["name"] for c in insp.get_columns(tabla)}
        return columna in cols
    except Exception:
        return False


def _migrar_columna(conn, tabla: str, columna: str, definicion: str) -> None:
    """Agrega una columna si no existe. Migración segura idempotente."""
    if not _columna_existe(conn, tabla, columna):
        try:
            conn.exec_driver_sql(f"ALTER TABLE {tabla} ADD COLUMN {columna} {definicion}")
            logger.info("DB: agregada columna %s.%s", tabla, columna)
        except Exception as e:
            logger.warning("DB: no se pudo agregar %s.%s (%s)", tabla, columna, e)


def init_db() -> None:
    """Crea tablas si no existen y migra columnas faltantes en tablas viejas.

    Soporta dos escenarios:
      1. BD vacía: create_all() crea todo con el schema nuevo
      2. BD ya existente (con schema viejo): create_all() omite tablas
         existentes; las migraciones ALTER TABLE agregan las columnas
         nuevas (modo, uso_pista, tiempo_ms) sin perder datos.
    """
    try:
        Base.metadata.create_all(engine)

        # Migraciones para BDs preexistentes con schema viejo
        with engine.begin() as conn:
            # Columnas nuevas en progreso_vocabulario
            _migrar_columna(conn, "progreso_vocabulario", "modo",      "VARCHAR(10)")
            _migrar_columna(conn, "progreso_vocabulario", "uso_pista", "BOOLEAN DEFAULT 0")
            _migrar_columna(conn, "progreso_vocabulario", "tiempo_ms", "INTEGER")
            # Columnas nuevas en progreso_cuento
            _migrar_columna(conn, "progreso_cuento",      "uso_pista", "BOOLEAN DEFAULT 0")
            _migrar_columna(conn, "progreso_cuento",      "tiempo_ms", "INTEGER")

        logger.info("DB lista: %s", DATABASE_URL.split("@")[-1])
    except Exception as e:
        logger.error("init_db: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de gestión de usuarios (códigos de acceso)
# ─────────────────────────────────────────────────────────────────────────────

def _normalizar_codigo(codigo: str) -> str:
    """Normaliza el código: mayúsculas, sin espacios, formato PSH-NNN.

    Acepta variaciones: 'psh-001', 'PSH001', ' psh 001 ', 'PSH-001'.
    Todas se normalizan a 'PSH-001'.
    """
    if not codigo:
        return ""
    # Remover espacios y pasar a mayúsculas
    c = codigo.strip().upper().replace(" ", "")
    # Match flexible: prefijo + número
    m = re.match(r"^([A-Z]+)-?(\d+)$", c)
    if not m:
        return c  # devuelve tal cual si no matchea; la validación posterior lo rechazará
    prefijo, numero = m.groups()
    return f"{prefijo}-{numero.zfill(3)}"


def crear_codigo(codigo: str, notas: str = "") -> bool:
    """Crea un código de acceso nuevo en la BD. Devuelve True si lo creó,
    False si ya existía o hubo error. Usado por scripts/generar_codigos.py."""
    codigo_norm = _normalizar_codigo(codigo)
    try:
        with SessionLocal() as session:
            existente = session.execute(
                select(Usuario).where(Usuario.codigo_acceso == codigo_norm)
            ).scalar_one_or_none()
            if existente:
                return False
            session.add(Usuario(
                codigo_acceso=codigo_norm,
                notas_investigador=notas or None,
            ))
            session.commit()
            return True
    except Exception as e:
        logger.error("crear_codigo(%s): %s", codigo, e)
        return False


def validar_codigo(codigo: str) -> bool:
    """True si el código existe en la BD (independiente de activo o no)."""
    codigo_norm = _normalizar_codigo(codigo)
    if not codigo_norm:
        return False
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(Usuario.id_usuario).where(Usuario.codigo_acceso == codigo_norm)
            ).first()
            return row is not None
    except Exception as e:
        logger.error("validar_codigo(%s): %s", codigo, e)
        return False


def activar_codigo(codigo: str, nombre: Optional[str] = None) -> bool:
    """Marca el código como activo y registra primer_acceso si no lo tiene.
    También actualiza ultimo_acceso. Devuelve True si funcionó.
    """
    codigo_norm = _normalizar_codigo(codigo)
    if not codigo_norm:
        return False
    try:
        with SessionLocal() as session:
            usuario = session.execute(
                select(Usuario).where(Usuario.codigo_acceso == codigo_norm)
            ).scalar_one_or_none()
            if not usuario:
                return False
            ahora = datetime.now()
            if usuario.fecha_primer_acceso is None:
                usuario.fecha_primer_acceso = ahora
            usuario.ultimo_acceso = ahora
            usuario.activo = True
            if nombre:
                usuario.nombre = nombre.strip()[:100]
            session.commit()
            return True
    except Exception as e:
        logger.error("activar_codigo(%s): %s", codigo, e)
        return False


def info_usuario(codigo: str) -> Optional[Dict[str, Any]]:
    """Devuelve dict con datos del usuario o None si no existe."""
    codigo_norm = _normalizar_codigo(codigo)
    try:
        with SessionLocal() as session:
            u = session.execute(
                select(Usuario).where(Usuario.codigo_acceso == codigo_norm)
            ).scalar_one_or_none()
            if not u:
                return None
            return {
                "codigo_acceso":           u.codigo_acceso,
                "nombre":                  u.nombre,
                "fecha_creacion":          u.fecha_creacion,
                "fecha_primer_acceso":     u.fecha_primer_acceso,
                "ultimo_acceso":           u.ultimo_acceso,
                "activo":                  u.activo,
                "notas_investigador":      u.notas_investigador,
                "modo_practica_preferido": u.modo_practica_preferido,
            }
    except Exception as e:
        logger.error("info_usuario(%s): %s", codigo, e)
        return None


# ── Tracking de sesiones de uso ──────────────────────────────────────────────
#
# Diseño: cada login crea una fila nueva en `sesiones` con fecha_inicio=ahora
# y fecha_fin=NULL. Cada interacción del usuario con el chatbot actualiza
# fecha_fin y duracion_seg. Cuando el usuario hace login de nuevo (otra
# sesión), se cierra automáticamente la previa con la última actividad
# conocida.
#
# Si el usuario simplemente cierra el navegador, la sesión queda con la
# fecha_fin de su última interacción, lo cual representa fielmente el tiempo
# real de uso (no necesitamos un job de cleanup).

def iniciar_sesion(codigo: str) -> Optional[int]:
    """
    Crea una sesión nueva para el usuario. La sesión más reciente previa
    (si existía y nunca tuvo actividad registrada) queda cerrada con
    duracion_seg=0 por defensividad. La actualidad de cualquier sesión
    previa con actividad ya está reflejada en su propio fecha_fin.

    Returns:
        id_sesion de la nueva sesión, o None si falló.
    """
    codigo_norm = _normalizar_codigo(codigo)
    try:
        ahora = datetime.now()
        with SessionLocal() as session:
            # Si la última sesión nunca tuvo actividad (caso raro: login
            # seguido de logout sin interacción), cerrarla con duración 0.
            ultima = session.execute(
                select(Sesion)
                .where(Sesion.codigo_acceso == codigo_norm)
                .order_by(Sesion.fecha_inicio.desc())
                .limit(1)
            ).scalar_one_or_none()
            if ultima and ultima.fecha_fin is None:
                ultima.fecha_fin = ultima.fecha_inicio
                ultima.duracion_seg = 0

            # Crear nueva sesión abierta
            nueva = Sesion(
                codigo_acceso=codigo_norm,
                fecha_inicio=ahora,
                fecha_fin=None,
                duracion_seg=None,
            )
            session.add(nueva)
            session.commit()
            session.refresh(nueva)
            return nueva.id_sesion
    except Exception as e:
        logger.error("iniciar_sesion(%s): %s", codigo, e)
        return None


def actualizar_actividad_sesion(codigo: str) -> bool:
    """
    Actualiza fecha_fin y duracion_seg de la sesión MÁS RECIENTE del
    usuario (sin importar si su fecha_fin previo era NULL o no).

    Diseño: la sesión activa siempre es la última creada para el usuario;
    cada interacción "extiende" su fecha_fin hasta el momento actual. Si
    el usuario hace login de nuevo, se crea una sesión nueva y la previa
    queda con la fecha_fin del último mensaje que envió antes del re-login.

    Returns:
        True si actualizó una sesión existente; False si no encontró
        ninguna sesión (en ese caso crea una retroactiva con duración 0).
    """
    codigo_norm = _normalizar_codigo(codigo)
    try:
        ahora = datetime.now()
        with SessionLocal() as session:
            ultima = session.execute(
                select(Sesion)
                .where(Sesion.codigo_acceso == codigo_norm)
                .order_by(Sesion.fecha_inicio.desc())
                .limit(1)
            ).scalar_one_or_none()

            if ultima:
                ultima.fecha_fin = ahora
                ultima.duracion_seg = int(
                    (ahora - ultima.fecha_inicio).total_seconds()
                )
                session.commit()
                return True

            # Caso defensivo: usuario interactúa con el bot sin haber pasado
            # por /login (puede pasar si el frontend retiene el sender_id en
            # localStorage tras reiniciar el server). Crear sesión retroactiva
            # con duración 0 para no perder el registro.
            nueva = Sesion(
                codigo_acceso=codigo_norm,
                fecha_inicio=ahora,
                fecha_fin=ahora,
                duracion_seg=0,
            )
            session.add(nueva)
            session.commit()
            return False
    except Exception as e:
        logger.error("actualizar_actividad_sesion(%s): %s", codigo, e)
        return False


def listar_usuarios() -> List[Dict[str, Any]]:
    """Devuelve todos los usuarios para reporte. Útil para el investigador."""
    try:
        with SessionLocal() as session:
            usuarios = session.execute(select(Usuario).order_by(Usuario.codigo_acceso)).scalars().all()
            return [{
                "codigo_acceso":       u.codigo_acceso,
                "nombre":              u.nombre,
                "fecha_primer_acceso": u.fecha_primer_acceso,
                "ultimo_acceso":       u.ultimo_acceso,
                "activo":              u.activo,
                "notas_investigador":  u.notas_investigador,
            } for u in usuarios]
    except Exception as e:
        logger.error("listar_usuarios: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Cierre de sesiones
# NOTA: iniciar_sesion() y actualizar_actividad_sesion() están definidas más
# arriba (sección "Tracking de sesiones de uso"). Antes existía aquí una
# segunda definición simplificada de iniciar_sesion() que, por orden de
# evaluación en Python, PISABA a la elaborada y anulaba el cierre automático
# de la sesión previa. Se eliminó para que la única iniciar_sesion vigente sea
# la que cierra la sesión anterior sin actividad. cerrar_sesion() se conserva.
# ─────────────────────────────────────────────────────────────────────────────

def cerrar_sesion(id_sesion: int) -> None:
    """Cierra una sesión registrando fecha_fin y duracion_seg."""
    try:
        with SessionLocal() as session:
            s = session.get(Sesion, id_sesion)
            if not s:
                return
            ahora = datetime.now()
            s.fecha_fin = ahora
            if s.fecha_inicio:
                s.duracion_seg = int((ahora - s.fecha_inicio).total_seconds())
            session.commit()
    except Exception as e:
        logger.error("cerrar_sesion(%s): %s", id_sesion, e)


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de escritura — INTERFAZ PÚBLICA EXISTENTE (no romper)
# ─────────────────────────────────────────────────────────────────────────────

def registrar_intento(
    sender_id: str,
    categoria: str,
    palabra_es: str,
    palabra_shp: str,
    resultado: str,
    intentos: int = 1,
    nivel: int = 2,
    criterios: str = "uso,ortografia",
    # ── Parámetros nuevos (opcionales para no romper actions.py existente) ──
    modo: Optional[str] = None,
    uso_pista: bool = False,
    tiempo_ms: Optional[int] = None,
) -> None:
    """Registra un intento en módulo Vocabulario.

    Args nuevos (opcionales):
      modo:      "es_a_shp" | "shp_a_es" (None = compatibilidad con código viejo)
      uso_pista: True si el usuario pidió pista antes de responder
      tiempo_ms: latencia entre pregunta y respuesta (opcional)
    """
    try:
        with SessionLocal() as session:
            session.add(ProgresoVocabulario(
                sender_id=sender_id,
                categoria=categoria,
                palabra_es=palabra_es,
                palabra_shp=palabra_shp,
                resultado=resultado,
                intentos=intentos,
                nivel=nivel,
                criterios_evaluados=criterios,
                modo=modo,
                uso_pista=uso_pista,
                tiempo_ms=tiempo_ms,
            ))
            session.commit()
    except Exception as e:
        logger.error("registrar_intento: %s", e)


def registrar_fragmento_cuento(
    sender_id: str,
    cuento_id: str,
    fragmento: int,
    respuesta_ok: bool,
    nivel: int = 2,
    criterios: str = "uso,comprension",
    # ── Parámetros nuevos (opcionales) ──
    uso_pista: bool = False,
    tiempo_ms: Optional[int] = None,
) -> None:
    """Registra un fragmento completado del cuento interactivo."""
    try:
        with SessionLocal() as session:
            session.add(ProgresoCuento(
                sender_id=sender_id,
                cuento_id=cuento_id,
                fragmento=fragmento,
                respuesta_ok=1 if respuesta_ok else 0,
                nivel=nivel,
                criterios_evaluados=criterios,
                uso_pista=uso_pista,
                tiempo_ms=tiempo_ms,
            ))
            session.commit()
    except Exception as e:
        logger.error("registrar_fragmento_cuento: %s", e)


# ─────────────────────────────────────────────────────────────────────────────
# Funciones de lectura — INTERFAZ PÚBLICA EXISTENTE (no romper)
# ─────────────────────────────────────────────────────────────────────────────

def ultima_posicion(sender_id: str) -> Optional[Tuple[str, str]]:
    """Devuelve (categoria, palabra_es) del último intento, o None."""
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(ProgresoVocabulario.categoria, ProgresoVocabulario.palabra_es)
                .where(ProgresoVocabulario.sender_id == sender_id)
                .order_by(ProgresoVocabulario.fecha.desc())
                .limit(1)
            ).first()
            return (row.categoria, row.palabra_es) if row else None
    except Exception as e:
        logger.error("ultima_posicion: %s", e)
        return None


def ultima_posicion_cuento(sender_id: str) -> Optional[Tuple[str, int]]:
    """Devuelve (cuento_id, fragmento) del último registro en cuento, o None."""
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(ProgresoCuento.cuento_id, ProgresoCuento.fragmento)
                .where(ProgresoCuento.sender_id == sender_id)
                .order_by(ProgresoCuento.fecha.desc(), ProgresoCuento.id.desc())
                .limit(1)
            ).first()
            return (row.cuento_id, int(row.fragmento)) if row else None
    except Exception as e:
        logger.error("ultima_posicion_cuento: %s", e)
        return None


def palabras_dominadas(sender_id: str, categoria: str) -> int:
    """Cuenta palabras con al menos un intento 'correcto' en la categoría."""
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(func.count(func.distinct(ProgresoVocabulario.palabra_es)))
                .where(ProgresoVocabulario.sender_id == sender_id)
                .where(ProgresoVocabulario.categoria == categoria)
                .where(ProgresoVocabulario.resultado == "correcto")
            ).scalar()
            return int(row or 0)
    except Exception as e:
        logger.error("palabras_dominadas: %s", e)
        return 0


def ultima_palabra_en_categoria(sender_id: str, categoria: str) -> Optional[str]:
    """Última palabra (en español) practicada en una categoría dada."""
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(ProgresoVocabulario.palabra_es)
                .where(ProgresoVocabulario.sender_id == sender_id)
                .where(ProgresoVocabulario.categoria == categoria)
                .order_by(ProgresoVocabulario.fecha.desc(),
                          ProgresoVocabulario.id.desc())
                .limit(1)
            ).first()
            return row.palabra_es if row else None
    except Exception as e:
        logger.error("ultima_palabra_en_categoria: %s", e)
        return None


def ultimo_fragmento_acertado(sender_id: str, cuento_id: str) -> Optional[int]:
    """Devuelve el índice (0-based) del último fragmento acertado del cuento."""
    try:
        with SessionLocal() as session:
            row = session.execute(
                select(func.max(ProgresoCuento.fragmento))
                .where(ProgresoCuento.sender_id == sender_id)
                .where(ProgresoCuento.cuento_id == cuento_id)
                .where(ProgresoCuento.respuesta_ok == 1)
            ).scalar()
            return int(row) if row is not None else None
    except Exception as e:
        logger.error("ultimo_fragmento_acertado: %s", e)
        return None


def get_resumen_categorias(sender_id: str) -> List[Dict[str, Any]]:
    """Resumen de progreso por categoría para 'Mi Aprendizaje'."""
    resultado = []
    try:
        with SessionLocal() as session:
            # Dominadas y vistas
            rows = session.execute(text("""
                SELECT
                    categoria,
                    COUNT(DISTINCT CASE WHEN resultado='correcto' THEN palabra_es END) AS dominadas,
                    COUNT(DISTINCT palabra_es) AS vistas
                FROM progreso_vocabulario
                WHERE sender_id = :sid
                GROUP BY categoria
            """), {"sid": sender_id}).mappings().all()

            # Mejor nivel por palabra
            niveles_rows = session.execute(text("""
                SELECT
                    categoria,
                    SUM(CASE WHEN max_nivel=2 THEN 1 ELSE 0 END) AS nivel_2,
                    SUM(CASE WHEN max_nivel=1 THEN 1 ELSE 0 END) AS nivel_1,
                    SUM(CASE WHEN max_nivel=0 THEN 1 ELSE 0 END) AS nivel_0
                FROM (
                    SELECT categoria, palabra_es, MAX(nivel) AS max_nivel
                    FROM progreso_vocabulario
                    WHERE sender_id = :sid
                    GROUP BY categoria, palabra_es
                ) AS sub
                GROUP BY categoria
            """), {"sid": sender_id}).mappings().all()

        visto = {r["categoria"]: r for r in rows}
        niveles = {r["categoria"]: r for r in niveles_rows}

        for cat in TOTAL_PALABRAS.keys():
            r = visto.get(cat)
            n = niveles.get(cat)
            dominadas = (r["dominadas"] if r else 0) or 0
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
        with SessionLocal() as session:
            rows = session.execute(text("""
                SELECT
                    cuento_id,
                    COUNT(DISTINCT fragmento) AS completados,
                    SUM(respuesta_ok)          AS correctas
                FROM progreso_cuento
                WHERE sender_id = :sid
                GROUP BY cuento_id
            """), {"sid": sender_id}).mappings().all()

            niveles_rows = session.execute(text("""
                SELECT
                    cuento_id,
                    SUM(CASE WHEN max_nivel=2 THEN 1 ELSE 0 END) AS nivel_2,
                    SUM(CASE WHEN max_nivel=1 THEN 1 ELSE 0 END) AS nivel_1,
                    SUM(CASE WHEN max_nivel=0 THEN 1 ELSE 0 END) AS nivel_0
                FROM (
                    SELECT cuento_id, fragmento, MAX(nivel) AS max_nivel
                    FROM progreso_cuento
                    WHERE sender_id = :sid
                    GROUP BY cuento_id, fragmento
                ) AS sub
                GROUP BY cuento_id
            """), {"sid": sender_id}).mappings().all()

        niveles = {r["cuento_id"]: r for r in niveles_rows}
        resultado = []
        for r in rows:
            cid = r["cuento_id"]
            n = niveles.get(cid)
            resultado.append({
                "cuento_id":    cid,
                "completados":  r["completados"] or 0,
                "correctas":    r["correctas"] or 0,
                "niveles": {
                    "2": (n["nivel_2"] if n else 0) or 0,
                    "1": (n["nivel_1"] if n else 0) or 0,
                    "0": (n["nivel_0"] if n else 0) or 0,
                },
            })
        return resultado
    except Exception as e:
        logger.error("get_resumen_cuento: %s", e)
        return []


# ─────────────────────────────────────────────────────────────────────────────
# Sistema de scoring ponderado (hito 1)
# ─────────────────────────────────────────────────────────────────────────────

def calcular_score_palabra(sender_id: str, palabra_es: str) -> Dict[str, Any]:
    """
    Calcula el score ponderado de una palabra para un usuario dado.

    Fórmula:
      score = (aciertos_produccion × PESO_PRODUCCION
               + aciertos_receptivo × PESO_RECEPTIVO
               - usos_pista × PENALIZA_PISTA
               - errores × PENALIZA_ERROR) / max_teorico × 100

    El max_teorico asume que TODOS los intentos fueron producción correcta
    sin pista (es el escenario óptimo). Esto produce un score entre 0 y 100.

    Returns dict con:
      score:      float 0-100
      estado:     "nuevo" | "aprendiendo" | "dominado"
      total_intentos: int
      aciertos_es_shp, aciertos_shp_es, errores, usos_pista
    """
    try:
        with SessionLocal() as session:
            rows = session.execute(
                select(
                    ProgresoVocabulario.resultado,
                    ProgresoVocabulario.modo,
                    ProgresoVocabulario.uso_pista,
                )
                .where(ProgresoVocabulario.sender_id == sender_id)
                .where(ProgresoVocabulario.palabra_es == palabra_es)
            ).all()

        aciertos_produccion = 0
        aciertos_receptivo = 0
        errores = 0
        usos_pista = 0

        for r in rows:
            es_correcto = r.resultado == "correcto"
            if r.uso_pista:
                usos_pista += 1
            if es_correcto:
                if r.modo == "es_a_shp":
                    aciertos_produccion += 1
                elif r.modo == "shp_a_es":
                    aciertos_receptivo += 1
                else:
                    # Sin modo definido (datos viejos): cuenta como producción
                    # con menor peso por incertidumbre
                    aciertos_receptivo += 1
            elif r.resultado == "incorrecto":
                errores += 1

        puntos = (
            aciertos_produccion * PESO_PRODUCCION
            + aciertos_receptivo * PESO_RECEPTIVO
            - usos_pista * PENALIZA_PISTA
            - errores * PENALIZA_ERROR
        )

        total_intentos = len(rows)
        # Max teórico: si todos los intentos hubieran sido producción correcta sin pista
        max_teorico = max(total_intentos, 1) * PESO_PRODUCCION
        score = max(0.0, min(100.0, (puntos / max_teorico) * 100)) if max_teorico else 0

        if score >= UMBRAL_DOMINADO:
            estado = "dominado"
        elif score >= UMBRAL_APRENDIENDO:
            estado = "aprendiendo"
        else:
            estado = "nuevo"

        return {
            "score":               round(score, 1),
            "estado":              estado,
            "total_intentos":      total_intentos,
            "aciertos_es_shp":     aciertos_produccion,
            "aciertos_shp_es":     aciertos_receptivo,
            "errores":             errores,
            "usos_pista":          usos_pista,
        }
    except Exception as e:
        logger.error("calcular_score_palabra(%s, %s): %s", sender_id, palabra_es, e)
        return {"score": 0, "estado": "nuevo", "total_intentos": 0,
                "aciertos_es_shp": 0, "aciertos_shp_es": 0, "errores": 0, "usos_pista": 0}


def dominio_global(sender_id: str) -> Dict[str, Any]:
    """Calcula métricas agregadas de dominio para un usuario.

    Returns dict con:
      score_promedio:   float (promedio de scores de todas las palabras vistas)
      palabras_dominadas, palabras_aprendiendo, palabras_nuevas: int
      total_intentos:   int
      tasa_acierto_es_shp, tasa_acierto_shp_es: float (0-100)
      tasa_uso_pista:   float (0-100)
    """
    try:
        with SessionLocal() as session:
            palabras_unicas = session.execute(
                select(ProgresoVocabulario.palabra_es)
                .where(ProgresoVocabulario.sender_id == sender_id)
                .distinct()
            ).scalars().all()

            todos = session.execute(
                select(
                    ProgresoVocabulario.resultado,
                    ProgresoVocabulario.modo,
                    ProgresoVocabulario.uso_pista,
                )
                .where(ProgresoVocabulario.sender_id == sender_id)
            ).all()

        dominadas = aprendiendo = nuevas = 0
        suma_score = 0
        for palabra in palabras_unicas:
            info = calcular_score_palabra(sender_id, palabra)
            suma_score += info["score"]
            if info["estado"] == "dominado":
                dominadas += 1
            elif info["estado"] == "aprendiendo":
                aprendiendo += 1
            else:
                nuevas += 1

        # Tasas por modo
        n_es_shp = sum(1 for r in todos if r.modo == "es_a_shp")
        n_shp_es = sum(1 for r in todos if r.modo == "shp_a_es")
        ok_es_shp = sum(1 for r in todos if r.modo == "es_a_shp" and r.resultado == "correcto")
        ok_shp_es = sum(1 for r in todos if r.modo == "shp_a_es" and r.resultado == "correcto")
        n_pista = sum(1 for r in todos if r.uso_pista)
        n_total = len(todos)

        return {
            "score_promedio":         round(suma_score / max(len(palabras_unicas), 1), 1),
            "palabras_dominadas":     dominadas,
            "palabras_aprendiendo":   aprendiendo,
            "palabras_nuevas":        nuevas,
            "palabras_vistas":        len(palabras_unicas),
            "total_intentos":         n_total,
            "tasa_acierto_es_shp":    round(ok_es_shp / n_es_shp * 100, 1) if n_es_shp else 0,
            "tasa_acierto_shp_es":    round(ok_shp_es / n_shp_es * 100, 1) if n_shp_es else 0,
            "tasa_uso_pista":         round(n_pista / n_total * 100, 1) if n_total else 0,
        }
    except Exception as e:
        logger.error("dominio_global(%s): %s", sender_id, e)
        return {"score_promedio": 0, "palabras_dominadas": 0, "palabras_aprendiendo": 0,
                "palabras_nuevas": 0, "palabras_vistas": 0, "total_intentos": 0,
                "tasa_acierto_es_shp": 0, "tasa_acierto_shp_es": 0, "tasa_uso_pista": 0}


def get_resumen_scoring_completo(sender_id: str) -> Dict[str, Any]:
    """
    Devuelve el desglose ponderado completo del usuario para el panel
    'Mi Aprendizaje', integrando dominio_global() con un breakdown por
    categoría.

    Returns dict con:
      global: salida de dominio_global() — métricas agregadas
      por_categoria: lista de dicts con:
        categoria, score_promedio, dominadas, aprendiendo, nuevas,
        producciones_ok, receptivos_ok, errores, usos_pista,
        n_palabras (palabras únicas vistas en la categoría)

    Notas de implementación:
      - Para cada palabra vista se llama a calcular_score_palabra() una vez.
        Esto es O(N) por número de palabras únicas vistas (~60 en uso típico),
        manejable para el endpoint /progreso.
      - Si la DB está vacía o falla, devuelve estructura vacía coherente
        para no romper el frontend.
    """
    glob = dominio_global(sender_id)
    por_categoria_list: List[Dict[str, Any]] = []

    try:
        with SessionLocal() as session:
            # Conteos crudos por (categoria, palabra) — base del breakdown
            rows = session.execute(text("""
                SELECT
                    categoria,
                    palabra_es,
                    SUM(CASE WHEN resultado='correcto' AND modo='es_a_shp' THEN 1 ELSE 0 END) AS p_ok,
                    SUM(CASE WHEN resultado='correcto' AND modo='shp_a_es' THEN 1 ELSE 0 END) AS r_ok,
                    SUM(CASE WHEN resultado='incorrecto' THEN 1 ELSE 0 END) AS errores,
                    SUM(CASE WHEN uso_pista THEN 1 ELSE 0 END) AS pistas
                FROM progreso_vocabulario
                WHERE sender_id = :sid
                GROUP BY categoria, palabra_es
            """), {"sid": sender_id}).mappings().all()

        # Agregar por categoría
        acum: Dict[str, Dict[str, Any]] = {}
        for r in rows:
            cat = r["categoria"]
            if cat not in acum:
                acum[cat] = {
                    "producciones_ok": 0, "receptivos_ok": 0,
                    "errores": 0, "usos_pista": 0,
                    "scores": [],
                }
            # Score ponderado por palabra (reusa la fórmula oficial)
            info_score = calcular_score_palabra(sender_id, r["palabra_es"])
            acum[cat]["producciones_ok"] += int(r["p_ok"] or 0)
            acum[cat]["receptivos_ok"]   += int(r["r_ok"] or 0)
            acum[cat]["errores"]         += int(r["errores"] or 0)
            acum[cat]["usos_pista"]      += int(r["pistas"] or 0)
            acum[cat]["scores"].append(info_score["score"])

        # Calcular promedios y estados por categoría
        for cat, agg in acum.items():
            scores = agg["scores"]
            n = len(scores)
            n_dom = sum(1 for s in scores if s >= UMBRAL_DOMINADO)
            n_apr = sum(1 for s in scores if UMBRAL_APRENDIENDO <= s < UMBRAL_DOMINADO)
            n_nue = sum(1 for s in scores if s < UMBRAL_APRENDIENDO)
            por_categoria_list.append({
                "categoria":       cat,
                "score_promedio":  round(sum(scores) / n, 1) if n else 0,
                "n_palabras":      n,
                "dominadas":       n_dom,
                "aprendiendo":     n_apr,
                "nuevas":          n_nue,
                "producciones_ok": agg["producciones_ok"],
                "receptivos_ok":   agg["receptivos_ok"],
                "errores":         agg["errores"],
                "usos_pista":      agg["usos_pista"],
            })

        # Ordenar para presentación estable (alfabético)
        por_categoria_list.sort(key=lambda x: x["categoria"])
    except Exception as e:
        logger.error("get_resumen_scoring_completo(%s): %s", sender_id, e)

    # Pesos efectivos (útil para que el frontend muestre la fórmula)
    pesos = {
        "produccion":      PESO_PRODUCCION,
        "receptivo":       PESO_RECEPTIVO,
        "penaliza_pista":  PENALIZA_PISTA,
        "penaliza_error":  PENALIZA_ERROR,
        "umbral_dominado": UMBRAL_DOMINADO,
        "umbral_aprendiendo": UMBRAL_APRENDIENDO,
    }

    return {
        "global":         glob,
        "por_categoria":  por_categoria_list,
        "pesos":          pesos,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Inicialización automática al importar (resiliente)
# ─────────────────────────────────────────────────────────────────────────────
try:
    init_db()
except Exception as _init_err:
    logger.error("No se pudo inicializar la DB: %s", _init_err)