# README — Emotion AI for Video Calls (`/emociones`)

> Emotion analysis module designed to integrate with the video‑calling screen. It includes **Django endpoints** to be called from the Twilio Video front end and **standalone scripts** for local testing and log generation. Session summaries are stored in **MongoDB Atlas**.

---

## 1) What it solves

* **Select a reference face (ROI)** for the video‑call session.
* **Real‑time emotion detection** on that ROI using **DeepFace** (pre‑trained CNNs) and **OpenCV**.
* **Smoothing and aggregation** of results (EWMA + sliding window) to reduce noise.
* **Session logging and summary** (average emotion + percentages) and **upload to MongoDB** (`LabasAppDB.LogsEmociones`).

---

## 2) Structure

```
emociones/
├─ cascades/
│  └─ haarcascade_frontalface_default.xml  # face detector (OpenCV)
├─ detector_edad.py           # local script (select the "oldest" face by estimated age)
├─ detector_emociones.py      # local script (detection + charts + logs + upload to Mongo)
├─ uploader_mongo.py          # helper to upload JSON logs to Mongo Atlas
├─ urls.py                    # Django routes for the emotion microservice
└─ views.py                   # REST endpoints (select ROI / detect / finalize)
```

---

## 3) Django endpoints (used by the video call UI)

Add `path('emociones/', include('emociones.urls'))` to your project `urls.py`.

### 3.1 `POST /emociones/seleccionar/`

**Body** `{ image_base64, room }` → Detects **all** faces in the frame using Haarcascade and returns the **largest‑area ROI** `(x,y,w,h)` plus a base64 crop of that face. Stores the ROI in memory keyed by `room`.

**Response** `{ roi: {x,y,w,h}, face_base64 }`

### 3.2 `POST /emociones/detectar/`

**Body** `{ image_base64, room }` → Crops using the stored ROI, resizes, and calls `DeepFace.analyze(..., actions=['emotion'])`. Returns `{ emotion, confidence }` and appends the result to an in‑memory log for that `room`.

### 3.3 `POST /emociones/finalizar/`

**Body** `{ room, identity }` → Computes **average emotion** and **percentages** from the in‑memory log; writes `log_final_*.json` under `emociones/logs/` and **uploads to MongoDB Atlas** (`LabasAppDB.LogsEmociones`). Cleans the in‑memory state for that room.

> **Notes**
>
> * All endpoints are `POST`. They return `405` if called with another method.
> * Errors return JSON `{error, details?}` with 4xx/5xx status codes.
> * In‑memory state is keyed by `room` → with multiple workers/instances, use shared storage (e.g., **Redis**) to avoid inconsistency.

---

## 4) Typical integration flow (video‑call front end)

1. **Initial ROI**: capture a frame from the local `<video>` and send it to `/emociones/seleccionar/`.
2. **Inference loop**: every N ms, capture a frame, send it to `/emociones/detectar/`, and update the UI with the returned emotion.
3. **Close**: when hanging up or when the “detect emotions” option is disabled, call `/emociones/finalizar/` with `room` and `identity`.

### Example snippets (JS)

```js
// 1) Capture a frame as base64 JPEG
function videoToBase64(videoEl) {
  const c = document.createElement('canvas');
  c.width = videoEl.videoWidth; c.height = videoEl.videoHeight;
  const ctx = c.getContext('2d');
  ctx.drawImage(videoEl, 0, 0, c.width, c.height);
  return c.toDataURL('image/jpeg', 0.7); // base64
}

// 2) Select ROI
const frame = videoToBase64(myVideoEl);
await fetch('/emociones/seleccionar/', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ image_base64: frame, room })
});

// 3) Detection loop
let running = true;
async function loop() {
  if (!running) return;
  const f = videoToBase64(myVideoEl);
  const res = await fetch('/emociones/detectar/', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({ image_base64: f, room })
  }).then(r=>r.json());
  // res: {emotion, confidence}
  updateUI(res);
  setTimeout(loop, 500); // every 500ms (adjust as needed)
}
loop();

// 4) Finalize and save the summary
await fetch('/emociones/finalizar/', {
  method: 'POST', headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({ room, identity })
});
```

---

## 5) Standalone scripts (for local testing)

> Useful to debug the camera, visualize metrics, and validate the pipeline without going through the front end.

### 5.1 `detector_edad.py`

* **Goal**: semi‑automatically pick the **oldest face** present in the scene.
* **How**: reads `num_frames`, estimates age with DeepFace, groups by centroids, picks the highest mean, and **asks for keyboard confirmation** (Windows, `msvcrt`).
* **Output**: `(x,y,w,h)` of the confirmed ROI.
* **Usage**:

```bash
python emociones/detector_edad.py
# In the window: press 'q' to quit or 'Y'/'N' in the console to confirm/retry
```

### 5.2 `detector_emociones.py`

* **Goal**: detect emotions in real time on an (optional) ROI and generate **charts** + **logs**.
* **Features**:
  * Circular buffer of `frame_buffer_size` frames and **dominance** with threshold `threshold_dominance`.
  * **EWMA** (`alpha_smooth`) to smooth probabilities.
  * Overlay button “**Average and Exit**” in the OpenCV window.
  * Thread with **Matplotlib** for live histogram and **periodic saving** of charts.
  * When finishing, computes the **average emotion** and uploads `log_final_*.json` to Mongo (uses `uploader_mongo.py`).

* **Usage**:

```bash
python emociones/detector_emociones.py
# Edit header params: camera_index, base_dir, detection_interval, etc.
# If you want to force a fixed ROI, set roi_bbox=(x,y,w,h) in the code.
```

### 5.3 `uploader_mongo.py`

* **Function**: `subir_log_a_mongo(path_log_json, mongo_uri, nombre_db, nombre_coleccion)`.
* Inserts the JSON and adds `_subido_en` (UTC) to each document.

---

## 6) Dependencies

* **Python**: `opencv-python`, `deepface`, `numpy`, `matplotlib`, `pymongo`, `django`.
* **DeepFace** requires a DL backend (e.g., **tensorflow**). Consider CPU/GPU builds according to your deployment.
* **Files**: `emociones/cascades/haarcascade_frontalface_default.xml`.

---

## 7) Environment variables & settings

* `MONGO_URI` → connection to **MongoDB Atlas** (read in `views.finalizar_emocion_sesion`).
* `BASE_DIR` (set in Django settings) to build cascade paths and the logs folder.
* **Recommended**: move any hard‑coded credentials in local scripts to environment variables.

---

## 8) Performance & quality

* **Fixed ROI** → cropping reduces compute and stabilizes inference.
* Tune `detection_interval` (e.g., 2–4 frames) and the **input size** (224×224) to balance **latency** vs **accuracy**.
* With `enforce_detection=False`, DeepFace avoids exceptions when the crop is noisy, at the cost of some degradation.
* In production, limit `image_base64` size (e.g., **720p** max) and use compression `toDataURL('image/jpeg', 0.6–0.8)`.

---

## 9) Security & deployment

* Always serve over **HTTPS**.
* **CSRF**: endpoints are `@csrf_exempt` for easier access from JS; if the front is on the same domain, enable CSRF.
* **Scaling**: `SESION_ROI`/`SESION_LOGS` live in process memory. Use **Redis** or DB when deploying multiple instances or to avoid losing state on restarts.
* **Privacy**: do not persist video frames; only aggregated logs.

---

## 10) Local quickstart (with Django)

1. Install dependencies: `pip install deepface opencv-python numpy matplotlib pymongo`.
2. Add `emociones` to `INSTALLED_APPS` and set `MONGO_URI`.
3. Include routes: `path('emociones/', include('emociones.urls'))`.
4. Run `runserver` and test with a JS client that sends base64 frames.
5. Check Atlas documents in `LabasAppDB.LogsEmociones`.

---

## 11) Limitations & future work

* Selecting ROI by **largest area** doesn’t always match *the oldest person*. You can integrate `detector_edad.py` as an optional initial step.
* Migrate session state to **Redis** and add **expiration** per `room`.
* Add an endpoint for **metric streaming** (SSE/WebSocket) instead of polling.
* Expose **configuration** (intervals, thresholds) via settings/ENV or admin.
* Automated tests with sample frames and *golden logs*.

---

### Checklist

* [ ] `emociones` in `INSTALLED_APPS`.
* [ ] `MONGO_URI` configured.
* [ ] Routes included.
* [ ] Front end captures base64 frames and calls `seleccionar` → `detectar` → `finalizar`.
* [ ] Atlas documents verified after a call.
