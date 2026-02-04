import cv2
from deepface import DeepFace
import time
import msvcrt  # para entrada desde consola en Windows

# Cargamos el clasificador Haar Cascade
face_cascade = cv2.CascadeClassifier(
    cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
)

def select_oldest_face(
    cap,
    num_frames: int = 60,
    frame_width: int = 640,
    frame_height: int = 480
):
    """
    Detecta la cara de mayor edad muestreando `num_frames` frames.
    Una vez estimada la cara más vieja, muestra la cámara en vivo con el
    ROI marcado y pide confirmación por consola (S/N) sin congelar la imagen.
    """
    while True:
        age_data = {}  # key: centroid, value: [sum_age, count, last_box]

        # 1) Recolectar datos de edad
        for _ in range(num_frames):
            ret, frame = cap.read()
            if not ret:
                return None
            frame = cv2.resize(frame, (frame_width, frame_height))
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(100, 100)
            )

            for (x, y, w, h) in faces:
                roi = frame[y:y+h, x:x+w]
                try:
                    result = DeepFace.analyze(
                        roi,
                        actions=['age'],
                        enforce_detection=False
                    )[0]
                    age = result.get('age', 0)
                except Exception:
                    continue

                cx, cy = x + w // 2, y + h // 2
                # Agrupar por centroid cercano
                key = None
                for (px, py) in list(age_data.keys()):
                    if abs(px - cx) < w/2 and abs(py - cy) < h/2:
                        key = (px, py)
                        break

                if key is None:
                    key = (cx, cy)
                    age_data[key] = [0, 0, (x, y, w, h)]

                age_data[key][0] += age
                age_data[key][1] += 1
                age_data[key][2] = (x, y, w, h)

            # Mostrar feed en vivo durante recolección
            cv2.imshow("Selección de edad", frame)
            if cv2.waitKey(1) & 0xFF == ord('q'):
                cv2.destroyAllWindows()
                return None

        if not age_data:
            continue

        # 2) Calcular edad media y escoger la más alta
        avg_ages = {k: v[0] / v[1] for k, v in age_data.items()}
        oldest_centroid = max(avg_ages, key=avg_ages.get)
        x, y, w, h = age_data[oldest_centroid][2]

        # 3) Confirmación con ROI en vivo y lectura por consola
        print("¿Es usted la persona de mayor edad? (S/N): ", end='', flush=True)
        while True:
            ret, frame = cap.read()
            if not ret:
                return None
            frame = cv2.resize(frame, (frame_width, frame_height))
            # Dibujar ROI en verde
            cv2.rectangle(frame, (x, y), (x + w, y + h), (0, 255, 0), 2)
            cv2.putText(
                frame,
                "Persona seleccionada",
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.7,
                (255, 255, 255),
                2
            )
            cv2.imshow("Selección de edad", frame)

            # Esperar confirmación desde consola sin bloquear la cámara
            if msvcrt.kbhit():
                ch = msvcrt.getwch().lower()
                if ch == 's':
                    cv2.destroyWindow("Selección de edad")
                    return (x, y, w, h)
                elif ch == 'n':
                    print('N - Rehaciendo detección...')
                    break

            if cv2.waitKey(1) & 0xFF == ord('q'):
                cv2.destroyAllWindows()
                return None

# Ejemplo de uso
if __name__ == "__main__":
    cap = cv2.VideoCapt