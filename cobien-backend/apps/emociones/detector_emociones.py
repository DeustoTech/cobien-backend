import cv2
from deepface import DeepFace
from collections import deque, Counter
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
import threading
import time
import os
import json
import glob
import sys
from uploader_mongo import subir_log_a_mongo

def run_emotion_detector(
    camera_index: int = 0,
    base_dir: str = "C:/Users/Jaime/Mast-TFM/Emociones",
    frame_buffer_size: int = 120,
    detection_interval: int = 1,
    threshold_dominance: float = 0.8,
    alpha_smooth: float = 0.3,
    save_interval: int = 60,
    roi_bbox: tuple = None
):
    # Directorios
    log_dir   = os.path.join(base_dir, "logs")
    graph_dir = os.path.join(base_dir, "graficas")
    os.makedirs(log_dir, exist_ok=True)
    os.makedirs(graph_dir, exist_ok=True)
    # Limpiar pruebas previas
    for f in glob.glob(os.path.join(log_dir,   "*")): os.remove(f)
    for f in glob.glob(os.path.join(graph_dir, "*")): os.remove(f)

    # Variables de estado
    emotion_buffer      = deque(maxlen=frame_buffer_size)
    emotion_history     = []
    emotion_ewma        = None
    last_detected_emotion = None
    last_save_time      = time.time()
    current_frame_emotion = "..."

    # Detector de caras
    face_cascade = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    # Botón “Media y Salir”
    button_rect    = None
    button_clicked = False
    def on_mouse(evt, x, y, flags, param):
        nonlocal button_clicked, button_rect
        if evt == cv2.EVENT_LBUTTONDOWN and button_rect:
            x1,y1,x2,y2 = button_rect
            if x1 <= x <= x2 and y1 <= y <= y2:
                button_clicked = True

    cv2.namedWindow("Detección de emociones + Métricas")
    cv2.setMouseCallback("Detección de emociones + Métricas", on_mouse)

    # Gráfico en hilo aparte
    def update_chart(i):
        plt.cla()
        if emotion_history:
            full_counts = dict(Counter(emotion_history))
            plt.bar(list(full_counts.keys()), list(full_counts.values()), color='skyblue')
            plt.title('Frecuencia de emociones durante la llamada')
            plt.ylabel('Recuento')
            plt.tight_layout()
    
    def run_plot():
        ani = FuncAnimation(plt.gcf(), update_chart, interval=1000)
        plt.tight_layout()
        plt.show()

    # ——— Arrancamos el hilo daemon antes del bucle principal ———
    threading.Thread(target=run_plot, daemon=True).start()

    cap = cv2.VideoCapture(camera_index)
    frame_count = 0

    # Bucle principal
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame = cv2.flip(frame, 1)
        frame_count += 1

        if frame_count % detection_interval == 0:
            try:
                # 1) Recorte ROI fijo si se pasó roi_bbox, o detección habitual
                if roi_bbox:
                    x, y, w, h = roi_bbox
                    margin = 10
                    x1 = max(x - margin, 0)
                    y1 = max(y - margin, 0)
                    x2 = min(x + w + margin, frame.shape[1])
                    y2 = min(y + h + margin, frame.shape[0])
                    face_roi = frame[y1:y2, x1:x2]
                    # Dibuja rectángulo ROJO sobre la ROI fija
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 0, 255), 2)
                else:
                    gray  = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    faces = face_cascade.detectMultiScale(gray, 1.1, 5, minSize=(100,100))
                    if not len(faces):
                        continue
                    x, y, w, h = faces[0]
                    face_roi = frame[y:y+h, x:x+w]
                    # Dibuja rectángulo VERDE en detección dinámica
                    cv2.rectangle(frame, (x, y), (x+w, y+h), (0,255,0), 2)


                # 2) Analizar emociones
                df = DeepFace.analyze(face_roi, actions=['emotion'], enforce_detection=False)[0]
                probs = df["emotion"]
                # EWMA
                if emotion_ewma is None:
                    emotion_ewma = dict(probs)
                else:
                    for emo,p in probs.items():
                        emotion_ewma[emo] = alpha_smooth*p + (1-alpha_smooth)*emotion_ewma.get(emo,0)

                smoothed = max(emotion_ewma, key=emotion_ewma.get)
                current_frame_emotion = smoothed
                emotion_buffer.append(smoothed)
                emotion_history.append(smoothed)

                # 3) Guardado periódico gráfico
                now = time.time()
                if now - last_save_time >= save_interval:
                    ts = time.strftime("%Y%m%d-%H%M%S")
                    path = os.path.join(graph_dir, f"grafico_{ts}.png")
                    plt.cla()
                    counts = dict(Counter(emotion_history))
                    plt.bar(list(counts.keys()), list(counts.values()), color='skyblue')
                    plt.tight_layout()
                    plt.savefig(path)
                    plt.close()
                    last_save_time = now

                # 4) Dominancia sliding window
                if len(emotion_buffer) >= frame_buffer_size:
                    most, cnt = Counter(emotion_buffer).most_common(1)[0]
                    ratio = cnt/frame_buffer_size
                    if ratio >= threshold_dominance and most != last_detected_emotion:
                        print(f"Emoción dominante detectada: {most} ({round(ratio*100)}%)")
                        ts = time.strftime("%Y%m%d-%H%M%S")
                        # Log JSON
                        log = {
                            "timestamp": ts,
                            "emocion_dominante": most,
                            "porcentaje_dominancia": round(ratio*100),
                            "historial_emociones": list(emotion_buffer)
                        }
                        with open(os.path.join(log_dir, f"log_sesion_{ts}.json"), "w", encoding="utf-8") as f:
                            json.dump(log, f, indent=4)
                        # Gráfico de momento
                        path = os.path.join(graph_dir, f"grafico_{ts}.png")
                        plt.bar(*zip(*Counter(emotion_buffer).most_common(3)), color='skyblue')
                        plt.tight_layout()
                        plt.savefig(path)
                        plt.close()
                        last_detected_emotion = most

            except Exception as e:
                current_frame_emotion = "Error"
                print("Error analizando emociones:", e)

        # Mostrar info
        cv2.putText(frame, f"Emocion actual: {current_frame_emotion}", (10,40),
                    cv2.FONT_HERSHEY_SIMPLEX, 1, (50,255,50), 2)
        y0 = 80
        for emo,val in dict(Counter(emotion_buffer).most_common(3)).items():
            cv2.putText(frame, f"{emo}: {val} veces", (10,y0),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
            y0 += 30

        # Botón “Media y Salir”
        h,w = frame.shape[:2]
        bw,bh,mg = 200,40,10
        x1 = w-bw-mg; y1=mg; x2=w-mg; y2=mg+bh
        button_rect = (x1,y1,x2,y2)
        cv2.rectangle(frame,(x1,y1),(x2,y2),(50,50,50),cv2.FILLED)
        cv2.putText(frame,"Media y Salir",(x1+10,y1+25),
                    cv2.FONT_HERSHEY_SIMPLEX,0.7,(255,255,255),2)

        cv2.imshow("Detección de emociones + Métricas", frame)
        if cv2.waitKey(1)&0xFF==ord('q') or button_clicked:
            break

    # Al cerrar: resumen final
    caps = cv2.VideoCapture(camera_index)  # reusar o usar el mismo cap
    caps.release()
    cv2.destroyAllWindows()

    files = glob.glob(os.path.join(log_dir, "log_sesion_*.json"))
    dominant = []
    for fn in files:
        with open(fn, "r", encoding="utf-8") as f:
            dom = json.load(f).get("emocion_dominante")
            if dom: dominant.append(dom)
    if dominant:
        media = Counter(dominant).most_common(1)[0][0]
        summary = {
            "timestamp": time.strftime("%Y%m%d-%H%M%S"),
            "emociones_dominantes": dominant,
            "emocion_media": media
        }
        final = os.path.join(log_dir, f"log_final_{summary['timestamp']}.json")
        with open(final, "w", encoding="utf-8") as f:
            json.dump(summary, f, indent=4, ensure_ascii=False)
        print(f"Emoción media de la llamada: {media}")
        print(f"Log resumen guardado en: {final}")

        MONGO_URI = "mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents"
        NOMBRE_DB = "LabasAppDB"
        NOMBRE_COLECCION = "LogsEmociones"

        exito = subir_log_a_mongo(
            path_log_json=final,
            mongo_uri=MONGO_URI,
            nombre_db=NOMBRE_DB,
            nombre_coleccion=NOMBRE_COLECCION
        )
        if exito:
            print("[Info] log_final.json se subió correctamente a LabasAppDB.LogsEmociones.")
        else:
            print("[Error] Hubo problemas al subir log_final.json a MongoDB Atlas.")
    else:
        print("No se encontraron logs de emoción dominante.")


    # Cierra TODAS las ventanas de OpenCV
    cv2.destroyAllWindows()
    # Cierra TODAS las figuras de Matplotlib
    plt.close('all')
    sys.exit(0)


if __name__ == "__main__":
    run_emotion_detector()
