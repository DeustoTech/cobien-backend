#!/usr/bin/env bash
# Script para iniciar el backend de CoBien en local
set -Eeuo pipefail

# Ir al directorio del script
cd "$(dirname "$0")"

echo "=== Configurando variables de entorno ==="
export MONGO_URI='mongodb+srv://usuarioCoBien:passwordCoBien@clustercobienevents.j8ev5.mongodb.net/?retryWrites=true&w=majority&appName=ClusterCoBienEvents'
export DB_NAME='LabasAppDB'
export DEBUG=True
export DJANGO_SECRET_KEY='django-insecure-local-dev-key'

# Crear entorno virtual si no existe
if [ ! -d ".venv" ]; then
    echo "Creando entorno virtual (.venv)..."
    python3 -m venv .venv
fi

echo "Activando entorno virtual..."
source .venv/bin/activate

echo "Actualizando pip e instalando dependencias (esto puede tardar la primera vez)..."
pip install --upgrade pip
pip install -r requirements.txt

echo "Ejecutando migraciones..."
python manage.py migrate || echo "Advertencia: Las migraciones fallaron. Asegúrate de que tu IP esté autorizada en MongoDB Atlas si usas la base de datos remota."

echo "Iniciando servidor de desarrollo en http://127.0.0.1:8000/ ..."
python manage.py runserver 0.0.0.0:8000
