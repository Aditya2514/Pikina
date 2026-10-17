"""
One-time download of the embedding model used by Pikina's memory.

    python -m pikina.memory.download

Pikina itself runs the model in offline mode, so this is the only step that
needs the internet. Requires the optional extra: pip install "pikina[memory]".
"""
import os
import sys


def main() -> int:
    from pikina.memory.vector_store import EMBEDDING_MODEL  # (this import turns offline mode on)
    os.environ.pop("HF_HUB_OFFLINE", None)                 # ...so switch it off afterwards
    try:
        from sentence_transformers import SentenceTransformer
    except ImportError:
        print('Install the memory extra first: pip install "pikina[memory]"')
        return 1
    print(f"Downloading {EMBEDDING_MODEL} ...")
    SentenceTransformer(EMBEDDING_MODEL)
    print("Done. Memory features will work offline from now on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
