import pandas as pd
from pymongo import MongoClient
from datetime import datetime

# Ruta al archivo CSV actualizado
csv_path = "eventos_con_location.csv"

# Leer el archivo CSV
df = pd.read_csv(csv_path)

# Conexión a MongoDB Atlas
client = MongoClient("mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/LabasAppDB?retryWrites=true&w=majority")
db = client['LabasAppDB']
collection = db['Eventos']

# Preparar e insertar eventos
eventos = []
for _, row in df.iterrows():
    try:
        raw_date = row['date']
        if pd.notnull(raw_date):
            date_obj = datetime.strptime(raw_date, "%m-%d-%Y")  # ← AQUÍ EL FORMATO CORRECTO
            date_str = date_obj.strftime("%d-%m-%Y")  # Convertimos a formato que usas en Mongo
        else:
            date_str = ""
        
        evento = {
            "date": date_str,
            "title": row['title'],
            "description": row['description'],
            "location": row['location']
        }
        eventos.append(evento)
    except Exception as e:
        print(f"Error procesando fila: {row} - {e}")

# Insertar en MongoDB
if eventos:
    result = collection.insert_many(eventos)
    print(f"{len(result.inserted_ids)} eventos insertados con éxito.")
else:
    print("No se insertaron eventos.")
