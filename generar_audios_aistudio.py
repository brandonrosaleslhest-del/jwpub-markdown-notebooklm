#!/usr/bin/env python3
"""
Generador Automático de Repasos en Audio (Maestro y Alumno) con Google AI Studio.
Descarga automáticamente los guiones limpios, detecta el mejor modelo TTS disponible
en tu API Key (SDK + REST nativo v1beta), llama a Gemini TTS (Multi-speaker) y
une todas las partes de cada lección en un solo archivo .mp3 y .wav.

Uso rápido en terminal (PC / Mac / Linux):
  export GEMINI_API_KEY="TU_API_KEY_DE_AI_STUDIO"
  pip install -U "google-genai>=2.24.0,<3"
  python3 generar_audios_aistudio.py --lecciones 35,01
"""

import argparse
import base64
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import wave
import zipfile
from pathlib import Path

ZIP_URL = "https://github.com/brandonrosaleslhest-del/jwpub-markdown-notebooklm/raw/main/pt14_S_Guiones_Audio_Maestro_Alumno.zip"

CANDIDATE_MODELS = [
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
    "gemini-2.5-pro-preview-tts",
    "gemini-2.5-flash-tts",
]

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


def _http_post_json(url: str, headers: dict, payload: dict) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP {e.code}: {body[:260]}") from e


def _call_rest_v1beta_generate(api_key, model_id, turns, script_text, voice_maestro, voice_alumno, style_maestro, style_alumno):
    speakers = {sp for sp, _ in turns}
    if len(speakers) == 1:
        only_sp = next(iter(speakers))
        v_name = voice_maestro if only_sp == "Maestro" else voice_alumno
        s_text = style_maestro if only_sp == "Maestro" else style_alumno
        prompt = f"{s_text}\n\n" + "\n\n".join(t for _, t in turns)
        speech_cfg = {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": v_name}}}
    else:
        prompt = (
            f"Lee en voz alta esta conversación de estudio bíblico en español natural.\n"
            f"Maestro: {style_maestro}\n"
            f"Alumno: {style_alumno}\n\n"
            f"{script_text.strip()}"
        )
        speech_cfg = {
            "multiSpeakerVoiceConfig": {
                "speakerVoiceConfigs": [
                    {"speaker": "Maestro", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_maestro}}},
                    {"speaker": "Alumno", "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice_alumno}}},
                ]
            }
        }
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": speech_cfg,
        },
    }
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_id}:generateContent"
    data = _http_post_json(url, {"x-goog-api-key": api_key}, payload)
    b64 = data["candidates"][0]["content"]["parts"][0]["inlineData"]["data"]
    return base64.b64decode(b64)


def _call_rest_v1beta_interactions(api_key, model_id, turns, script_text, voice_maestro, voice_alumno, style_maestro, style_alumno):
    speakers = {sp for sp, _ in turns}
    if "3.8" in model_id:
        if len(speakers) == 1:
            only_sp = next(iter(speakers))
            v_name = voice_maestro if only_sp == "Maestro" else voice_alumno
            s_text = style_maestro if only_sp == "Maestro" else style_alumno
            payload = {
                "model": model_id,
                "input": [{"type": "text", "text": "\n\n".join(t for _, t in turns), "annotations": [{"type": "speech_metadata", "style": s_text}]}],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": v_name}]},
            }
        else:
            payload = {
                "model": model_id,
                "input": [
                    {"type": "text", "text": t_txt, "annotations": [{"type": "speech_metadata", "speaker": sp, "style": style_maestro if sp == "Maestro" else style_alumno}]}
                    for sp, t_txt in turns
                ],
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"speaker": "Maestro", "voice": voice_maestro}, {"speaker": "Alumno", "voice": voice_alumno}]},
            }
    else:
        if len(speakers) == 1:
            only_sp = next(iter(speakers))
            v_name = voice_maestro if only_sp == "Maestro" else voice_alumno
            s_text = style_maestro if only_sp == "Maestro" else style_alumno
            payload = {
                "model": model_id,
                "input": f"{s_text}\n\n" + "\n\n".join(t for _, t in turns),
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"voice": v_name}]},
            }
        else:
            prompt = (
                f"Lee en voz alta esta conversación de estudio bíblico en español natural.\n"
                f"Maestro: {style_maestro}\n"
                f"Alumno: {style_alumno}\n\n"
                f"{script_text.strip()}"
            )
            payload = {
                "model": model_id,
                "input": prompt,
                "response_format": {"type": "audio"},
                "generation_config": {"speech_config": [{"speaker": "Maestro", "voice": voice_maestro}, {"speaker": "Alumno", "voice": voice_alumno}]},
            }
    url = "https://generativelanguage.googleapis.com/v1beta/interactions"
    data = _http_post_json(url, {"x-goog-api-key": api_key, "Api-Revision": "2026-05-20"}, payload)
    for step in reversed(data.get("steps", [])):
        for c in step.get("content", []):
            if c.get("type") == "audio" and c.get("data"):
                return base64.b64decode(c["data"])
    if "output_audio" in data and data["output_audio"].get("data"):
        return base64.b64decode(data["output_audio"]["data"])
    raise RuntimeError(f"Respuesta sin audio en interactions: {str(data)[:200]}")


def _call_sdk_generate(client, model_id, turns, script_text, voice_maestro, voice_alumno, style_maestro, style_alumno):
    from google.genai import types
    speakers = {sp for sp, _ in turns}
    if len(speakers) == 1:
        only_sp = next(iter(speakers))
        v_name = voice_maestro if only_sp == "Maestro" else voice_alumno
        s_text = style_maestro if only_sp == "Maestro" else style_alumno
        prompt = f"{s_text}\n\n" + "\n\n".join(t for _, t in turns)
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=v_name))
            ),
        )
    else:
        prompt = (
            f"Lee en voz alta esta conversación de estudio bíblico en español natural.\n"
            f"Maestro: {style_maestro}\n"
            f"Alumno: {style_alumno}\n\n"
            f"{script_text.strip()}"
        )
        cfg = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                multi_speaker_voice_config=types.MultiSpeakerVoiceConfig(
                    speaker_voice_configs=[
                        types.SpeakerVoiceConfig(speaker="Maestro", voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_maestro))),
                        types.SpeakerVoiceConfig(speaker="Alumno", voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_alumno))),
                    ]
                )
            ),
        )
    resp = client.models.generate_content(model=model_id, contents=prompt, config=cfg)
    data = resp.candidates[0].content.parts[0].inline_data.data
    return base64.b64decode(data) if isinstance(data, str) else data


def synthesize_part_with_fallback(
    api_key: str,
    client,
    script_text: str,
    candidate_models: list[str],
    voice_maestro: str,
    voice_alumno: str,
    style_maestro: str,
    style_alumno: str,
):
    turns = parse_turns(script_text)
    last_err = None

    for model_id in candidate_models:
        methods = [
            ("rest_v1beta_interactions", lambda m: _call_rest_v1beta_interactions(api_key, m, turns, script_text, voice_maestro, voice_alumno, style_maestro, style_alumno)),
            ("rest_v1beta_generate", lambda m: _call_rest_v1beta_generate(api_key, m, turns, script_text, voice_maestro, voice_alumno, style_maestro, style_alumno)),
            ("sdk_generate", lambda m: _call_sdk_generate(client, m, turns, script_text, voice_maestro, voice_alumno, style_maestro, style_alumno)),
        ]
        for m_name, fn in methods:
            for retry in range(1, 4):
                try:
                    audio = fn(model_id)
                    return audio, f"{model_id} [{m_name}]"
                except Exception as err:
                    last_err = err
                    msg = str(err)
                    if any(code in msg for code in ("404", "401", "403", "not found", "NOT_FOUND", "Unknown name", "INVALID_ARGUMENT", "400")):
                        break
                    wait_s = 12 * retry
                    print(f"   ⏳ {model_id} ({m_name}) ocupado: {msg[:120]}. Reintentando en {wait_s}s...")
                    time.sleep(wait_s)

    raise RuntimeError(f"Ningún modelo TTS respondió correctamente. Último error: {last_err}")


def group_parts_by_lesson(parts_dir: Path):
    files = sorted(f for f in parts_dir.glob("[0-9]*.txt") if not f.name.startswith("00_"))
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
    model_id: str = "auto",
    voice_maestro: str = "Orus",
    voice_alumno: str = "Puck",
    pause_between_parts_sec: float = 0.6,
    delay_between_requests_sec: float = 3.0,
):
    from google import genai
    from google.genai import types

    client = genai.Client(
        api_key=api_key,
        vertexai=False,
        http_options=types.HttpOptions(api_version="v1beta"),
    )
    models_to_try = CANDIDATE_MODELS if model_id == "auto" else [model_id] + [m for m in CANDIDATE_MODELS if m != model_id]

    parts_dir = ensure_scripts_downloaded(out_dir / "_guiones_descargados")
    by_lesson = group_parts_by_lesson(parts_dir)

    out_dir.mkdir(parents=True, exist_ok=True)
    parts_cache_dir = out_dir / "partes_wav"
    parts_cache_dir.mkdir(parents=True, exist_ok=True)

    selected_keys = [
        k for k in sorted(by_lesson.keys())
        if not lesson_prefixes or any(k.lower().startswith(p.lower().strip()) or p.lower().strip() in k.lower() for p in lesson_prefixes)
    ]
    if not selected_keys:
        raise ValueError(f"No se encontraron lecciones que coincidan con: {lesson_prefixes}")

    generated_files = []
    working_model = None

    for lesson_key in selected_keys:
        part_files = by_lesson[lesson_key]
        print(f"\n============================================================")
        print(f"🎧 Generando {lesson_key} ({len(part_files)} parte(s))")
        print(f"   Voz Maestro: {voice_maestro} | Voz Alumno: {voice_alumno}")
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
                order = [working_model] + [m for m in models_to_try if m != working_model] if working_model else models_to_try
                raw_bytes, used_info = synthesize_part_with_fallback(
                    api_key=api_key,
                    client=client,
                    script_text=script_text,
                    candidate_models=order,
                    voice_maestro=voice_maestro,
                    voice_alumno=voice_alumno,
                    style_maestro=DEFAULT_STYLE_MAESTRO,
                    style_alumno=DEFAULT_STYLE_ALUMNO,
                )
                used_m = used_info.split(" ")[0]
                if working_model != used_m:
                    working_model = used_m
                    print(f"   ✅ Modelo activo confirmado: {used_info}")
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
    parser.add_argument("--lecciones", default="35,01", help="Prefijos separados por coma (ej. '35,01,03' o 'todas').")
    parser.add_argument("--modelo", default="auto", help="Modelo TTS o 'auto' para autodetectar.")
    parser.add_argument("--voz-maestro", default="Orus", help="Voz para Maestro.")
    parser.add_argument("--voz-alumno", default="Puck", help="Voz para Alumno.")
    parser.add_argument("--salida", default="./Audios_Escuela_Precursores", help="Carpeta de salida.")
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
