# Chatbot Educativo Shipibo-Konibo (Rasa)

Asistente conversacional en espanol para practicar vocabulario en shipibo-konibo y recorrer un cuento interactivo con preguntas guiadas.

## Objetivo

Este proyecto implementa un MVP educativo con Rasa 3.x que incluye:

- flujo de aprendizaje de vocabulario contextualizado,
- flujo narrativo por fragmentos (cuento del pescador shipibo),
- acciones personalizadas para evaluar respuestas, dar pistas y retomar contexto.

## Stack y requisitos

- Python 3.8 a 3.10
- Rasa 3.x
- Rasa SDK

Instalacion recomendada:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install rasa rasa-sdk
```

Verificar instalacion:

```bash
rasa --version
```

## Estructura del repositorio

```text
.
├── actions/
│   ├── __init__.py
│   └── actions.py
├── data/
│   ├── nlu.yml
│   ├── stories.yml
│   └── rules.yml
├── tests/
│   └── test_stories.yml
├── config.yml
├── domain.yml
├── endpoints.yml
├── credentials.yml
└── corpus_escolar_basico.json
```

## Ejecutar el proyecto

1) Entrena el modelo:

```bash
rasa train
```

2) En una terminal separada, levanta el servidor de acciones:

```bash
rasa run actions
```

3) En otra terminal, inicia el chat por consola:

```bash
rasa shell
```

4) (Opcional) Depuracion guiada de historias:

```bash
rasa interactive
```

5) (Opcional) Probar solo clasificacion NLU:

```bash
rasa shell nlu
```

## Comandos utiles

- Ejecutar pruebas de historias:

```bash
rasa test core
```

- Validar datos de entrenamiento:

```bash
rasa data validate
```

## Notas de desarrollo

- Las acciones estan en `actions/actions.py`.
- El estado conversacional se define por slots en `domain.yml`.
- Si cambias `domain.yml`, `config.yml` o archivos de `data/`, vuelve a entrenar con `rasa train`.
- Si cambias `actions/actions.py`, reinicia `rasa run actions`.

## Estado actual

El proyecto usa datos embebidos de ejemplo para vocabulario y cuento dentro de `actions/actions.py`. Estan pensados como base de trabajo para integrar corpus y modulos reales en una siguiente iteracion.
