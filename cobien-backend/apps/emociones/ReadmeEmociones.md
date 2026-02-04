# README — IA de Emociones para Videollamadas (`/emociones`)

> Módulo de análisis emocional pensado para integrarse con la pantalla de videollamadas. Incluye **endpoints Django** para operar desde el front de Twilio Video y **scripts standalone** para pruebas locales y generación de logs. Persiste resúmenes en **MongoDB Atlas**.

---

## 1) Qué resuelve

* **Seleccionar un rostro de referencia (ROI)** para la sesión de videollamada.
* **Detectar emociones en tiempo real** sobre esa ROI empleando **DeepFace** (CNN pre-entrenadas) y **OpenCV**.
* **Suavizado y agregación** de resultados (EWMA + ventana deslizante) para reducir ruido.
* **Registro y resumen de la sesión** (emoción media + porcentajes) y subida a **MongoDB** (`LabasAppDB.LogsEmociones`).

---

## 2) Estructura

```
emociones/
├─ cascades/
│  └─ haarcascade_frontalface_default.xml  # detector de rostro (OpenCV)
├─ detector_edad.py           # script local (selección de la cara "mayor" por estimación de edad)
├─ detector_emociones.py      # script local (detección + gráficos + logs + subida a Mongo)
├─ uploader_mongo.py          # helper para subir JSON de logs a Mongo Atlas
├─ urls.py                    # rutas Django del microservicio emocional
└─ views.py                   # endpoints REST (seleccionar ROI / detectar / finalizar)
```

---

## 3) Endpoints Django (uso desde la videollamada)

Añade `path('emociones/', include('emociones.urls'))` en `urls.py` del proyecto.

### 3.1 `POST /emociones/seleccionar/`

**Body** `{ image_base64, room }`  → Detecta **todos** los rostros en el frame con Haarcascade y devuelve la **ROI con mayor área** (x,y,w,h) junto con un recorte de esa cara en base64. Guarda la ROI en memoria por `room`.

**Response** `{ roi: {x,y,w,h}, face_base64 }`

### 3.2 `POST /emociones/detectar/`

**Body** `{ image_base64, room }`  → Recorta con la ROI almacenada, redimensiona y llama a `DeepFace.analyze(..., actions=['emotion'])`. Devuelve `{ emocion, confianza }` y acumula en un log en memoria para esa `room`.

### 3.3 `POST /emociones/finalizar/`

**Body** `{ room, identity }` → Calcula **emoción media** y **porcentajes** a partir del log en memoria; escribe `log_final_*.json` en `emociones/logs/` y lo **sube a MongoDB Atlas** (`LabasAppDB.LogsEmociones`). Limpia el estado en memoria para esa sala.

> **Notas**
>
> * Todos los endpoints son `POST`. Envían `405` si se usan con otro método.
> * Errores devuelven JSON `{error, detalle?}` con códigos 4xx/5xx.
> * El estado en memoria se indexa por `room` → en despliegues con múltiples workers/instancias, usar almacenamiento compartido (p. ej., **Redis**) para evitar incoherencias.

---

## 4) Flujo típico de integración (front de videollamada)

1. **ROI inicial**: capturar un frame del `<video>` local y enviar a `/emociones/seleccionar/`.
2. **Bucle de inferencia**: cada N ms, capturar un frame, enviarlo a `/emociones/detectar/` y pintar en UI la emoción devuelta.
3. **Cierre**: al colgar o al desactivar la opción de “detectar emociones”, llamar a `/emociones/finalizar/` con `room` e `identity`.

### Snippets de ejemplo (JS)

```js
// 1) Capturar frame como base64 JPEG
function videoToBase64(videoEl) {
  const c = document.createElement('canvas');
  c.width = videoEl.videoWidth; c.height = videoEl.videoHeight;
  const ctx = c.getContext('2d');
  ctx.drawImage(videoEl, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.7); // base64
}

// 2) Seleccionar ROI
const frame = videoToBase64(myVideoEl);
await fetch('/emociones/seleccionar/', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ image_base64: frame, room })
});

// 3) Bucle de detección
let running = true;
async function loop() {
  if (!running) return;
  const f = videoToBase64(myVideoEl);
  const res = await fetch('/emociones/detectar/', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ image_base64: f, room })
  }).then(r=>r.json());
  // res: {emocion, confianza}
  updateUI(res);
  setTimeout(loop, 500); // cada 500ms (ajustable)
}
loop();

// 4) Finalizar y guardar resumen
await fetch('/emociones/finalizar/', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ room, identity })
});
```

---

## 5) Scripts standalone (para pruebas locales)

> Útiles para depurar la cámara, visualizar métricas y confirmar el pipeline sin pasar por el front.

### 5.1 `detector_edad.py`

* **Objetivo**: elegir de forma semiautomática la **cara de mayor edad** presente en la escena.
* **Cómo**: lee `num_frames`, estima edad por DeepFace y agrupa por centroides; selecciona la media más alta y **pide confirmación** por teclado (Windows, `msvcrt`).
* **Salida**: `(x,y,w,h)` de la ROI confirmada.
* **Uso**:

```bash
python emociones/detector_edad.py
# En la ventana: pulsa 'q' para salir o 'S'/'N' en la consola para confirmar/repetir
```

### 5.2 `detector_emociones.py`

* **Objetivo**: detectar emociones en tiempo real sobre una ROI (opcional) y generar **gráficos** + **logs**.
* **Características**:

  * Buffer circular de `frame_buffer_size` frames y **dominancia** con umbral `threshold_dominance`.
  * **EWMA** (`alpha_smooth`) para suavizado de probabilidades.
  * Botón overlay “**Media y Salir**” en la ventana OpenCV.
  * Hilo con **Matplotlib** para histograma en vivo y **guardado periódico** de gráficas.
  * Al terminar, calcula la **emoción media** y sube `log_final_*.json` a Mongo (usa `uploader_mongo.py`).
* **Uso**:

```bash
python emociones/detector_emociones.py
# Parámetros a editar en cabecera: camera_index, base_dir, detection_interval, etc.
# Si quieres forzar una ROI fija, pasa roi_bbox=(x,y,w,h) dentro del código.
```

### 5.3 `uploader_mongo.py`

* **Función**: `subir_log_a_mongo(path_log_json, mongo_uri, nombre_db, nombre_coleccion)`.
* Inserta el JSON y añade `_subido_en` (UTC) a cada documento.

---

## 6) Dependencias

* **Python**: `opencv-python`, `deepface`, `numpy`, `matplotlib`, `pymongo`, `django`.
* **DeepFace** requiere backend de DL (p. ej., **tensorflow**). Considera instalar versiones con soporte **CPU/GPU** según despliegue.
* **Archivos**: `emociones/cascades/haarcascade_frontalface_default.xml`.

---

## 7) Variables de entorno y settings

* `MONGO_URI` → conexión a **MongoDB Atlas** (leída en `views.finalizar_emocion_sesion`).
* `BASE_DIR` (ajustado en settings de Django) para construir rutas de cascades y carpeta de logs.
* **Recomendado**: mover cualquier credencial hardcodeada en scripts locales a variables de entorno.

---

## 8) Rendimiento y calidad

* **ROI fija** → recortar reduce cómputo y estabiliza la inferencia.
* Ajusta `detection_interval` (p. ej., 2–4 frames) y el **tamaño de entrada** (224×224) para equilibrar **latencia** vs **precisión**.
* Con `enforce_detection=False` DeepFace evita excepciones cuando el recorte es ruidoso, a costa de alguna degradación.
* En producción, limita el tamaño del `image_base64` (p. ej. **720p** máx.) y usa compresión `toDataURL('image/jpeg', 0.6–0.8)`.

---

## 9) Seguridad y despliegue

* Servir siempre por **HTTPS**.
* **CSRF**: los endpoints están `@csrf_exempt` para facilitar captura desde JS; si el front está en el mismo dominio, habilita CSRF.
* **Escalado**: `SESION_ROI`/`SESION_LOGS` viven en memoria del proceso. Usa **Redis** o DB si desplegarás múltiples instancias o si no quieres perder estado en reinicios.
* **Privacidad**: no persistir frames de vídeo; solo logs agregados.

---

## 10) Quickstart local (con Django)

1. Instala dependencias: `pip install deepface opencv-python numpy matplotlib pymongo`.
2. Añade `emociones` a `INSTALLED_APPS` y configura `MONGO_URI`.
3. Incluye rutas: `path('emociones/', include('emociones.urls'))`.
4. Arranca `runserver` y prueba con un cliente JS que envíe frames base64.
5. Verifica en Atlas los documentos en `LabasAppDB.LogsEmociones`.

---

## 11) Limitaciones y mejoras futuras

* La selección de ROI por **mayor área** no siempre coincide con *la persona de mayor edad*. Puedes integrar `detector_edad.py` como paso inicial opcional.
* Migrar el **estado de sesión** a Redis y añadir **expiración** por `room`.
* Endpoint para **streaming de métricas** (SSE/WebSocket) en lugar de *polling*.
* Exponer **configuraciones** (intervalos, umbrales) vía settings/ENV o admin.
* Pruebas automatizadas con frames de ejemplo y *golden logs*.

---

### Checklist

* [ ] `emociones` en `INSTALLED_APPS`.
* [ ] `MONGO_URI` configurado.
* [ ] Rutas incluidas.
* [ ] Front captura frames base64 y consume `seleccionar` → `detectar` → `finalizar`.
* [ ] Verificación de documentos en Atlas tras una llamada.
