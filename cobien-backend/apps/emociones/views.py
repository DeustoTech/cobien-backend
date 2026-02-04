from django.shortcuts import render
# emociones/views.py (esquema simplificado)
import base64, json
import numpy as np
import os
import cv2
from deepface import DeepFace
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from .uploader_mongo import subir_log_a_mongo
from datetime import datetime
from collections import Counter
from django.conf import settings

SESION_ROI = {}
SESION_LOGS = {}

@csrf_exempt
def seleccionar_rostro_mayor(request):
    """
    Endpoint liviano para detectar *todos* los rostros con CascadeClassifier,
    elegir por mayor área (w*h) y devolver esa ROI en base64,
    evitando cargar modelos pesados de TF para edad.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    try:
        # 1) Leer JSON
        body = json.loads(request.body)
        img_data = body.get('image_base64', '')
        room = body.get('room', None)
        if not room:
            return JsonResponse({'error': 'Falta parámetro "room".'}, status=400)

        # 2) Decodificar base64 → bytes JPEG
        if ',' in img_data:
            img_data = img_data.split(',', 1)[1]
        try:
            jpeg_bytes = base64.b64decode(img_data)
        except Exception:
            return JsonResponse({'error': 'Imagen base64 inválida.'}, status=400)

        # 3) Convertir bytes a BGR de OpenCV
        nparr = np.frombuffer(jpeg_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return JsonResponse({'error': 'No se pudo decodificar la imagen.'}, status=400)

        # 4) Convertir a escala de grises para el Haarcascade
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # 5) Cargar el Haarcascade desde la carpeta local "emociones/cascades/"
        cascade_path = os.path.join(
            settings.BASE_DIR,
            'emociones',
            'cascades',
            'haarcascade_frontalface_default.xml'
        )
        face_cascade = cv2.CascadeClassifier(cascade_path)
        if face_cascade.empty():
            return JsonResponse({'error': 'No se pudo cargar Haarcascade.'}, status=500)

        # 6) Detectar todos los rostros
        rects = face_cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5)
        if len(rects) == 0:
            return JsonResponse({'error': 'No se detectaron rostros.'}, status=400)

        # 7) Elegir la caja (x,y,w,h) con mayor área (w*h)
        mejor_area = -1
        mejor_rect = None
        for (x, y, w, h) in rects:
            area = int(w) * int(h)
            if area > mejor_area:
                mejor_area = area
                mejor_rect = (x, y, w, h)

        if mejor_rect is None:
            return JsonResponse({'error': 'No se pudo determinar la ROI.'}, status=500)

        # Extraemos y convertimos a int nativos de Python
        x_sel, y_sel, w_sel, h_sel = mejor_rect
        x_sel = int(x_sel)
        y_sel = int(y_sel)
        w_sel = int(w_sel)
        h_sel = int(h_sel)

        # 8) Recortar la cara más grande
        cara_crop = frame[y_sel:y_sel + h_sel, x_sel:x_sel + w_sel]
        if cara_crop is None or cara_crop.size == 0:
            return JsonResponse({'error': 'Error al recortar la cara.'}, status=500)

        # 9) Codificar el recorte a JPEG y luego a base64
        _, jpeg_crop = cv2.imencode(".jpg", cara_crop)
        base64_crop = base64.b64encode(jpeg_crop.tobytes()).decode('utf-8')
        face_base64 = "data:image/jpeg;base64," + base64_crop

        # 10) Guardar coordenadas y lista vacía de logs para esta sala
        SESION_ROI[room] = (x_sel, y_sel, w_sel, h_sel)
        SESION_LOGS[room] = []

        # Al convertir todo a int, ya podemos serializar sin problemas
        return JsonResponse({
            'roi': {'x': x_sel, 'y': y_sel, 'w': w_sel, 'h': h_sel},
            'face_base64': face_base64
        })

    except Exception as e:
        # Capturamos cualquier excepción inesperada y la imprimimos en logs
        import traceback
        traceback_str = traceback.format_exc()
        print("=== Error en seleccionar_rostro_mayor ===\n", traceback_str)
        return JsonResponse({'error': 'Error interno del servidor.', 'detalle': str(e)}, status=500)
    
@csrf_exempt
def detectar_emocion_superpuestos(request):
    """
    Recibe POST con JSON { "image_base64": "...", "room": "..." }.
    1) Decodifica el frame.
    2) Recorta según SESION_ROI[room].
    3) Redimensiona el recorte a un tamaño más pequeño.
    4) Llama a DeepFace.analyze(...) para obtener emoción.
    5) Guarda resultado en SESION_LOGS y devuelve { 'emocion': ..., 'confianza': ... }.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    try:
        body = json.loads(request.body)
        img_data = body.get('image_base64', '')
        room = body.get('room', None)
        if not room:
            return JsonResponse({'error': 'Falta parámetro "room".'}, status=400)
        if room not in SESION_ROI:
            return JsonResponse({'error': 'No existe ROI para esta sala.'}, status=400)

        # 1) Decodificar base64
        if ',' in img_data:
            img_data = img_data.split(',', 1)[1]
        try:
            jpg_bytes = base64.b64decode(img_data)
        except Exception:
            return JsonResponse({'error': 'Imagen base64 inválida.'}, status=400)

        nparr = np.frombuffer(jpg_bytes, np.uint8)
        frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if frame is None:
            return JsonResponse({'error': 'No se pudo decodificar la imagen.'}, status=400)

        # 2) Recortar usando la ROI almacenada
        x, y, w, h = SESION_ROI[room]
        height, width = frame.shape[:2]
        if x < 0 or y < 0 or x + w > width or y + h > height:
            return JsonResponse({'error': 'ROI fuera de rango.'}, status=400)
        cara = frame[y:y + h, x:x + w]
        if cara.size == 0:
            return JsonResponse({'error': 'Error al recortar la cara.'}, status=400)

        # 3) Redimensionar a un tamaño más pequeño para DeepFace (p. ej. 224×224)
        try:
            cara_resized = cv2.resize(cara, (224, 224))
        except Exception:
            cara_resized = cara

        # 4) Analizar emoción con DeepFace
        try:
            resultado = DeepFace.analyze(
                img_path=cara_resized,
                actions=['emotion'],
                enforce_detection=False
            )
            # Si DeepFace devuelve una lista, tomamos el primer elemento
            if isinstance(resultado, list) and len(resultado) > 0:
                resultado = resultado[0]
            dominante = resultado.get('dominant_emotion', None)
            confianza = 0.0
            if dominante is not None and 'emotion' in resultado:
                confianza = resultado['emotion'].get(dominante, 0.0)
        except Exception as e:
            # Imprimir traza en servidor para depuración
            import traceback; traceback.print_exc()
            return JsonResponse({'error': 'Error analizando emoción.', 'detalle': str(e)}, status=500)

        # 5) Guardar en SESION_LOGS[room]
        ahora = datetime.utcnow().isoformat()
        SESION_LOGS[room].append({
            'timestamp': ahora,
            'emocion': dominante,
            'confianza': float(confianza)
        })

        return JsonResponse({
            'emocion': dominante,
            'confianza': float(confianza)
        })

    except Exception as e:
        import traceback
        traceback_str = traceback.format_exc()
        print("=== Error en detectar_emocion_superpuestos ===\n", traceback_str)
        return JsonResponse({'error': 'Error interno del servidor.', 'detalle': str(e)}, status=500)
    
@csrf_exempt
def finalizar_emocion_sesion(request):
    """
    Llamado cuando se deshabilita la detección en el front.
    Genera un log_final.json con:
      - timestamps inicio/fin
      - emoción media (basada en SESION_LOGS[room])
      - porcentaje por emoción
    Lo guarda en un archivo local (opcional) y lo sube a Mongo con subir_log_a_mongo().
    Luego borra la sesión de memoria.
    """
    if request.method != 'POST':
        return JsonResponse({'error': 'Método no permitido'}, status=405)

    data = json.loads(request.body)
    room = data.get('room', 'default_room')
    identity = data.get('identity', 'anonimo')

    if room not in SESION_LOGS:
        return JsonResponse({'error': 'No hay log emocional para esta sala.'}, status=400)

    registros = SESION_LOGS[room]  # lista de diccionarios {'timestamp', 'emocion', 'confianza'}
    if len(registros) == 0:
        return JsonResponse({'error': 'No se detectó ninguna emoción en la sesión.'}, status=400)

    # Armar el resumen
    emos = [r['emocion'] for r in registros]
    conteo = Counter(emos)
    emocion_media = conteo.most_common(1)[0][0]

    resumen = {
        'identity': identity,
        'room': room,
        'timestamp_inicio': registros[0]['timestamp'],
        'timestamp_fin': registros[-1]['timestamp'],
        'emocion_media': emocion_media,
        'porcentajes_por_emocion': {k: v / len(emos) for k, v in conteo.items()},
        'registros': registros
    }

    # Guardar localmente (opcional; si no quieres el archivo fíjalo a False)
    ts_str = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    nombre_arch = f"log_final_{identity}_{room}_{ts_str}.json"
    carpeta_logs = os.path.join(settings.BASE_DIR, "emociones", "logs")
    os.makedirs(carpeta_logs, exist_ok=True)
    ruta_local = os.path.join(carpeta_logs, nombre_arch)
    with open(ruta_local, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=4, ensure_ascii=False)

    # Subir a MongoDB Atlas
    mongo_uri = os.getenv('MONGO_URI')
    ok = subir_log_a_mongo(
        path_log_json=ruta_local,
        mongo_uri=mongo_uri,
        nombre_db="LabasAppDB",
        nombre_coleccion="LogsEmociones"
    )

    # Limpiar memoria de esa sala
    del SESION_ROI[room]
    del SESION_LOGS[room]

    return JsonResponse({'success': True, 'mongo_ok': ok})

