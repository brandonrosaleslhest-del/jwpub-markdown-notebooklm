#!/usr/bin/env python3
"""
Generador Automático de Repasos en Audio (Maestro y Alumno) con Google AI Studio.
Descarga automáticamente los guiones limpios, llama a Gemini TTS (Multi-speaker)
y une todas las partes de cada lección en un solo archivo .mp3 y .wav listo para escuchar.

Uso rápido en terminal (PC / Mac / Linux):
  export GEMINI_API_KEY="TU_API_KEY_DE_AI_STUDIO"
  pip install -U "google-genai>=1.16.0"
  python3 generar_audios_aistudio.py --lecciones 01,03,35
"""

import argparse
import base64
import io
import os
import re
import shutil
import subprocess
import time
import urllib.request
import wave
import zipfile
from pathlib import Path

ZIP_URL = "https://github.com/brandonrosaleslhest-del/jwpub-markdown-notebooklm/raw/main/pt14_S_Guiones_Audio_Maestro_Alumno.zip"

DEFAULT_STYLE_MAESTRO = (
    "Lee en español latinoamericano natural, con tono cálido, sereno, claro y didáctico, "
    "como un maestro cristiano experimentado explicando con cariño la Biblia."
)
DEFAULT_STYLE_ALUMNO = (
    "Lee en español latinoamericano natural, con tono atento, respetuoso y reflexivo, "
    "como un estudiante aplicado haciendo las preguntas de la lección."
)


def ensure_scripts_downloaded(work_dir: Path) -> Path:
    parts_dir = work_dir / "03_Partes_Para_Google_AI_Studio"
    if parts_dir.exists() and any(parts_dir.glob("[0-9]*.txt")):
        return parts_dir
    local_existing = Path("/home/antigravity/pt14_S_audio_estudio/03_Partes_Para_Google_AI_Studio")
    if local_existing.exists() and any(local_existing.glob("[0-9]*.txt")):
        return local_existing

    work_dir.mkdir(parents=True, exist_ok=True)
    zip_path = work_dir / "pt14_S_Guiones_Audio_Maestro_Alumno.zip"
    if not zip_path.exists():
        print(f"📥 Descargando guiones desde GitHub ({ZIP_URL})...")
        urllib.request.urlretrieve(ZIP_URL, zip_path)
    print("📂 Extrayendo partes listas para AI Studio...")
    with zipfile.ZipFile(zip_path, "r") as zf:
        zf.extractall(work_dir)
    return parts_dir


def parse_script_turns(text: str):
    """Divide un archivo de guion en turnos [('Maestro'|'Alumno', texto_limpio)]."""
    turns = []
    cur_speaker = None
    cur_lines = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            if cur_lines:
                cur_lines.append("")
            continue
        if line.startswith("Maestro:"):
            if cur_speaker and any(cur_lines):
                turns.append((cur_speaker, "\n".join(cur_lines).strip()))
            cur_speaker = "Maestro"
            cur_lines = [line[len("Maestro:"):].strip()]
        elif line.startswith("Alumno:"):
            if cur_speaker and any(cur_lines):
                turns.append((cur_speaker, "\n".join(cur_lines).strip()))
            cur_speaker = "Alumno"
            cur_lines = [line[len("Alumno:"):].strip()]
        else:
            if cur_speaker is None:
                cur_speaker = "Maestro"
            cur_lines.append(line)
    if cur_speaker and any(cur_lines):
        turns.append((cur_speaker, "\n".join(cur_lines).strip()))
    return [(sp, txt) for sp, txt in turns if txt]


def extract_pcm_from_audio_bytes(audio_bytes: bytes):
    """Devuelve (pcm_bytes, sample_rate, channels, sample_width) desde WAV o PCM crudo."""
    if audio_bytes[:4] == b"RIFF":
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            return (
                wf.readframes(wf.getnframes()),
                wf.getframerate(),
                wf.getnchannels(),
                wf.getsampwidth(),
            )
    return audio_bytes, 24000, 1, 2


def save_wav_from_pcm(out_wav: Path, pcm_bytes: bytes, rate=24000, channels=1, width=2):
    with wave.open(str(out_wav), "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(width)
        wf.setframerate(rate)
        wf.writeframes(pcm_bytes)


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> bool:
    if not shutil.which("ffmpeg"):
        return False
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(wav_path),
        "-codec:a", "libmp3lame", "-qscale:a", "2",
        str(mp3_path),
    ]
    res = subprocess.run(cmd, check=False)
    return res.returncode == 0 and mp3_path.exists()


def synthesize_part_with_retry(
    client,
    script_text: str,
    model_id: str,
    voice_maestro: str,
    voice_alumno: str,
    style_maestro: str,
    style_alumno: str,
    max_retries: int = 5,
) -> bytes:
    from google.genai import types

    turns = parse_script_turns(script_text)
    speakers_present = {sp for sp, _ in turns}

    for attempt in range(1, max_retries + 1):
        try:
            # 1) Intento con API moderna Interactions (Gemini 3.8 TTS) si el SDK lo soporta
            if hasattr(client, "interactions") and "3.8" in model_id:
                if len(speakers_present) == 1:
                    only_sp = next(iter(speakers_present))
                    v_name = voice_maestro if only_sp == "Maestro" else voice_alumno
                    s_text = style_maestro if only_sp == "Maestro" else style_alumno
                    full_txt = "\n\n".join(t for _, t in turns)
                    interaction = client.interactions.create(
                        model=model_id,
                        input=[
                            {
                                "type": "text",
                                "text": full_txt,
                                "annotations": [
                                    {"type": "speech_metadata", "style": s_text}
                                ],
                            }
                        ],
                        response_format={"type": "audio"},
                        generation_config={"speech_config": [{"voice": v_name}]},
                    )
                else:
                    turn_inputs = [
                        {
                            "type": "text",
                            "text": t_txt,
                            "annotations": [
                                {
                                    "type": "speech_metadata",
                                    "speaker": sp,
                                    "style": style_maestro if sp == "Maestro" else style_alumno,
                                }
                            ],
                        }
                        for sp, t_txt in turns
                    ]
                    interaction = client.interactions.create(
                        model=model_id,
                        input=turn_inputs,
                        response_format={"type": "audio"},
                        generation_config={
                            "speech_config": [
                                {"speaker": "Maestro", "voice": voice_maestro},
                                {"speaker": "Alumno", "voice": voice_alumno},
                            ]
                        },
                    )
                return base64.b64decode(interaction.output_audio.data)

            # 2) Ruta universal compatible con generate_content (Gemini 2.5 / 3.1 / 3.8 TTS)
            gen_model = model_id
            if not hasattr(client, "interactions") and "3.8" in gen_model:
                gen_model = "gemini-2.5-flash-preview-tts"

            if len(speakers_present) == 1:
                only_sp = next(iter(speakers_present))
                v_name = voice_maestro if only_sp == "Maestro" else voice_alumno
                s_text = style_maestro if only_sp == "Maestro" else style_alumno
                body = "\n\n".join(t for _, t in turns)
                prompt = f"{s_text}\n\n{body}"
                response = client.models.generate_content(
                    model=gen_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=v_name
                                )
                            )
                        ),
                    ),
                )
            else:
                prompt = (
                    f"Lee en voz alta esta conversación de estudio bíblico en español natural.\n"
                    f"Maestro: {style_maestro}\n"
                    f"Alumno: {style_alumno}\n\n"
                    f"{script_text.strip()}"
                )
                response = client.models.generate_content(
                    model=gen_model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                                speaker_voice_configs=[
                                    types.SpeakerVoiceConfig(
                                        speaker="Maestro",
                                        voice_config=types.VoiceConfig(
                                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                                voice_name=voice_maestro
                                            )
                                        ),
                                    ),
                                    types.SpeakerVoiceConfig(
                                        speaker="Alumno",
                                        voice_config=types.VoiceConfig(
                                            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                                voice_name=voice_alumno
                                            )
                                        ),
                                    ),
                                ]
                            )
                        ),
                    ),
                )
            data = response.candidates[0].content.parts[0].inline_data.data
            if isinstance(data, str):
                return base64.b64decode(data)
            return data

        except Exception as err:
            err_str = str(err)
            # Si el modelo 3.8 no está habilitado en esa región/SDK, hacer fallback automático a 2.5
            if "3.8" in model_id and ("404" in err_str or "not found" in err_str.lower() or "invalid" in err_str.lower()):
                print("   ℹ️ Cambiando automáticamente a gemini-2.5-flash-preview-tts por compatibilidad...")
                model_id = "gemini-2.5-flash-preview-tts"
            wait_s = min(60, 12 * attempt)
            print(f"   ⚠️ Aviso en intento {attempt}/{max_retries}: {err_str[:160]}. Reintentando en {wait_s}s...")
            time.sleep(wait_s)

    raise RuntimeError("No se pudo sintetizar la parte tras varios intentos. Revisa tu cuota o API Key.")


def group_parts_by_lesson(parts_dir: Path):
    files = sorted(
        f for f in parts_dir.glob("[0-9]*.txt")
        if not f.name.startswith("00_")
    )
    by_lesson = {}
    for f in files:
        m = re.match(r"^(\d+_[A-Za-z0-9_]+?)_AIStudio", f.name)
        key = m.group(1) if m else f.stem
        by_lesson.setdefault(key, []).append(f)
    return by_lesson


def generate_lessons(
    api_key: str,
    lesson_prefixes: list[str],
    out_dir: Path,
    model_id: str = "gemini-3.8-flash-tts",
    voice_maestro: str = "Orus",
    voice_alumno: str = "Puck",
    pause_between_parts_sec: float = 0.6,
    delay_between_requests_sec: float = 4.0,
):
    from google import genai

    client = genai.Client(api_key=api_key)
    parts_dir = ensure_scripts_downloaded(out_dir / "_guiones_descargados")
    by_lesson = group_parts_by_lesson(parts_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    parts_cache_dir = out_dir / "partes_wav"
    parts_cache_dir.mkdir(parents=True, exist_ok=True)

    selected_keys = []
    for k in sorted(by_lesson.keys()):
        if not lesson_prefixes or any(
            k.lower().startswith(p.lower().strip()) or p.lower().strip() in k.lower()
            for p in lesson_prefixes
        ):
            selected_keys.append(k)

    if not selected_keys:
        raise ValueError(f"No se encontraron lecciones que coincidan con: {lesson_prefixes}")

    generated_files = []
    for lesson_key in selected_keys:
        part_files = by_lesson[lesson_key]
        print(f"\n============================================================")
        print(f"🎧 Generando {lesson_key} ({len(part_files)} parte(s))")
        print(f"   Voz Maestro: {voice_maestro} | Voz Alumno: {voice_alumno} | Modelo: {model_id}")
        print(f"============================================================")

        combined_pcm = bytearray()
        sample_rate, channels, width = 24000, 1, 2

        for idx, pf in enumerate(part_files, 1):
            cached_wav = parts_cache_dir / f"{pf.stem}.wav"
            if cached_wav.exists() and cached_wav.stat().st_size > 1000:
                print(f"  [{idx}/{len(part_files)}] ✅ Usando parte ya guardada: {cached_wav.name}")
                raw_bytes = cached_wav.read_bytes()
            else:
                print(f"  [{idx}/{len(part_files)}] 🎙️ Sintetizando {pf.name} ({pf.stat().st_size:,} bytes)...")
                script_text = pf.read_text(encoding="utf-8")
                raw_bytes = synthesize_part_with_retry(
                    client=client,
                    script_text=script_text,
                    model_id=model_id,
                    voice_maestro=voice_maestro,
                    voice_alumno=voice_alumno,
                    style_maestro=DEFAULT_STYLE_MAESTRO,
                    style_alumno=DEFAULT_STYLE_ALUMNO,
                )
                pcm_part, sample_rate, channels, width = extract_pcm_from_audio_bytes(raw_bytes)
                save_wav_from_pcm(cached_wav, pcm_part, sample_rate, channels, width)
                if idx < len(part_files) and delay_between_requests_sec > 0:
                    time.sleep(delay_between_requests_sec)

            pcm_part, sample_rate, channels, width = extract_pcm_from_audio_bytes(raw_bytes)
            combined_pcm.extend(pcm_part)
            if idx < len(part_files) and pause_between_parts_sec > 0:
                silence_frames = int(sample_rate * pause_between_parts_sec)
                combined_pcm.extend(b"\x00" * (silence_frames * channels * width))

        final_wav = out_dir / f"{lesson_key}_Maestro_Alumno.wav"
        save_wav_from_pcm(final_wav, bytes(combined_pcm), sample_rate, channels, width)
        final_mp3 = out_dir / f"{lesson_key}_Maestro_Alumno.mp3"
        if convert_wav_to_mp3(final_wav, final_mp3):
            duration_min = (len(combined_pcm) / (sample_rate * channels * width)) / 60.0
            print(f"✅ ¡Lección lista! -> {final_mp3.name} ({duration_min:.1f} min, {final_mp3.stat().st_size / 1048576:.2f} MB)")
            generated_files.append(final_mp3)
        else:
            print(f"✅ ¡Lección lista (WAV)! -> {final_wav.name} ({final_wav.stat().st_size / 1048576:.2f} MB)")
            generated_files.append(final_wav)

    return generated_files


def main():
    parser = argparse.ArgumentParser(description="Genera audios Maestro/Alumno con Google AI Studio.")
    parser.add_argument(
        "--lecciones",
        default="35,01",
        help="Prefijos de lección separados por coma (ej. '35,01,03' o 'todas'). Por defecto: '35,01'",
    )
    parser.add_argument("--modelo", default="gemini-3.8-flash-tts", help="Modelo TTS (ej. gemini-3.8-flash-tts, gemini-3.8-flash-lite-tts, gemini-2.5-flash-preview-tts)")
    parser.add_argument("--voz-maestro", default="Orus", help="Voz para Maestro (ej. Orus, Charon, Schedar, Sulafat)")
    parser.add_argument("--voz-alumno", default="Puck", help="Voz para Alumno (ej. Puck, Fenrir, Kore, Aoede)")
    parser.add_argument("--salida", default="./Audios_Escuela_Precursores", help="Carpeta de salida")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        raise SystemExit("❌ Falta GEMINI_API_KEY. Ejecuta: export GEMINI_API_KEY='tu_clave_de_aistudio'")

    prefixes = [] if args.lecciones.strip().lower() in ("todas", "all", "*") else [x.strip() for x in args.lecciones.split(",") if x.strip()]
    generate_lessons(
        api_key=api_key,
        lesson_prefixes=prefixes,
        out_dir=Path(args.salida),
        model_id=args.modelo,
        voice_maestro=args.voz_maestro,
        voice_alumno=args.voz_alumno,
    )


if __name__ == "__main__":
    main()
