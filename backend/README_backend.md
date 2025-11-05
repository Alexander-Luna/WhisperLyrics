Instrucciones rápidas:

Crear un entorno virtual (venv) y activar.

pip install -r requirements.txt

Exportar la variable de entorno OPENAI_API_KEY con tu clave.

Ejecutar: uvicorn main:app --reload --port 8000

Endpoint importante: POST /transcribe/ (multipart form) acepta file (audio) y opcional url (si subes por URL). Devuelve JSON con segments y un enlace para descargar .lrc.