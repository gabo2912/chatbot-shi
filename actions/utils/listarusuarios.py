import sys

sys.path.insert(0, "actions")

from db import listar_usuarios

for u in listar_usuarios():
    print(
        f"{u['codigo_acceso']}: "
        f"activo={u['activo']}, "
        f"nombre={u['nombre']}, "
        f"primer_acceso={u['fecha_primer_acceso']}"
    )