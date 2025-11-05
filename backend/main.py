from fastapi import FastAPI, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import subprocess, tempfile, os, uuid, json, requests, mimetypes, threading
import re
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploads"
MODEL_PATH = "models/model.bin"
WHISPER_EXE = "whisper/whisper-cli.exe"

os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOAD_DIR), name="uploads")

# -----------------------------
# Diccionario en memoria para progreso
# -----------------------------
progress_map = {}


def parse_time_to_seconds(time_str):
    """Convierte formato HH:MM:SS.mmm a segundos."""
    h, m, s = time_str.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)

def run_whisper_with_progress(cmd, output_json, task_id):
    """
    Ejecuta whisper.cpp y actualiza el progreso real (0–100 %) en progress_map[task_id].
    """
    progress_map[task_id] = {"status": "processing", "progress": 0}
    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )

    total_duration = None
    last_progress = 0

    for line in process.stdout:
        # Detecta la duración total (ej. "(200.0 sec)")
        if "processing" in line and "sec" in line:
            m = re.search(r"\(([\d.]+)\s*sec\)", line)
            if m:
                total_duration = float(m.group(1))

        # Detecta segmentos con tiempo
        if "-->" in line:
            match = re.search(r"\[(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})\]", line)
            if match and total_duration:
                end = parse_time_to_seconds(match.group(2))
                progress = min(100, (end / total_duration) * 100)
                if progress > last_progress:
                    progress_map[task_id]["progress"] = round(progress, 2)
                    last_progress = progress

    process.wait()

    if process.returncode == 0 and os.path.exists(output_json):
        progress_map[task_id] = {"status": "done", "progress": 100}
    else:
        progress_map[task_id] = {"status": "error", "progress": last_progress}

@app.post("/transcribe/")
async def transcribe_audio(file: UploadFile = None, url: str = Form(None)):
    """
    Transcribe un archivo de audio o una URL con whisper.cpp y genera .lrc y progreso en tiempo real.
    """
    # -----------------------------
    # 1. Guardar o descargar archivo
    # -----------------------------
    if file:
        ext = os.path.splitext(file.filename)[1] or mimetypes.guess_extension(file.content_type or '') or '.mp3'
        temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}{ext}")
        with open(temp_path, "wb") as f:
            f.write(await file.read())
    elif url:
        r = requests.get(url)
        if r.status_code != 200:
            return {"error": "No se pudo descargar el audio desde la URL proporcionada"}
        temp_path = os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}.mp3")
        with open(temp_path, "wb") as f:
            f.write(r.content)
    else:
        return {"error": "Debe enviar un archivo o una URL válida"}

    # -----------------------------
    # 2. Preparar whisper.cpp (CLI)
    # -----------------------------
    output_base = os.path.splitext(temp_path)[0]
    output_json = f"{output_base}.json"
    task_id = os.path.basename(output_base)

    cmd = [
        WHISPER_EXE,
        "-m", MODEL_PATH,
        "-l", "es",      # idioma español
        "-f", temp_path,
        "-oj",              # salida JSON
        "-of", output_base,  # prefijo del archivo de salida
        "--print-progress",
        "--max-len", "1",      # fuerza cortes más pequeños
        "--split-on-word"      # si tu versión lo soporta
    ]

    # Ejecutar whisper en hilo aparte para no bloquear
    thread = threading.Thread(target=run_whisper_with_progress, args=(cmd, output_json, task_id))
    thread.start()

    # Retornar ID del proceso
    return {"task_id": task_id, "audio_file": f"/uploads/{os.path.basename(temp_path)}"}


@app.get("/progress/{task_id}")
def get_progress(task_id: str):
    """
    Devuelve el estado actual de la transcripción.
    Ejemplo: {"status": "processing", "progress": 52.4}
    """
    progress = progress_map.get(task_id)
    if not progress:
        return {"error": "ID no encontrado"}
    return progress


def time_to_seconds(t: str) -> float:
    # Convierte "00:01:07,240" → 67.24
    m = re.match(r"(\d+):(\d+):(\d+),(\d+)", t)
    if not m:
        return 0.0
    h, m_, s, ms = map(int, m.groups())
    return h * 3600 + m_ * 60 + s + ms / 1000.0

@app.get("/result/{task_id}")
def get_result(task_id: str):
    output_json = f"uploads/{task_id}.json"
    if not os.path.exists(output_json):
        return {"error": "Archivo JSON no encontrado"}

    with open(output_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    transcription = data.get("transcription", [])
    segments = []
    for seg in transcription:
        start = time_to_seconds(seg["timestamps"]["from"])
        end = time_to_seconds(seg["timestamps"]["to"])
        text = seg["text"].strip()
        if not text:
            continue

        words = text.split()
        dur_per_word = (end - start) / max(len(words), 1)

        current_time = start
        for w in words:
            segments.append({
                "start": current_time,
                "end": current_time + dur_per_word,
                "text": w
            })
            current_time += dur_per_word

    # Detectar el primer bloque de voz para saltar instrumental
    offset = segments[0]["start"] if segments else 0

    return {
        "segments": segments,
        "offset": offset,  # nuevo valor
        "audio_file": f"/uploads/{task_id}.mp3"
    }
