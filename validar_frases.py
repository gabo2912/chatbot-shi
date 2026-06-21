#!/usr/bin/env python3
"""
validar_frases.py — Comprueba que las frases nuevas del corpus conversacional
se están cargando y son alcanzables, USANDO EL MISMO loader que el bot.

Colocalo en la raíz del proyecto (chatbot-shi/) y corré:
    python3 validar_frases.py

Valida tres cosas:
  1. Que el loader leyó el archivo correcto (cuenta total + flag MODO_DESARROLLO).
  2. Que las frases NUEVAS se encuentran al buscarlas (sondas).
  3. Que las categorías nuevas (sobre todo 'emocion', que no existía antes)
     tienen contenido.

Importante: refleja el estado del ARCHIVO en disco, no del bot en ejecución.
Si cambiaste el Excel, igual tenés que reiniciar `rasa run actions` para que
el bot lo tome.
"""

import sys
import os

# Permitir `import interaccion_loader` desde actions/ (igual que listarusuarios.py)
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "actions"))
sys.path.insert(0, "actions")

import interaccion_loader as il


# Sondas: frases que SOLO existen en el corpus nuevo (categoría emocion = prueba
# limpia, no existía antes del merge). (texto_a_buscar, shp_esperado_contiene)
SONDAS = [
    ("Me alegra.",        "raro"),
    ("¡Qué tristeza!",    "onis"),
    ("Me da miedo.",      "raketai"),
    ("¡Qué bonito!",      "jakin"),
    ("Exacto.",           "senen"),
    ("Correcto.",         "jakon"),
]

# Categorías que deberían existir tras el merge (emocion es la delatora).
CATS_ESPERADAS = ["emocion", "afirmacion", "negacion", "cortesia", "ayuda"]


def _norm(s):
    return " ".join(str(s or "").lower().split())


def main():
    print("═" * 60)
    print("  Validación del corpus conversacional")
    print("═" * 60)

    # ── 1. Carga ──────────────────────────────────────────────────────────────
    if not il.frases_disponibles():
        print("  ✗ El loader NO cargó frases. Revisá la ruta del Excel:")
        print("    actions/corpus/frases_conversacionales.xlsx")
        sys.exit(1)

    total = il.total_frases()
    validadas = il.total_validadas()
    modo_dev = getattr(il, "MODO_DESARROLLO", "?")
    print(f"  Frases cargadas        : {total}")
    print(f"  Con validado_asesor=SI : {validadas}")
    print(f"  MODO_DESARROLLO        : {modo_dev}")
    if modo_dev:
        print("    (carga TODAS las frases, validadas o no)")
    else:
        print(f"    (solo carga las {validadas} validadas; el resto se ignora)")
    print("─" * 60)

    # ── 2. Sondas: ¿se encuentran las frases nuevas? ─────────────────────────
    print("  Búsqueda de frases NUEVAS:")
    hits = 0
    for texto, esperado in SONDAS:
        m = il.buscar_frase(texto)
        if m and esperado in _norm(m.get("shp", "")):
            print(f"    ✓ {texto!r:22} → {m['shp']!r}  (cat={m['categoria']})")
            hits += 1
        elif m:
            print(f"    ~ {texto!r:22} → encontró {m['shp']!r} "
                  f"(esperaba que contuviera '{esperado}')")
        else:
            print(f"    ✗ {texto!r:22} → NO encontrada")
    print("─" * 60)

    # ── 3. Categorías nuevas con contenido ───────────────────────────────────
    print("  Categorías nuevas:")
    disponibles = set(il.categorias_disponibles())
    cats_ok = 0
    for cat in CATS_ESPERADAS:
        n = len(il.frases_por_categoria(cat))
        estado = "✓" if n > 0 else "✗"
        if n > 0:
            cats_ok += 1
        print(f"    {estado} {cat:<12} {n} frase(s)")
    print("─" * 60)

    # ── Veredicto ─────────────────────────────────────────────────────────────
    ok = hits == len(SONDAS) and cats_ok == len(CATS_ESPERADAS)
    if ok:
        print("  ✓ VALIDADO: las frases nuevas están cargadas y son alcanzables.")
        print("    Reiniciá `rasa run actions` si el bot estaba corriendo.")
    else:
        print(f"  ⚠ PARCIAL: {hits}/{len(SONDAS)} sondas, "
              f"{cats_ok}/{len(CATS_ESPERADAS)} categorías.")
        print("    Si MODO_DESARROLLO=False, revisá que las nuevas tengan validado_asesor=SI.")
    print("═" * 60)


if __name__ == "__main__":
    main()
