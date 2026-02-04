from google.cloud import vision
import re


ruta_imagen = r'C:\Users\Jaime\Proyecto_CoBien\backend-web\ImagenesLectura\Imagen3.png'
def procesar_imagen_google_vision(ruta_imagen):
    # Crea un cliente de Vision
    client = vision.ImageAnnotatorClient()

    # Carga la imagen desde el archivo
    with open(ruta_imagen, 'rb') as image_file:
        content = image_file.read()
    image = vision.Image(content=content)

    # Llama a la API para detectar texto
    response = client.text_detection(image=image)

    # Verifica errores
    if response.error.message:
        raise Exception(f'Error de Google Vision API: {response.error.message}')

    # Extrae el texto detectado
    texto = response.text_annotations[0].description if response.text_annotations else ""
    return texto

def extraer_informacion(texto):
    eventos = []
    horarios = []
    lugares = []

    # Detectar nombres de eventos (mayúsculas y líneas largas)
    for linea in texto.splitlines():
        if linea.isupper() and len(linea) > 3:
            eventos.append(linea.strip())

    # Buscar horarios (formato típico HH:MM AM/PM)
    horarios = re.findall(r'\d{1,2}:\d{2} ?(?:AM|PM|am|pm|p\.m\.|a\.m\.)?', texto)

    # Buscar ubicaciones por palabras clave
    for linea in texto.splitlines():
        if any(kw in linea.lower() for kw in ['calle', 'avenida', 'plaza', 'evento', 'lugar']):
            lugares.append(linea.strip())

    return {
        "eventos": eventos,
        "horarios": horarios,
        "lugares": lugares
    }

# Main
ruta_imagen = 'Imagen3.png'  # Ruta de tu imagen del cartel
try:
    # Procesar la imagen con Google Vision
    texto_extraido = procesar_imagen_google_vision(ruta_imagen)
    print("Texto Extraído:")
    print(texto_extraido)

    # Extraer información estructurada
    informacion = extraer_informacion(texto_extraido)
    print("\nNombre de los Eventos:", informacion['eventos'])
    print("Horarios:", informacion['horarios'])
    print("Lugares:", informacion['lugares'])

except Exception as e:
    print(f"Error procesando la imagen: {e}")
