"""OCR complet du livre Metre de Batiment (282 pages).
Sauvegarde incrementale en JSON pour resume possible.
"""
import json
import time
import sys
from pathlib import Path

import easyocr
import pymupdf

BOOK_PATH = r"C:\Users\youss\Downloads\Telegram Desktop\Livre Métré de Bâtiment Michel Manteau.pdf"
OUTPUT_PATH = Path("data/livre_metre/ocr_pages.json")
BATCH_SIZE = 10  # save every N pages


def ocr_book():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Load existing progress
    done = {}
    if OUTPUT_PATH.exists():
        with open(OUTPUT_PATH, "r", encoding="utf-8") as f:
            done = json.load(f)
        print(f"Resuming: {len(done)} pages already OCR'd")

    doc = pymupdf.open(BOOK_PATH)
    total = len(doc)
    reader = easyocr.Reader(["fr"], gpu=False, verbose=False)

    t0 = time.time()
    for i in range(total):
        key = str(i)
        if key in done:
            continue

        page = doc[i]
        pix = page.get_pixmap(dpi=200)
        img_path = f"temp_ocr_{i}.png"
        pix.save(img_path)

        results = reader.readtext(img_path)
        text = " ".join([r[1] for r in results])
        done[key] = {"page": i + 1, "text": text, "chars": len(text)}

        Path(img_path).unlink(missing_ok=True)

        # Save incrementally
        if (i + 1) % BATCH_SIZE == 0 or i == total - 1:
            with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
                json.dump(done, f, ensure_ascii=False, indent=1)
            elapsed = time.time() - t0
            pct = len(done) / total * 100
            eta = elapsed / max(len(done) - (total - len(done)), 1) * (total - len(done))
            print(
                f"[{len(done)}/{total}] {pct:.0f}% | "
                f"{elapsed:.0f}s elapsed | ETA {eta/60:.0f}min",
                flush=True,
            )

    # Final save
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(done, f, ensure_ascii=False, indent=1)

    total_chars = sum(v["chars"] for v in done.values())
    non_empty = sum(1 for v in done.values() if v["chars"] > 0)
    print(f"\nDone: {len(done)} pages, {non_empty} with text, {total_chars} total chars")
    doc.close()


if __name__ == "__main__":
    ocr_book()
