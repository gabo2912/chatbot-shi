# Justificación pedagógica del sistema de scoring ponderado

> Fragmento para integrar en el capítulo de Metodología (sección Diseño del módulo de vocabulario).

## 1. Planteamiento

El módulo de vocabulario de Pishico evalúa el conocimiento léxico del aprendiz mediante actividades que demandan distinto grado de procesamiento cognitivo. Para reflejar esta heterogeneidad en la métrica de avance del usuario, se diseñó un sistema de scoring ponderado donde la magnitud del incremento (o decremento) aplicado al registro del intento depende del tipo de tarea ejecutada y del apoyo externo recibido.

La ponderación adoptada es:

| Evento | Peso aplicado |
|---|---|
| Acierto en modo productivo (ES → SHP, escribir la palabra shipibo) | +2.0 |
| Acierto en modo receptivo (SHP → ES, identificar el significado) | +1.0 |
| Uso de pista antes de responder correctamente | −0.5 |
| Respuesta incorrecta tras agotar intentos | −1.0 |

## 2. Fundamentación teórica

La distinción entre conocimiento *receptivo* y *productivo* del vocabulario en una segunda lengua (L2) es una de las dicotomías más establecidas y replicadas en la investigación sobre adquisición léxica (Nation, 2013; Schmitt, 2014; Webb, 2005). El conocimiento receptivo se manifiesta cuando el aprendiz reconoce el significado de una palabra al escucharla o leerla; el productivo, cuando es capaz de recuperarla activamente para usarla al hablar o escribir.

### 2.1. Asimetría receptivo-productiva

Webb (2008) diseñó tests de traducción bidireccionales sobre los mismos ítems léxicos para cuantificar la brecha entre ambos tipos de conocimiento. Sus resultados confirmaron empíricamente que:

- El vocabulario receptivo es sistemáticamente mayor que el productivo en aprendices de L2.
- La brecha entre ambos **aumenta a medida que disminuye la frecuencia léxica** de la palabra evaluada.
- Con scoring estricto, la ratio productivo/receptivo se ubicó en 77%; con scoring sensible, en 93%.

Esta asimetría es especialmente relevante para el caso del shipibo-konibo, una lengua de baja densidad de recursos donde prácticamente todo el vocabulario meta cae en bandas de baja frecuencia desde la perspectiva del aprendiz hispanohablante. Por tanto, ponderar más el éxito productivo no es una decisión arbitraria sino un reflejo de la mayor dificultad cognitiva documentada para esa modalidad.

### 2.2. Niveles de fuerza del conocimiento léxico

Laufer y Goldstein (2004) propusieron un modelo jerárquico de cuatro niveles de *strength of knowledge* para palabras de L2, ordenados por dificultad creciente:

1. *Passive recognition* — reconocer una palabra entre opciones (receptivo, opción múltiple).
2. *Active recognition* — identificar el significado entre alternativas.
3. *Passive recall* — recuperar el significado en L1 a partir de la forma en L2 (receptivo escrito).
4. *Active recall* — producir la forma en L2 a partir del significado en L1 (productivo).

El sistema de scoring de Pishico opera sobre los niveles 3 (modo SHP → ES) y 4 (modo ES → SHP), justamente los dos niveles más exigentes del modelo. Asignar peso 2 al nivel 4 y peso 1 al nivel 3 operacionaliza directamente la jerarquía propuesta por estos autores.

### 2.3. Carga de implicación y soporte externo

La *involvement load hypothesis* (Hulstijn y Laufer, 2001) sostiene que la retención léxica es proporcional a la cantidad de búsqueda, evaluación y necesidad cognitiva que el aprendiz invierte al procesar un ítem. Cuando el sistema otorga una pista al usuario antes de que responda, parte de esa carga cognitiva queda externalizada: la respuesta correcta posterior refleja entonces conocimiento parcialmente asistido, no recuperación autónoma.

Penalizar el uso de pista con −0.5 mantiene el reconocimiento de un acierto (el aprendiz, en última instancia, produjo la respuesta) pero descuenta el componente que fue resuelto por el sistema y no por su propio recurso cognitivo. La penalización de −1.0 por error agotado, por su parte, refleja que el ítem no fue dominado en ninguno de los niveles del modelo de Laufer y Goldstein.

### 2.4. Pertinencia adicional desde la sensibilidad de scoring

Webb (2008) subrayó que los métodos de scoring sensibles tienden a igualar las puntuaciones receptiva y productiva, mientras que los estrictos preservan la diferencia. Pishico adopta un esquema intermedio que combina:

- **Discriminación binaria del acierto** sobre normalización (acentos, mayúsculas y guiones suaves se ignoran al comparar), siguiendo el principio sensible para no penalizar errores ortográficos triviales en una lengua sin estandarización ortográfica plenamente consolidada como el shipibo-konibo.
- **Diferenciación de peso entre modos**, recuperando la separación estricta que Webb defiende como condición para hacer visible la brecha receptivo-productivo en la métrica final.

Esta combinación es coherente con el contexto de una lengua minorizada donde imponer un estándar ortográfico inflexible introduciría ruido evaluativo no atribuible al conocimiento léxico real del aprendiz.

## 3. Síntesis defendible

El sistema de scoring ponderado de Pishico no es una decisión heurística sino una operacionalización directa de tres líneas de investigación convergentes en la literatura de adquisición de vocabulario L2: la asimetría receptivo-productiva (Webb, 2008), el modelo jerárquico de fuerza de conocimiento (Laufer y Goldstein, 2004) y la teoría de carga de implicación (Hulstijn y Laufer, 2001). Cada uno de los cuatro pesos (+2, +1, −0.5, −1) tiene anclaje teórico explícito y permite a la métrica de dominio léxico del aprendiz reflejar no solo cuántas palabras conoce, sino con qué profundidad y autonomía las recupera.

## 4. Limitaciones reconocidas

Conviene declarar en la sección de limitaciones de la tesis que:

- Los pesos específicos (2, 1, 0.5, 1) no provienen de una calibración empírica con población shipiba-aprendiz, sino de la interpretación cualitativa del marco teórico. La calibración cuantitativa exigiría un estudio comparativo que excede el alcance del MVP.
- El modelo de Laufer y Goldstein fue desarrollado para EFL/ESL en contextos académicos, no para lenguas indígenas amazónicas. Su transferencia al shipibo asume una equivalencia funcional de los niveles de fuerza que debería validarse a futuro.
- Las penalizaciones (-0.5, -1) podrían generar desmotivación en aprendices con baja autoeficacia. Esta hipótesis es contrastable con los datos del piloto mediante el subscale de Confianza del cuestionario ARCS-IMMS.

## 5. Referencias bibliográficas

Hulstijn, J. H., & Laufer, B. (2001). Some empirical evidence for the involvement load hypothesis in vocabulary acquisition. *Language Learning*, 51(3), 539–558. https://doi.org/10.1111/0023-8333.00164

Laufer, B., & Goldstein, Z. (2004). Testing vocabulary knowledge: Size, strength, and computer adaptiveness. *Language Learning*, 54(3), 399–436. https://doi.org/10.1111/j.0023-8333.2004.00260.x

Laufer, B., & Nation, P. (1999). A vocabulary-size test of controlled productive ability. *Language Testing*, 16(1), 33–51. https://doi.org/10.1177/026553229901600103

Nation, I. S. P. (2013). *Learning vocabulary in another language* (2.ª ed.). Cambridge University Press.

Schmitt, N. (2014). Size and depth of vocabulary knowledge: What the research shows. *Language Learning*, 64(4), 913–951. https://doi.org/10.1111/lang.12077

Webb, S. (2005). Receptive and productive vocabulary learning: The effects of reading and writing on word knowledge. *Studies in Second Language Acquisition*, 27(1), 33–52. https://doi.org/10.1017/S0272263105050023

Webb, S. (2008). Receptive and productive vocabulary sizes of L2 learners. *Studies in Second Language Acquisition*, 30(1), 79–95. https://doi.org/10.1017/S0272263108080042
