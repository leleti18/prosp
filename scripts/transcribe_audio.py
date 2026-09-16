"""
Transcricao de audios via OpenRouter.

Usa a mesma OPENROUTER_API_KEY do resto do projeto e suporta dois caminhos,
escolhidos automaticamente pelo modelo configurado em TRANSCRIPTION_MODEL:

- Modelo STT dedicado (ex: 'openai/whisper-1', cobrado por minuto de audio)
  -> POST /api/v1/audio/transcriptions, enviando o arquivo como multipart.
- Modelo de chat multimodal (ex: 'google/gemini-3.5-flash-lite', cobrado por token)
  -> POST /api/v1/chat/completions, com o audio em base64 no content part
     'input_audio'.

A funcao transcribe_audio() tambem e usada pelo painel (app.py).

Uso:
    python transcribe_audio.py audio1.ogg audio2.m4a
    python transcribe_audio.py audios/*.ogg --md transcricoes.md
    python transcribe_audio.py --model google/gemini-3.5-flash-lite audio.ogg
"""

import argparse
import base64
import glob
import os
import sys
from datetime import datetime

import requests

from config import OPENROUTER_API_KEY, TRANSCRIPTION_MODEL

TRANSCRIPTION_URL = "https://openrouter.ai/api/v1/audio/transcriptions"
CHAT_URL = "https://openrouter.ai/api/v1/chat/completions"

# Limite do endpoint de transcricao do OpenRouter.
MAX_AUDIO_BYTES = 25 * 1024 * 1024

# Extensoes aceitas no upload. 'ogg'/'opus' cobrem o audio de WhatsApp.
SUPPORTED_EXTENSIONS = (
    "aac", "flac", "m4a", "mp3", "mp4", "mpeg", "mpga",
    "oga", "ogg", "opus", "wav", "webm",
)

MIME_BY_EXTENSION = {
    "aac": "audio/aac",
    "flac": "audio/flac",
    "m4a": "audio/mp4",
    "mp3": "audio/mpeg",
    "mp4": "audio/mp4",
    "mpeg": "audio/mpeg",
    "mpga": "audio/mpeg",
    "oga": "audio/ogg",
    "ogg": "audio/ogg",
    "opus": "audio/ogg",
    "wav": "audio/wav",
    "webm": "audio/webm",
}

# Formato declarado no content part 'input_audio' do caminho de chat.
CHAT_FORMAT_BY_EXTENSION = {
    "aac": "aac",
    "flac": "flac",
    "m4a": "m4a",
    "mp3": "mp3",
    "mp4": "m4a",
    "mpeg": "mp3",
    "mpga": "mp3",
    "oga": "ogg",
    "ogg": "ogg",
    "opus": "ogg",
    "wav": "wav",
    "webm": "webm",
}

# Se o nome do modelo tiver uma dessas marcas, ele e um STT dedicado e vai pelo
# endpoint /audio/transcriptions em vez do /chat/completions.
STT_MODEL_HINTS = ("whisper", "stt", "transcribe", "scribe", "voxtral")

CHAT_INSTRUCTION = (
    "Transcreva integralmente o audio, em portugues do Brasil, mantendo as palavras "
    "exatamente como foram ditas. Responda SOMENTE com a transcricao: sem comentarios, "
    "sem aspas, sem traducao e sem resumo. Se o audio estiver mudo ou inaudivel, "
    "responda exatamente: [audio inaudivel]"
)


def file_extension(filename):
    return (os.path.splitext(filename or "")[1] or "").lstrip(".").lower()


def is_supported_audio(filename):
    return file_extension(filename) in SUPPORTED_EXTENSIONS


def is_stt_model(model):
    model = (model or "").lower()
    return any(hint in model for hint in STT_MODEL_HINTS)


def _headers():
    if not OPENROUTER_API_KEY:
        raise RuntimeError("OPENROUTER_API_KEY precisa estar no .env pra transcrever audios.")
    return {"Authorization": f"Bearer {OPENROUTER_API_KEY}"}


def _raise_for_error(response):
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:500]
        raise RuntimeError(f"OpenRouter respondeu {response.status_code}: {detail}")


def _validate(audio_bytes, filename):
    if not audio_bytes:
        raise ValueError(f"Arquivo de audio vazio: {filename}")
    if len(audio_bytes) > MAX_AUDIO_BYTES:
        size_mb = len(audio_bytes) / (1024 * 1024)
        raise ValueError(
            f"Audio '{filename}' tem {size_mb:.1f} MB e o limite e 25 MB. "
            "Corte o audio em partes menores."
        )
    extension = file_extension(filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Extensao '.{extension or '?'}' nao suportada. "
            f"Use uma destas: {', '.join(SUPPORTED_EXTENSIONS)}."
        )
    return extension


def _transcribe_via_stt(audio_bytes, filename, extension, model, language, timeout):
    files = {"file": (filename, audio_bytes, MIME_BY_EXTENSION.get(extension, "application/octet-stream"))}
    data = {"model": model}
    if language:
        data["language"] = language

    response = requests.post(
        TRANSCRIPTION_URL, headers=_headers(), files=files, data=data, timeout=timeout
    )
    _raise_for_error(response)
    payload = response.json()
    usage = payload.get("usage") or {}
    return {
        "text": (payload.get("text") or "").strip(),
        "model": payload.get("model") or model,
        "cost": usage.get("cost"),
        "duration_seconds": usage.get("seconds") or usage.get("duration"),
    }


def _transcribe_via_chat(audio_bytes, filename, extension, model, timeout):
    body = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": CHAT_INSTRUCTION},
                    {
                        "type": "input_audio",
                        "input_audio": {
                            "data": base64.b64encode(audio_bytes).decode("ascii"),
                            "format": CHAT_FORMAT_BY_EXTENSION.get(extension, "mp3"),
                        },
                    },
                ],
            }
        ],
        "temperature": 0,
        "usage": {"include": True},
    }

    headers = dict(_headers())
    headers["Content-Type"] = "application/json"
    response = requests.post(CHAT_URL, headers=headers, json=body, timeout=timeout)
    _raise_for_error(response)
    payload = response.json()

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter nao retornou transcricao para '{filename}': {payload}")

    usage = payload.get("usage") or {}
    return {
        "text": (choices[0].get("message", {}).get("content") or "").strip(),
        "model": payload.get("model") or model,
        "cost": usage.get("cost"),
        "duration_seconds": None,
    }


def transcribe_audio(audio_bytes, filename, model=None, language="pt", timeout=300):
    """Transcreve um audio e devolve {'text', 'model', 'cost', 'duration_seconds'}.

    audio_bytes: conteudo do arquivo (bytes).
    filename: nome do arquivo, usado pra descobrir o formato do audio.
    model: slug do OpenRouter; por padrao usa TRANSCRIPTION_MODEL do .env.
    language: idioma esperado, so usado pelos modelos STT dedicados.
    """
    model = model or TRANSCRIPTION_MODEL
    extension = _validate(audio_bytes, filename)

    if is_stt_model(model):
        return _transcribe_via_stt(audio_bytes, filename, extension, model, language, timeout)
    return _transcribe_via_chat(audio_bytes, filename, extension, model, timeout)


def transcribe_file(path, model=None, language="pt"):
    with open(path, "rb") as handle:
        audio_bytes = handle.read()
    return transcribe_audio(audio_bytes, os.path.basename(path), model=model, language=language)


def expand_paths(patterns):
    """Resolve '~' e curingas nos caminhos.

    O PowerShell nao expande curingas para programas externos, entao
    'audios/*.ogg' chega aqui como texto literal e precisa ser expandido.
    No bash a expansao ja veio pronta e glob() so devolve o proprio arquivo.
    """
    paths = []
    for pattern in patterns:
        expanded = os.path.expanduser(pattern)
        matches = sorted(glob.glob(expanded))
        if matches:
            paths.extend(matches)
        elif any(char in expanded for char in "*?["):
            print(f"[aviso] nenhum arquivo casa com '{pattern}'")
        else:
            # Sem curinga: mantem o caminho pra o erro de abertura ser explicito.
            paths.append(expanded)
    return paths


def build_markdown(entries):
    """Monta um markdown unico com as transcricoes de varios audios.

    entries: lista de dicts com 'name' e ('result' ou 'error').
    """
    done = [entry for entry in entries if entry.get("result")]
    failed = [entry for entry in entries if entry.get("error")]

    header = [f"Gerado em {datetime.now():%d/%m/%Y %H:%M}", f"{len(done)} audio(s)"]
    total_seconds = sum((entry["result"].get("duration_seconds") or 0) for entry in done)
    if total_seconds:
        header.append(f"{total_seconds / 60:.1f} min de audio")
    total_cost = sum((entry["result"].get("cost") or 0) for entry in done)
    if total_cost:
        header.append(f"custo total US$ {total_cost:.4f}")

    lines = ["# Transcricoes de audio", "", " | ".join(header), ""]

    for index, entry in enumerate(done, start=1):
        result = entry["result"]
        details = [f"modelo: `{result['model']}`"]
        if result.get("duration_seconds"):
            details.append(f"duracao: {result['duration_seconds']}s")
        if result.get("cost") is not None:
            details.append(f"custo: US$ {result['cost']:.4f}")
        lines += [
            f"## {index}. {entry['name']}",
            "",
            "_" + " · ".join(details) + "_",
            "",
            result.get("text") or "_(sem transcricao)_",
            "",
        ]

    if failed:
        lines += ["## Falhas", ""]
        lines += [f"- **{entry['name']}**: {entry['error']}" for entry in failed]
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Transcreve audios via OpenRouter.")
    parser.add_argument(
        "paths",
        nargs="+",
        help="Arquivos de audio a transcrever. Aceita curingas (ex: audios/*.ogg).",
    )
    parser.add_argument("--model", default=None, help=f"Slug do modelo (padrao: {TRANSCRIPTION_MODEL}).")
    parser.add_argument("--language", default="pt", help="Idioma esperado (padrao: pt).")
    parser.add_argument(
        "--md",
        default=None,
        metavar="ARQUIVO",
        help="Salva todas as transcricoes juntas num arquivo markdown.",
    )
    args = parser.parse_args()

    paths = expand_paths(args.paths)
    if not paths:
        print("[erro] nenhum arquivo de audio encontrado.")
        return 1

    entries = []
    failures = 0
    for path in paths:
        name = os.path.basename(path)
        print(f"\n=== {path} ===")
        try:
            result = transcribe_file(path, model=args.model, language=args.language)
        except (OSError, ValueError, RuntimeError, requests.RequestException) as error:
            failures += 1
            entries.append({"name": name, "error": str(error)})
            print(f"[erro] {error}")
            continue
        entries.append({"name": name, "result": result})
        print(result["text"] or "[sem transcricao]")
        details = [f"modelo: {result['model']}"]
        if result.get("duration_seconds"):
            details.append(f"duracao: {result['duration_seconds']}s")
        if result.get("cost") is not None:
            details.append(f"custo: US$ {result['cost']:.4f}")
        print(f"({' | '.join(details)})")

    if args.md:
        # encoding explicito: no Windows o padrao nao e utf-8 e quebra os acentos.
        with open(args.md, "w", encoding="utf-8") as handle:
            handle.write(build_markdown(entries))
        print(f"\nMarkdown salvo em {args.md}")

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
