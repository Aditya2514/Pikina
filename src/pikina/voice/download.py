"""
One-time download of a whisper.cpp speech model into Pikina's data folder.

    python -m pikina.voice.download            # base.en, 142 MB (recommended)
    python -m pikina.voice.download tiny.en    # 75 MB, faster, less accurate
    python -m pikina.voice.download small.en   # 466 MB, slower, more accurate

Voice input works offline after this.
"""
import os
import sys

from pikina.voice.stt import models_dir

URL = "https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-{name}.bin"
SIZES_MB = {"tiny.en": 75, "base.en": 142, "small.en": 466, "tiny": 75, "base": 142, "small": 466}
DEFAULT = "base.en"


def download(name: str = DEFAULT, out=print) -> int:
    import requests
    if name not in SIZES_MB:
        out(f"Unknown model '{name}'. Choose one of: {', '.join(SIZES_MB)}")
        return 2
    target = models_dir() / f"ggml-{name}.bin"
    if target.is_file():
        out(f"Already downloaded: {target}")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(".bin.part")
    out(f"Downloading ggml-{name}.bin (about {SIZES_MB[name]} MB) to {target.parent} ...")
    try:
        with requests.get(URL.format(name=name), stream=True, timeout=30) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            got, shown = 0, -1
            with open(part, "wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    pct = got * 100 // total if total else -1
                    if pct >= 0 and pct // 10 > shown:
                        shown = pct // 10
                        out(f"  {pct}%")
        if total and got != total:
            raise OSError(f"download stopped at {got} of {total} bytes")
        os.replace(part, target)
    except (OSError, requests.RequestException) as exc:
        part.unlink(missing_ok=True)
        out(f"Download failed: {exc}")
        return 1
    out("Done. Say 'voice' in the pikina terminal to try it.")
    return 0


def main() -> int:
    return download(sys.argv[1] if len(sys.argv) > 1 else DEFAULT)


if __name__ == "__main__":
    sys.exit(main())
