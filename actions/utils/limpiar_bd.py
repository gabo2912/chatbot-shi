#!/usr/bin/env python3
"""
limpiar_bd.py — Vacía TODAS las tablas de datos del piloto.

Deja la base limpia para empezar el piloto desde cero. Borra:
    • progreso_vocabulario   (intentos de vocabulario)
    • progreso_cuento        (fragmentos de cuento)
    • sesiones               (registros de sesión)
    • usuarios               (códigos de acceso PSH-NNN)

Las TABLAS no se eliminan, solo se vacían. La estructura queda intacta, así
que después podés correr generar_codigos.py directamente sin re-inicializar.

⚠ DESTRUCTIVO E IRREVERSIBLE. Pide confirmación escrita antes de borrar.
   Pensado para correr ANTES del piloto, cuando solo hay datos de prueba.

Uso (desde la raíz del proyecto chatbot-shi/):
    python3 -m actions.scripts.limpiar_bd
    # o:
    python3 actions/scripts/limpiar_bd.py

Flujo recomendado del piloto:
    1. python3 actions/scripts/limpiar_bd.py      ← este script (limpia todo)
    2. python3 actions/scripts/generar_codigos.py ← re-crea PSH-001..PSH-020
"""

import sys
import os

# Mismo path-injection que generar_codigos.py: agrega la carpeta actions/ al
# sys.path para que `from db import ...` funcione desde cualquier directorio.
HERE = os.path.dirname(os.path.abspath(__file__))
ACTIONS_DIR = os.path.dirname(HERE)
if ACTIONS_DIR not in sys.path:
    sys.path.insert(0, ACTIONS_DIR)

from sqlalchemy import text
from db import SessionLocal, DATABASE_URL

# Orden de borrado: hijas primero, padres después. `sesiones` tiene una FK
# a usuarios.codigo_acceso, así que usuarios va al final. Las tablas de
# progreso usan sender_id sin FK declarada, pero las ponemos primero igual.
TABLAS_EN_ORDEN = [
    "progreso_vocabulario",
    "progreso_cuento",
    "sesiones",
    "usuarios",
]


def _contar_filas(session, tabla: str) -> int:
    """Cuenta filas de una tabla. Devuelve -1 si la tabla no existe aún."""
    try:
        r = session.execute(text(f"SELECT COUNT(*) FROM {tabla}"))
        return int(r.scalar() or 0)
    except Exception:
        return -1


def _es_postgres() -> bool:
    return DATABASE_URL.startswith("postgresql") or DATABASE_URL.startswith("postgres")


def main():
    print("═" * 60)
    print("  Limpieza de base de datos — Pishico Bot")
    print("═" * 60)
    print()
    # Mostrar a qué base apunta, ocultando credenciales (todo lo previo a @)
    destino = DATABASE_URL.split("@")[-1] if "@" in DATABASE_URL else DATABASE_URL
    motor = "PostgreSQL" if _es_postgres() else "SQLite"
    print(f"  Motor:   {motor}")
    print(f"  Destino: {destino}")
    print()

    # ── Inventario previo ────────────────────────────────────────────────────
    print("  Estado actual de las tablas:")
    print(f"  {'TABLA':<24} {'FILAS':>8}")
    print(f"  {'─'*24} {'─'*8}")
    total = 0
    faltantes = []
    with SessionLocal() as session:
        for t in TABLAS_EN_ORDEN:
            n = _contar_filas(session, t)
            if n < 0:
                faltantes.append(t)
                print(f"  {t:<24} {'(no existe)':>8}")
            else:
                total += n
                print(f"  {t:<24} {n:>8}")
    print()

    if faltantes:
        print(f"  ⚠ Estas tablas no existen todavía: {', '.join(faltantes)}")
        print("    Si es una base nueva, corré init_db() o generar_codigos.py primero.")
        print()

    if total == 0 and not faltantes:
        print("  La base ya está vacía. No hay nada que borrar.")
        return

    # ── Confirmación ─────────────────────────────────────────────────────────
    print("─" * 60)
    print(f"  ⚠ Vas a BORRAR {total} fila(s) de {motor}. Esto es IRREVERSIBLE.")
    print("─" * 60)
    respuesta = input('  Escribí exactamente  BORRAR TODO  para continuar: ').strip()
    if respuesta != "BORRAR TODO":
        print("\n  Cancelado. No se tocó la base de datos.")
        return

    # ── Borrado ──────────────────────────────────────────────────────────────
    print()
    borradas = []
    with SessionLocal() as session:
        try:
            if _es_postgres():
                # TRUNCATE ... RESTART IDENTITY CASCADE: vacía, reinicia los
                # autoincrement y respeta las FK en una sola operación rápida.
                tablas_sql = ", ".join(TABLAS_EN_ORDEN)
                session.execute(
                    text(f"TRUNCATE TABLE {tablas_sql} RESTART IDENTITY CASCADE")
                )
                borradas = list(TABLAS_EN_ORDEN)
            else:
                # SQLite no soporta TRUNCATE: DELETE en orden de FK + reset de
                # la secuencia autoincrement vía sqlite_sequence.
                for t in TABLAS_EN_ORDEN:
                    if _contar_filas(session, t) >= 0:
                        session.execute(text(f"DELETE FROM {t}"))
                        session.execute(
                            text("DELETE FROM sqlite_sequence WHERE name = :t"),
                            {"t": t},
                        )
                        borradas.append(t)
            session.commit()
        except Exception as e:
            session.rollback()
            print(f"  ✗ Error durante el borrado, se revirtió todo: {e}")
            sys.exit(1)

    # ── Verificación posterior ───────────────────────────────────────────────
    print("  ✓ Borrado completado. Verificando...")
    print()
    print(f"  {'TABLA':<24} {'FILAS':>8}")
    print(f"  {'─'*24} {'─'*8}")
    with SessionLocal() as session:
        ok = True
        for t in borradas:
            n = _contar_filas(session, t)
            estado = "" if n == 0 else "  ⚠ NO VACÍA"
            if n != 0:
                ok = False
            print(f"  {t:<24} {n:>8}{estado}")
    print()
    print("─" * 60)
    if ok:
        print("  ✓ Base limpia. Próximo paso:")
        print("      python3 actions/scripts/generar_codigos.py")
    else:
        print("  ⚠ Alguna tabla no quedó vacía. Revisá antes de continuar.")
    print("─" * 60)
    print()


if __name__ == "__main__":
    main()
