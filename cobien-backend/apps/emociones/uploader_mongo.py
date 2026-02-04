# uploader_mongo.py

import json
from pymongo import MongoClient
from datetime import datetime

def subir_log_a_mongo(path_log_json, mongo_uri,
                      nombre_db="LabasAppDB",
                      nombre_coleccion="LogsEmociones"):
    """
    Lee un archivo JSON en path_log_json y lo inserta en LabasAppDB.LogsEmociones.
    
    Parámetros:
    - path_log_json: ruta al archivo log_final.json.
    - mongo_uri: cadena de conexión a Atlas (p. ej. mongodb+srv://...).
    - nombre_db: "LabasAppDB" (base de datos existente).
    - nombre_coleccion: "LogsEmociones" (colección que creamos).
    """
    # 1. Cargar JSON desde archivo
    try:
        with open(path_log_json, "r", encoding="utf-8") as f:
            contenido = json.load(f)
    except FileNotFoundError:
        print(f"[Error] No se encontró el archivo {path_log_json}")
        return False
    except json.JSONDecodeError as e:
        print(f"[Error] No se pudo decodificar JSON: {e}")
        return False

    # 2. Conectarse a MongoDB
    try:
        client = MongoClient(mongo_uri)
        db = client[nombre_db]
        coleccion = db[nombre_coleccion]
    except Exception as e:
        print(f"[Error] No se pudo conectar a MongoDB: {e}")
        return False

    # 3. Añadir un campo con fecha de subida (opcional)
    contenido["_subido_en"] = datetime.utcnow()

    # 4. Insertar en la colección
    try:
        resultado = coleccion.insert_one(contenido)
        print(f"[Info] Log insertado con _id: {resultado.inserted_id}")
        return True
    except Exception as e:
        print(f"[Error] No se pudo insertar el documento: {e}")
        return False
