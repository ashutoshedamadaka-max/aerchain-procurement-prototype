"""Render vendor documents into light previews the hosted app can show without PyMuPDF or python-docx.

Run: python scripts/build_previews.py  ->  dataset/artifacts/previews/{V2_p1.png..., V3_response.txt}
"""
from pathlib import Path

import docx
import fitz

ART = Path(__file__).resolve().parents[1] / "dataset" / "artifacts"
OUT = ART / "previews"
OUT.mkdir(exist_ok=True)

for i, page in enumerate(fitz.open(ART / "V2_response.pdf"), 1):
    page.get_pixmap(dpi=110).save(OUT / f"V2_p{i}.png")
(OUT / "V3_response.txt").write_text("\n\n".join(p.text for p in docx.Document(ART / "V3_response.docx").paragraphs if p.text.strip()), encoding="utf-8")
print(sorted(p.name for p in OUT.iterdir()))
