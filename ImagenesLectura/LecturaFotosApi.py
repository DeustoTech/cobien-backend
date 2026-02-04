import cv2
import pytesseract
from pytesseract import Output

# Configuración para Tesseract
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'  # Ajustar la ruta si es diferente

def procesar_imagen(ruta_imagen):
    # Cargar la imagen
    imagen = cv2.imread(ruta_imagen)

    # Convertir la imagen a escala de grises para mejorar el OCR
    gris = cv2.cvtColor(imagen, cv2.COLOR_BGR2GRAY)

    # Aplicar un umbral adaptativo para mejorar contraste
    _, umbral = cv2.threshold(gris, 150, 255, cv2.THRESH_BINARY)

    # Extraer texto con Tesseract
    texto = pytesseract.image_to_string(umbral, lang='spa', config='--psm 6')  # Ajustar PSM según el cartel
    return texto

def extraer_informacion(texto):
    import re
    
    # Ejemplo de expresiones regulares para extraer la información
    eventos = []
    horarios = []
    lugares = []

    # Buscar nombres de eventos (líneas en mayúsculas generalmente)
    for linea in texto.splitlines():
        if linea.isupper() and len(linea) > 3:  # Simplemente ejemplo para detectar eventos
            eventos.append(linea)

    # Buscar horarios (formato típico)
    horarios = re.findall(r'\d{1,2}:\d{2} ?(?:AM|PM)?', texto)

    # Buscar posibles ubicaciones basándose en palabras clave
    for linea in texto.splitlines():
        if any(kw in linea.lower() for kw in ['calle', 'avenida', 'plaza', 'centro']):
            lugares.append(linea)

    return {
        "eventos": eventos,
        "horarios": horarios,
        "lugares": lugares
    }

# Main
ruta_imagen = 'Imagen1.png'  # Cambiar a la ruta de tu imagen
texto_extraido = procesar_imagen(ruta_imagen)
informacion = extraer_informacion(texto_extraido)

print("Nombre de los Eventos:", informacion['eventos'])
print("Horarios:", informacion['horarios'])
print("Lugares:", informacion['lugares'])
