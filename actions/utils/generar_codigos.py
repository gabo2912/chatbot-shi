#!/usr/bin/env python3
"""
generar_codigos.py — Crea códigos de acceso pre-asignados.

Genera N códigos con formato PSH-NNN y los inserta en la tabla `usuarios`.
Los códigos quedan con `activo=False` hasta que un usuario ingrese por
primera vez con ese código.

Uso (desde la raíz del proyecto chatbot-shi/):
    python3 -m actions.scripts.generar_codigos
    # o:
    python3 actions/scripts/generar_codigos.py

Configurar al inicio del archivo:
    CANTIDAD = 20         # cuántos códigos generar
    PREFIJO  = "PSH"      # prefijo (Pishico)
    DESDE    = 1          # número inicial (001)
"""

import sys
import os

# Permitir ejecutar desde cualquier directorio: agrega la carpeta `actions/`
# al sys.path para que `from db import ...` funcione.
HERE = os.path.dirname(os.path.abspath(__file__))
ACTIONS_DIR = os.path.dirname(HERE)
if ACTIONS_DIR not in sys.path:
    sys.path.insert(0, ACTIONS_DIR)

from db import crear_codigo, listar_usuarios


# ─── Configuración ────────────────────────────────────────────────────────────
CANTIDAD = 20        # cantidad de códigos a generar
PREFIJO  = "PSH"     # prefijo legible: PSH = Pishico
DESDE    = 1         # número inicial


def main():
    print("═" * 60)
    print("  Generador de códigos de acceso para Pishico Bot")
    print("═" * 60)
    print()
    print(f"  Prefijo:           {PREFIJO}")
    print(f"  Cantidad a crear:  {CANTIDAD}")
    print(f"  Rango:             {PREFIJO}-{DESDE:03d}  hasta  {PREFIJO}-{DESDE+CANTIDAD-1:03d}")
    print()

    creados = []
    ya_existentes = []
    for i in range(DESDE, DESDE + CANTIDAD):
        codigo = f"{PREFIJO}-{i:03d}"
        if crear_codigo(codigo, notas=""):
            creados.append(codigo)
        else:
            ya_existentes.append(codigo)

    print(f"✓ Códigos nuevos creados: {len(creados)}")
    if ya_existentes:
        print(f"⚠ Códigos ya existentes (omitidos): {len(ya_existentes)}")
        for c in ya_existentes:
            print(f"     - {c}")

    print()
    print("─" * 60)
    print("  LISTADO COMPLETO DE CÓDIGOS EN LA BD")
    print("─" * 60)
    print()
    print(f"  {'CÓDIGO':<12} {'NOMBRE':<25} {'ACTIVO':<8} {'PRIMER ACCESO':<20}")
    print(f"  {'─'*12} {'─'*25} {'─'*8} {'─'*20}")

    usuarios = listar_usuarios()
    for u in usuarios:
        codigo = u["codigo_acceso"]
        nombre = (u["nombre"] or "—")[:24]
        activo = "✓" if u["activo"] else "○ no usado"
        primer = u["fecha_primer_acceso"].strftime("%Y-%m-%d %H:%M") if u["fecha_primer_acceso"] else "—"
        print(f"  {codigo:<12} {nombre:<25} {activo:<8} {primer:<20}")

    print()
    print("─" * 60)
    print(f"  Total en BD: {len(usuarios)}")
    print(f"  Activados:   {sum(1 for u in usuarios if u['activo'])}")
    print(f"  Sin usar:    {sum(1 for u in usuarios if not u['activo'])}")
    print("─" * 60)
    print()
    print("Próximo paso: entregá los códigos a los voluntarios. Para identificarlos,")
    print("anotá manualmente en tu cuaderno o spreadsheet a qué persona le diste cada código.")
    print()


if __name__ == "__main__":
    main()
