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

def run_whisper_with_progress(audio_path, task_id):
    # 👇 El output_json debe coincidir con el que se espera en get_result
    output_json = f"uploads/{task_id}.json"

    # 👇 Usar los mismos valores que en /transcribe/
    cmd = [
        WHISPER_EXE,  # 👈 Cambiado de "./main" a WHISPER_EXE
        "-m", MODEL_PATH,
        "-l", "es",      # idioma español
        "-f", audio_path, # 👈 audio_path es temp_path
        "-oj",              # salida JSON
        "-of", f"uploads/{task_id}",  # 👈 Prefijo de salida debe coincidir con task_id
        "--print-progress",
        "--max-len", "1",      # fuerza cortes más pequeños
        "--split-on-word"      # si tu versión lo soporta
    ]

    process = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
    )
    progress_map[task_id] = {"status": "processing", "progress": 0}

    for line in process.stdout:
        if "%" in line:
            match = re.search(r"(\d+)%", line)
            if match:
                progress_map[task_id]["progress"] = int(match.group(1))
        elif "total time" in line:
            progress_map[task_id]["status"] = "completed"

    process.wait()

    if process.returncode == 0 and os.path.exists(output_json):
        progress_map[task_id]["status"] = "done"
    else:
        progress_map[task_id]["status"] = "error"

        
@app.post("/transcribe/")
async def transcribe_audio(file: UploadFile = None, url: str = Form(None)):
    """
    Transcribe un archivo de audio o una URL con whisper.cpp y genera .lrc y progreso en tiempo real.
    """
    # -----------------------------
    # 1. Generar un único UUID para todo el proceso
    # -----------------------------
    task_id = str(uuid.uuid4())

    # -----------------------------
    # 2. Guardar o descargar archivo
    # -----------------------------
    if file:
        ext = os.path.splitext(file.filename)[1] or mimetypes.guess_extension(file.content_type or '') or '.mp3'
        temp_path = os.path.join(UPLOAD_DIR, f"{task_id}{ext}")  # 👈 Usar task_id
        with open(temp_path, "wb") as f:
            f.write(await file.read())
    elif url:
        r = requests.get(url)
        if r.status_code != 200:
            return {"error": "No se pudo descargar el audio desde la URL proporcionada"}
        temp_path = os.path.join(UPLOAD_DIR, f"{task_id}.mp3")  # 👈 Usar task_id
        with open(temp_path, "wb") as f:
            f.write(r.content)
    else:
        return {"error": "Debe enviar un archivo o una URL válida"}

    # -----------------------------
    # 2. Preparar whisper.cpp (CLI)
    # -----------------------------
    # 👇 output_base es uploads/task_id
    output_base = os.path.join(UPLOAD_DIR, task_id)

    cmd = [
        WHISPER_EXE,
        "-m", MODEL_PATH,
        "-l", "es",      # idioma español
        "-f", temp_path,
        "-oj",              # salida JSON
        "-of", output_base,  # prefijo del archivo de salida (uploads/task_id)
        "--print-progress",
        "--max-len", "1",      # fuerza cortes más pequeños
        "--split-on-word"      # si tu versión lo soporta
    ]

    # Ejecutar whisper en hilo aparte para no bloquear
    # 👇 CORREGIDO: Pasar audio_path (temp_path) y task_id
    thread = threading.Thread(target=run_whisper_with_progress, args=(temp_path, task_id))
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


import os
import json
import subprocess
import re

def get_audio_duration(audio_path: str) -> float:
    """Devuelve duración en segundos (float) usando ffprobe o None si falla."""
    try:
        cmd = [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            audio_path
        ]
        proc = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = proc.stdout.strip()
        if out:
            return float(out)
    except Exception:
        pass
    return None

def parse_hhmmss_ms(t: str) -> float:
    """Convierte 'HH:MM:SS,mmm' o 'HH:MM:SS.mmm' a segundos (float)."""
    t = t.replace(',', '.')
    parts = t.split(':')
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    return 0.0

@app.get("/result/{task_id}")
def get_result(task_id: str):
    output_json = f"uploads/{task_id}.json"
    audio_path = f"uploads/{task_id}.mp3"

    if not os.path.exists(output_json):
        return {"error": "Archivo JSON no encontrado"}

    total_duration = get_audio_duration(audio_path)  # puede ser None

    with open(output_json, "r", encoding="utf-8") as f:
        data = json.load(f)

    transcription = data.get("transcription") or data.get("segments") or data.get("result") or []
    # normalize: transcription is a list of blocks that might contain:
    # - "timestamps": {"from":"HH:MM:SS,mmm","to":"..."}  OR
    # - "offsets": {"from": millis, "to": millis} OR
    # - "t0"/"t1" numeric values (centi/ms)
    starts_option1 = []  # helper arrays to decide scale
    starts_option2 = []
    uses_offsets = False
    uses_t_fields = False

    # first pass: detect which fields exist
    for b in transcription:
        if isinstance(b, dict) and "offsets" in b:
            uses_offsets = True
            break
        if isinstance(b, dict) and ("t0" in b or "t1" in b):
            uses_t_fields = True

    segments = []
    last_end = 0.0

    if uses_offsets:
        # offsets are normally in milliseconds
        for b in transcription:
            offs = b.get("offsets", {})
            start_ms = offs.get("from", None)
            end_ms = offs.get("to", None)
            if start_ms is None or end_ms is None:
                # fallback to timestamps field if present
                ts_from = b.get("timestamps", {}).get("from")
                ts_to = b.get("timestamps", {}).get("to")
                if ts_from and ts_to:
                    start = parse_hhmmss_ms(ts_from)
                    end = parse_hhmmss_ms(ts_to)
                else:
                    continue
            else:
                start = float(start_ms) / 1000.0
                end = float(end_ms) / 1000.0

            text = (b.get("text") or "").strip()

            # if there's a gap before this block, create a silence segment
            if start > last_end + 1e-6:
                segments.append({"start": round(last_end,3), "end": round(start,3), "text": "", "type":"silence"})
            if text == "":
                segments.append({"start": round(start,3), "end": round(end,3), "text":"", "type":"silence"})
            else:
                # split block into words keeping exact start/end interval
                words = text.split()
                if len(words) == 1:
                    segments.append({"start": round(start,3), "end": round(end,3), "text": words[0], "type":"word"})
                else:
                    dur = (end - start) / len(words)
                    cur = start
                    for w in words:
                        segments.append({"start": round(cur,3), "end": round(cur+dur,3), "text": w, "type":"word"})
                        cur += dur
            last_end = max(last_end, end)

    elif uses_t_fields:
        # t0/t1 sometimes come in *centiseconds* (t0=360 -> 3.60s) or milliseconds.
        # We'll test which scale fits the audio duration best if total_duration is available.
        tvalues = []
        for b in transcription:
            t0 = b.get("t0")
            t1 = b.get("t1")
            if t0 is None or t1 is None:
                continue
            tvalues.append((float(t0), float(t1)))
        # default denominators to try
        try_denoms = [1000.0, 100.0, 1.0]  # ms, centi, already seconds
        chosen_denom = 1000.0
        if total_duration:
            # pick denom whose max(t1)/denom is closest to total_duration (but <= 1.5*total_duration)
            best = None
            best_diff = 1e9
            for denom in try_denoms:
                max_sec = max((t1 / denom for (_, t1) in tvalues), default=0.0)
                # prefer denom where max_sec is <= total_duration*1.2 (tolerancia)
                diff = abs((total_duration - max_sec))
                # penaliza si max_sec > total_duration*1.5
                if max_sec > total_duration * 1.5:
                    diff *= 10
                if diff < best_diff:
                    best_diff = diff
                    best = denom
            if best:
                chosen_denom = best
        else:
            # sin total_duration asumimos milisegundos
            chosen_denom = 1000.0

        # ahora parseamos usando chosen_denom
        for b in transcription:
            t0 = b.get("t0")
            t1 = b.get("t1")
            if t0 is None or t1 is None:
                # fallback a timestamps string si existe
                ts_from = b.get("timestamps", {}).get("from")
                ts_to = b.get("timestamps", {}).get("to")
                if ts_from and ts_to:
                    start = parse_hhmmss_ms(ts_from)
                    end = parse_hhmmss_ms(ts_to)
                else:
                    continue
            else:
                start = float(t0) / chosen_denom
                end = float(t1) / chosen_denom

            text = (b.get("text") or "").strip()

            if start > last_end + 1e-6:
                segments.append({"start": round(last_end,3), "end": round(start,3), "text": "", "type":"silence"})
            if text == "":
                segments.append({"start": round(start,3), "end": round(end,3), "text":"", "type":"silence"})
            else:
                words = text.split()
                if len(words) == 1:
                    segments.append({"start": round(start,3), "end": round(end,3), "text": words[0], "type":"word"})
                else:
                    dur = (end - start) / len(words)
                    cur = start
                    for w in words:
                        segments.append({"start": round(cur,3), "end": round(cur+dur,3), "text": w, "type":"word"})
                        cur += dur
            last_end = max(last_end, end)

    else:
        # fallback: try timestamps strings HH:MM:SS,mmm
        for b in transcription:
            ts_from = b.get("timestamps", {}).get("from")
            ts_to = b.get("timestamps", {}).get("to")
            if not ts_from or not ts_to:
                continue
            start = parse_hhmmss_ms(ts_from)
            end = parse_hhmmss_ms(ts_to)
            text = (b.get("text") or "").strip()
            if start > last_end + 1e-6:
                segments.append({"start": round(last_end,3), "end": round(start,3), "text":"", "type":"silence"})
            if text == "":
                segments.append({"start": round(start,3), "end": round(end,3), "text":"", "type":"silence"})
            else:
                words = text.split()
                if len(words) == 1:
                    segments.append({"start": round(start,3), "end": round(end,3), "text": words[0], "type":"word"})
                else:
                    dur = (end - start) / len(words)
                    cur = start
                    for w in words:
                        segments.append({"start": round(cur,3), "end": round(cur+dur,3), "text": w, "type":"word"})
                        cur += dur
            last_end = max(last_end, end)

    # agregar silencio final hasta total_duration si lo conocemos
    if total_duration is not None and total_duration > last_end + 1e-6:
        segments.append({"start": round(last_end,3), "end": round(total_duration,3), "text":"", "type":"silence"})
        last_end = total_duration

    # calcular offset (primer segmento con texto)
    offset = 0.0
    for s in segments:
        if s.get("text"):
            offset = s["start"]
            break

    return {"segments": segments, "offset": offset, "audio_file": f"/uploads/{task_id}.mp3", "total_duration": total_duration}