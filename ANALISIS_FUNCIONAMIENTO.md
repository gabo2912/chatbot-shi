# Analisis de funcionamiento - Chatbot Shipibo-Konibo

## 1) Arquitectura actual (como esta operando)

- **Canal web**: `index.html` + `server.py` montan un frontend propio y un proxy HTTP en `localhost:8080`.
- **Motor conversacional**: `rasa run --enable-api` expone REST en `localhost:5005/webhooks/rest/webhook`.
- **Acciones custom**: `rasa run actions` ejecuta la logica pedagogica en `actions/actions.py`.
- **Persistencia**: SQLite local en `actions/progress.db` via `actions/db.py`.
- **Corpus lexico**: carga dinamica desde `actions/corpus/palabras.xlsx` usando `actions/corpus_loader.py`.

Flujo tecnico de un mensaje:
1. Usuario escribe en web -> `index.html` envia POST a `/webhooks/rest/webhook`.
2. `server.py` reenvia al endpoint REST de Rasa.
3. Rasa predice intent/rule/form y, si aplica, invoca accion custom en `actions`.
4. La accion responde texto/botones y puede guardar progreso en SQLite.
5. Respuesta vuelve al frontend y se renderiza con quick replies.

---

## 2) Historias de usuario e intenciones detectadas

### Historias principales

1. **Como aprendiz**, quiero practicar vocabulario por categorias para memorizar palabras en shipibo.
2. **Como aprendiz**, quiero escuchar un cuento por fragmentos con preguntas para aprender en contexto.
3. **Como aprendiz**, quiero pedir ayuda/pista/repeticion para no quedarme bloqueado.
4. **Como aprendiz**, quiero traducir palabras puntuales entre espanol y shipibo.
5. **Como usuario**, quiero pausar/salir/retomar para controlar la sesion.

### Intenciones NLU configuradas

En `domain.yml` se definen 15 intents funcionales:
- `saludo`, `despedida`, `agradecer`, `pedir_ayuda`, `pedir_repeticion`, `pedir_traduccion`
- `aprender_vocabulario`, `iniciar_cuento`, `responder_actividad`, `continuar`, `pausar`
- `confirmacion`, `negacion`, `expresar_emocion`, `out_of_scope`

Observacion clave:
- El intent `responder_actividad` fue redisenado para aprender **estructuras de respuesta** (no palabras sueltas), y las palabras fueron movidas a lookup `palabra_objetivo` (buena decision para reducir sesgo de clasificacion).

---

## 3) Flujos conversacionales reales

## 3.1 Flujo vocabulario

Base de control:
- Regla de inicio: `data/rules.yml` ("Iniciar vocabulario desde estado libre").
- Activa `actividad_form` para capturar `respuesta_actividad` por `from_text`.

Comportamiento:
1. `action_iniciar_vocabulario` elige categoria (mencionada, siguiente o fallback) y pregunta palabra.
2. `actividad_form` solicita respuesta.
3. `validate_actividad_form` filtra interrupciones (ayuda, repetir, pausa, cambio de flujo) y solo guarda respuesta valida.
4. Al cerrar form, `action_evaluar_respuesta_vocab` evalua `correcto/parcial/incorrecto`.
5. En correcto/parcial/incorrecto(2do intento) registra en DB con `registrar_intento(...)`.
6. Ofrece continuar con botones.

Fortalezas:
- Buen manejo de respuestas cortas aunque el intent falle (via `from_text`).
- Reglas claras de interrupcion dentro de actividad.
- Evaluacion con normalizacion + variantes ortograficas.

## 3.2 Flujo cuento interactivo

Base de control:
- Reglas en `data/rules.yml` cuando `flujo_actual: cuento`.

Comportamiento:
1. `action_iniciar_cuento` arranca en fragmento 0 y pregunta si corresponde.
2. `continuar` llama `action_siguiente_fragmento`.
3. `responder_actividad` llama `action_evaluar_respuesta_cuento`.
4. Si hay pregunta, evalua y registra `progreso_cuento` con `registrar_fragmento_cuento(...)`.
5. Permite ayuda (`action_dar_ayuda_cuento`) y repeticion (`action_repetir_fragmento`).

Fortalezas:
- Experiencia simple y predecible por reglas.
- Persistencia de respuestas por fragmento.

## 3.3 Flujos globales

- Saludo y oferta de opciones.
- Ayuda/traduccion/out_of_scope cuando no hay flujo activo.
- Pausa con mensaje de reinicio recomendado (`/restart`).

---

## 4) Que esta funcionando bien

- **Separacion de responsabilidades**: NLU/Rules/Actions/Proxy/Frontend bien desacoplados para un MVP.
- **Control de contexto por slots** (`flujo_actual`, `categoria_actual`, `palabra_actual`, `fragmento_actual`).
- **Manejo de errores pedagogico**: pista en primer error, respuesta en segundo.
- **Carga de corpus externa** desde Excel validado (evita hardcode principal).
- **Persistencia operativa**: se crean tablas e indices automaticamente; hay registros reales en `progress.db`.

---

## 5) Brechas y mejoras recomendadas

## 5.1 NLU y cobertura linguistica

Brecha:
- El reporte (`results/intent_report.json`) muestra accuracy ~0.736 y recall bajo en intents clave (`pedir_ayuda`, `despedida`, `agradecer`, `pedir_repeticion`).
- En `results/intent_errors.json` aun hay confusion hacia `responder_actividad` y baja robustez en frases shipibo conversacionales.

Mejoras:
- Aumentar ejemplos de `despedida`, `agradecer`, `pedir_repeticion` (especialmente shipibo y variantes coloquiales).
- Reducir ambiguedad semantica entre `pedir_ayuda` y `expresar_emocion` con plantillas mas contrastivas.
- Mantener tests separados por idioma (espanol vs shipibo) y reportar metricas por segmento.

## 5.2 Reglas y consistencia de dominio

Brecha:
- En `domain.yml` se declara `action_continuar_vocabulario` y `action_continuar_cuento`, pero en reglas no se usan (quedan huerfanas funcionalmente).
- `action_retomar_flujo` existe en codigo, pero no esta listado en `domain.yml` ni invocado por reglas.

Mejoras:
- Eliminar acciones no usadas o conectarlas explicitamente en reglas/stories para evitar deuda tecnica.
- Agregar stories de regresion para "interrupcion + retoma" en ambos flujos.

## 5.3 Frontend vs backend de progreso

Brecha critica:
- El panel **Mi Aprendizaje** en `index.html` usa `localStorage` (`pishico_progreso`) y **no** consume `actions/progress.db`.
- La funcion `registrarProgresLocal` incrementa progreso si el texto del bot contiene categoria; pero los mensajes de exito no siempre incluyen categoria, por lo que el progreso local puede quedar subregistrado o inconsistente.

Mejoras:
- Exponer endpoint de progreso real (basado en DB) y renderizarlo en frontend.
- Mantener localStorage solo como cache temporal, no como fuente de verdad.

## 5.4 Gestion de sesion

Brecha:
- Al cargar la app se envia `/restart`, lo cual limpia contexto conversacional en Rasa en cada inicio de vista, aunque la DB conserva historial. Esto genera sensacion de "progreso partido" (estado conversacional nuevo vs historial viejo).

Mejoras:
- Definir estrategia: reinicio total por sesion o reanudacion real por `sender_id`.
- Si se quiere continuidad, usar `ultima_posicion(...)` (ya existe en DB pero no se usa) para retomar palabra/categoria.

## 5.5 Robustez operativa

Brecha:
- Si falta `openpyxl` o el Excel, el sistema queda con vocabulario vacio y solo emite aviso al iniciar actividad.

Mejoras:
- Health check explicito de corpus al inicio (mensaje proactivo antes de iniciar ejercicios).
- Script de validacion previa (`rasa data validate` + chequeo Excel).

---

## 6) Revision de gestion de base de datos

## 6.1 Lo que implementa hoy

Archivo: `actions/db.py`

- Motor: SQLite (`actions/progress.db`).
- Conexion: `check_same_thread=False` + `PRAGMA journal_mode=WAL`.
- Tablas:
  - `progreso_vocabulario(sender_id, categoria, palabra_es, palabra_shp, resultado, intentos, fecha)`
  - `progreso_cuento(sender_id, cuento_id, fragmento, respuesta_ok, fecha)`
- Indices por `sender_id` para ambas tablas.
- Inicializacion automatica en import (`init_db()`).

Uso real desde acciones:
- `registrar_intento(...)` se llama en respuestas de vocabulario (correcto/parcial/incorrecto final).
- `registrar_fragmento_cuento(...)` se llama en evaluacion del cuento.

Estado observado en la DB local:
- Tablas creadas correctamente.
- `progreso_vocabulario` contiene registros reales (4 filas en la revision).
- `progreso_cuento` actualmente sin registros (0 filas en la revision puntual).

## 6.2 Brechas de la capa de datos

- No hay restricciones `CHECK` para valores de `resultado`.
- `fecha` usa texto localtime; para analitica seria preferible timestamp UTC consistente.
- `ultima_posicion`, `get_resumen_categorias`, `get_resumen_cuento` existen pero no estan integradas al frontend.
- No hay politica de deduplicacion por palabra/sesion (puede crecer rapido por reintentos repetidos).

## 6.3 Recomendaciones concretas

1. Definir la DB como **fuente unica de verdad** del progreso mostrado al usuario.
2. Exponer API de lectura de resumen por `sender_id` y conectar `Mi Aprendizaje`.
3. Agregar validaciones de integridad (al menos en logica app o constraints SQL).
4. Estandarizar timestamps UTC para trazabilidad.
5. Agregar consultas de retoma de sesion usando `ultima_posicion(...)`.

---

## 7) Conclusión ejecutiva

El MVP esta bien encaminado: los dos flujos pedagogicos (vocabulario y cuento) funcionan con reglas claras, acciones robustas y persistencia local operativa. La principal brecha no esta en "si guarda datos" sino en **consistencia de producto**: hoy conviven dos progresos (SQLite real y localStorage visual) que pueden divergir. El siguiente salto de calidad deberia enfocarse en alinear NLU bilingue y conectar el panel de aprendizaje a la DB real para cerrar el ciclo end-to-end.
