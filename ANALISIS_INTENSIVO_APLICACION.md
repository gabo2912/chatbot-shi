# Analisis intensivo de la aplicacion (Rasa + frontend)

## Alcance y metodologia

Este analisis se realizo **sin modificar el codigo fuente funcional** de la aplicacion. Se revisaron:

- configuracion de Rasa (`config.yml`, `domain.yml`, `data/*.yml`),
- acciones personalizadas (`actions/*.py`),
- capa de persistencia (`actions/db.py`),
- proxy/backend web (`server.py`) y frontend (`index.html`),
- artefactos de evaluacion NLU (`results/*.json`, `tests/*.yml`).

Adicionalmente, se toma en cuenta el esquema de ejecucion indicado:

1. `rasa run actions`
2. `rasa run --enable-api`
3. `python3 server.py`

cada uno en terminales separadas.

---

## 1) Arquitectura general y responsabilidades

### 1.1 Componentes

- **Rasa Core + NLU**: orquesta intenciones, reglas y forms (puerto 5005).
- **Rasa SDK Actions**: ejecuta logica pedagogica y persistencia (puerto 5055, webhook en `endpoints.yml`).
- **Proxy HTTP propio (`server.py`)**: sirve `index.html`, hace proxy a `/webhooks/rest/webhook` y expone `/progreso/<sender_id>`.
- **Frontend web (`index.html`)**: interfaz de chat + panel de progreso + gestion local de usuario.
- **SQLite (`actions/progress.db`)**: persistencia de intentos y avance por cuento.
- **Corpus externos**:
  - vocabulario: `actions/corpus/palabras.xlsx`
  - cuentos: `actions/corpus/cuentos.xlsx`

### 1.2 Flujo tecnico end-to-end

1. Usuario envia mensaje en frontend.
2. Frontend hace POST a `/webhooks/rest/webhook` del proxy.
3. `server.py` reenvia a Rasa REST (`localhost:5005`).
4. Rasa clasifica intent + aplica reglas/politicas/forms.
5. Si corresponde, Rasa invoca accion custom por `action_endpoint` (`localhost:5055`).
6. Action responde texto/botones y puede registrar avance en SQLite.
7. Respuesta vuelve a frontend y se renderiza (burbujas + quick replies).

### 1.3 Dependencias operativas criticas

- `openpyxl` es requisito real para cargar Excel en `corpus_loader.py` y `cuentos_loader.py`.
- Si faltan archivos Excel, el bot inicia pero con datos vacios (degrada experiencia, no rompe proceso completo).
- La API REST de Rasa debe estar activa por `--enable-api` para que el frontend funcione.

---

## 2) Configuracion conversacional de Rasa

## 2.1 Pipeline NLU (`config.yml`)

- Tokenizacion: `WhitespaceTokenizer`.
- Features: `RegexFeaturizer`, `LexicalSyntacticFeaturizer`, `CountVectorsFeaturizer` (palabra + char n-gram 1-4).
- Clasificador principal: `DIETClassifier` (150 epocas).
- Entidades regex/lookup: `RegexEntityExtractor` con `use_lookup_tables: true`.
- Mapeo sinonimos: `EntitySynonymMapper`.
- Fallback NLU: `FallbackClassifier` con threshold 0.5.

Interpretacion:
- El pipeline esta orientado a robustecer variaciones ortograficas y cadenas cortas.
- La lookup `palabra_objetivo` esta correctamente respaldada por `RegexEntityExtractor`.

## 2.2 Politicas Core

- `RulePolicy` (fallback core habilitado, threshold 0.4).
- `MemoizationPolicy`.
- `TEDPolicy` (`max_history: 5`, 100 epocas).

Interpretacion:
- Predominio de **flujo dirigido por reglas** (esperable para MVP educativo con rutas controladas).

---

## 3) Dominio conversacional

## 3.1 Intenciones

El dominio declara 16 intents funcionales:

- sociales/control: `saludo`, `despedida`, `agradecer`, `pedir_ayuda`, `pedir_repeticion`, `pedir_traduccion`, `confirmacion`, `negacion`, `expresar_emocion`, `pausar`, `out_of_scope`
- pedagogicos: `aprender_vocabulario`, `iniciar_cuento`, `responder_actividad`, `continuar`, `ver_mi_progreso`

## 3.2 Slots

Slots clave de estado:

- `flujo_actual` (`vocabulario|cuento|ninguno`) controla enrutamiento principal.
- `palabra_actual`, `categoria_actual`, `intentos_palabra` sostienen el ejercicio de vocabulario.
- `fragmento_actual`, `cuento_actual` sostienen avance del cuento.
- `respuesta_actividad` captura texto libre via form.
- `ultima_respuesta_bot` soporta repeticion contextual.

## 3.3 Form activo

- Form unico: `actividad_form` con slot requerido `respuesta_actividad`.
- Mapeo `from_text` con exclusiones para evitar capturar comandos de inicio de flujo.

Interpretacion:
- Este diseño desacopla la captura de respuesta del intent NLU, reduciendo el problema de misclasificacion en respuestas cortas.

---

## 4) NLU training y estrategia linguistica

## 4.1 Diseno de `responder_actividad`

En `data/nlu.yml` se observa una decision acertada:

- se retiran palabras sueltas del intent `responder_actividad`,
- se privilegian patrones de frase ("creo que es...", "mi respuesta es..."),
- las palabras pasan a **lookup table** `palabra_objetivo`.

Impacto esperado:
- menor efecto "intent iman" sobre entradas cortas.

## 4.2 Cobertura por idioma

- El entrenamiento es principalmente espanol con inserciones limitadas.
- Los tests `test_nlu_v2.yml` incluyen shipibo exploratorio; los resultados muestran baja generalizacion en esos intents.

## 4.3 Intencion `ver_mi_progreso`

- Esta entrenada en `nlu.yml` y enlazada por regla a `action_ver_progreso`.
- Tambien puede dispararse por botones con payload explicito, sin depender de clasificacion.

---

## 5) Reglas y control de flujo (`data/rules.yml`)

## 5.1 Estado libre (`flujo_actual: ninguno`)

Reglas para saludo, despedida, agradecer, pausar, ayuda general, traduccion, out_of_scope.

## 5.2 Flujo vocabulario

Secuencia base:

1. `aprender_vocabulario` -> `action_iniciar_vocabulario`
2. set `flujo_actual: vocabulario`
3. activa `actividad_form`
4. cierre form -> `action_evaluar_respuesta_vocab`

Tambien define reglas para `continuar`, `pedir_ayuda`, `pedir_repeticion`, `pausar` en ese flujo.

## 5.3 Flujo cuento

Secuencia base:

1. `iniciar_cuento` -> `action_iniciar_cuento`
2. set `flujo_actual: cuento`
3. respuestas/evolucion por `responder_actividad`, `continuar`, ayuda y repeticion.

## 5.4 Observaciones de consistencia

- Hay acciones declaradas en dominio que no aparecen en reglas (`action_continuar_vocabulario`, `action_continuar_cuento`).
- Existe `ActionRetomarFlujo` implementada en Python pero no declarada en `domain.yml`.

No rompe el sistema, pero indica deuda de alineacion dominio-codigo.

---

## 6) Analisis intensivo de acciones custom

## 6.1 Validacion del form (`ValidateActividadForm`)

Puntos fuertes:

- Normaliza texto y detecta interrupciones (ayuda, repeticion, pausa, despedida).
- Bloquea cambios de flujo dentro de una pregunta activa y pide terminar/pausar.
- Si hay entidad `palabra_objetivo`, la prioriza como respuesta.
- En flujo cuento, reutiliza el mismo form con soporte de pista por fragmento.

Riesgos/limites:

- `INTENTS_INTERRUPCION` esta definido pero no se usa directamente.
- Algunos sets de palabras clave de interrupcion son exact-match; expresiones parafraseadas pueden pasar como respuesta.

## 6.2 Vocabulario

- `action_iniciar_vocabulario` selecciona categoria por:
  1) mencion textual,
  2) semantica de cambio (otra/siguiente/cambiar),
  3) fallback primera categoria disponible.
- `action_evaluar_respuesta_vocab` clasifica `correcto|parcial|incorrecto` con:
  - normalizacion,
  - variantes ortograficas,
  - tolerancia parcial.
- Estrategia pedagogica:
  - 1er error: pista y reintento.
  - 2do error: revela respuesta y propone continuar.
- Registra avance en DB solo en estados finales relevantes.

## 6.3 Cuento interactivo

- Carga fragmentos desde Excel por `cuentos_loader`.
- Si fragmento tiene pregunta, activa form y solicita respuesta.
- Evaluacion por `action_evaluar_respuesta_cuento` + registro por fragmento.
- Al finalizar cuento, limpia flujo y ofrece pasar a vocabulario o progreso.

## 6.4 Traduccion y progreso

- `action_traducir` usa heuristica simple de ultima palabra significativa + diccionario bidireccional.
- `action_ver_progreso` consulta DB agregada por categoria/cuento y renderiza barras textuales.

---

## 7) Persistencia y analitica (`actions/db.py`)

## 7.1 Modelo de datos

- `progreso_vocabulario`: historial de intentos por palabra.
- `progreso_cuento`: avance por fragmento y bandera de acierto.

## 7.2 Caracteristicas tecnicas

- SQLite local con WAL.
- Indices por `sender_id` para consultas de progreso.
- Inicializacion automatica al importar modulo.

## 7.3 Observaciones

- Existe API interna para resumen (`get_resumen_categorias`, `get_resumen_cuento`) y ultima posicion (`ultima_posicion`).
- `ultima_posicion` no esta integrada al flujo de retoma en frontend/backend.

---

## 8) Proxy y frontend: experiencia real de uso

## 8.1 `server.py`

- Endpoint de chat: proxy transparente a Rasa.
- Endpoint `/status` para health check de Rasa.
- Endpoint `/progreso/<sender_id>` lee progreso real desde SQLite.

## 8.2 `index.html`

- Crea `senderID` UUID en `localStorage`.
- Al iniciar app y al cambiar entre vistas vocab/cuento envia `/restart` antes de iniciar flujo.
- Usa payloads explicitos (`/aprender_vocabulario`, `/iniciar_cuento`, `/saludo`) para mayor determinismo.
- Renderiza panel de progreso consumiendo `/progreso/<sender_id>`.

Nota importante:
- Tambien mantiene una logica local `registrarProgresLocal`, pero el panel principal de progreso ya se construye con datos del backend.

---

## 9) Resultados NLU observados (`results/intent_report.json`)

Metricas globales reportadas:

- `accuracy`: **0.7355**
- `weighted F1`: **0.7418**

Desempeno por intent (destacados):

- fuerte:
  - `aprender_vocabulario` (F1 ~0.976)
  - `continuar` (F1 ~0.905)
  - `saludo` (F1 ~0.818)
- debil:
  - `pedir_ayuda` (recall ~0.517)
  - `responder_actividad` (precision ~0.567)
  - `despedida`, `agradecer`, `pedir_repeticion` (F1 0.0 en ese corte)

Patron de error dominante (`intent_errors.json`):

- frases shipibo de cortesia/ayuda/despedida migran a intents no deseados,
- persisten confusiones hacia `responder_actividad` en entradas cortas o raras,
- algunas frases de ayuda se cruzan con `expresar_emocion`.

Interpretacion:
- Para espanol operativo el sistema ya es util, pero la robustez bilingue (shipibo conversacional) sigue en fase basal.

---

## 10) Riesgos funcionales identificados

1. **Dependencia silenciosa de corpus Excel**: si falta archivo o dependencia, el bot sigue pero con actividad limitada.
2. **Reseteo frecuente de contexto (`/restart`)** desde frontend: mejora limpieza de flujo, pero elimina continuidad conversacional entre vistas.
3. **Desalineacion menor dominio-codigo**: acciones implementadas no enrutadas o no declaradas.
4. **NLU shipibo insuficiente** para intents sociales fuera del microdominio pedagogico.

---

## 11) Conclusiones ejecutivas

- La aplicacion esta bien estructurada para un MVP educativo guiado por reglas.
- El nucleo pedagogico (vocabulario + cuento + pistas + evaluacion + persistencia) esta correctamente implementado.
- El mecanismo `form + from_text + validacion custom` es la decision mas solida del sistema para tolerar errores NLU en respuestas cortas.
- El principal cuello de botella actual no es Core, sino **cobertura NLU** en intents de conversacion abierta y shipibo.
- La arquitectura de tres procesos (`actions`, `rasa api`, `server.py`) es coherente con el diseño y necesaria para el funcionamiento del frontend.

---

## 12) Checklist de verificacion operativa recomendada

Para validar ejecucion de extremo a extremo (sin tocar codigo):

1. Terminal A: `rasa run actions`
2. Terminal B: `rasa run --enable-api`
3. Terminal C: `python3 server.py`
4. Abrir `http://localhost:8080`
5. Pruebas minimas:
   - iniciar vocabulario -> responder correcto/parcial/incorrecto,
   - pedir pista y repetir,
   - pausar y reiniciar actividad,
   - iniciar cuento -> responder -> continuar,
   - abrir "Mi Aprendizaje" y verificar que refleja datos persistidos.

Si falla algo, el primer control debe ser `GET /status` en el proxy para confirmar disponibilidad de Rasa.
