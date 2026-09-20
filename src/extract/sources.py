"""Format sources. Each turns a file into (a) a text dump for the model with a locator on every line and (b) deterministic
lookups the verifier uses: text_at, verbatim, row_of, candidate_rows. Locator schemes:
  xlsx  Sheet!H10                     pdf   p2:r13:desc | p2:r13:qty | p2:r13:total (table row = printed item no.) | p2:L7 (free line)
  docx  para12 (paragraph number)     txt   L6 (line number in the file)
"""
import re

SIZE = re.compile(r"(\d+)\s*[x×X*]\s*(\d+)\s*[x×X*]\s*(\d+)")


def _norm(t):
    return re.sub(r"\s+", " ", t or "").strip()


class TextSourceBase:
    """Free-text sources: a snippet is verbatim if it appears in the text at its locator, whitespace-normalised."""

    def canonical(self, anchor, sheet=None):
        """Repair formatting slop in a locator (a stray 'sheet!' prefix; a page tag dropped from a table locator) without changing what it points at."""
        a = anchor.rpartition("!")[2].strip()
        return a

    def verbatim(self, anchor, snippet):
        t, s = self.text_at(anchor), _norm(snippet)
        return bool(t and s and s in _norm(t))

    def is_text_number(self, anchor):
        return False

    def candidate_rows(self, sheets=None):
        return set()


class PdfSource(TextSourceBase):
    kind = "pdf"

    def __init__(self, path):
        import fitz
        self.rows, self.free, self.order = {}, {}, []
        for pno, page in enumerate(fitz.open(path), 1):
            lines = []
            for b in page.get_text("dict")["blocks"]:
                for ln in b.get("lines", []):
                    t = "".join(s["text"] for s in ln["spans"]).strip()
                    if t:
                        lines.append((round(ln["bbox"][1], 1), ln["bbox"][0], t, round(max(s["size"] for s in ln["spans"]), 1)))
            lines.sort()
            hdr = {t: (y, x, sz) for y, x, t, sz in lines if t in ("Item", "Description and offered rate", "Quantity", "Amount INR")}
            used, tag = set(), f"p{pno}"
            if "Item" in hdr:
                hy, ix, sz = hdr["Item"]
                cols = sorted((hdr[k][1], name) for k, name in (("Description and offered rate", "desc"), ("Quantity", "qty"), ("Amount INR", "total")))
                items = [(y, int(t)) for y, x, t, s in lines if t.isdigit() and s == sz and abs(x - ix) < 3 and y > hy]
                used |= {i for i, (y, x, t, s) in enumerate(lines) if (y, x, t) == (hy, ix, "Item") or y == hy and s == sz}
                for y0, item in items:
                    cells = {"desc": [], "qty": [], "total": []}
                    for i, (y, x, t, s) in enumerate(lines):
                        if s == sz and 0 <= y - y0 <= 12 and x >= cols[0][0] - 3:
                            col = [n for cx, n in cols if x >= cx - 3][-1]
                            cells[col].append(t)
                            used.add(i)
                    used |= {i for i, (y, x, t, s) in enumerate(lines) if y == y0 and abs(x - ix) < 3 and t == str(item)}
                    self.rows[(tag, item)] = {k: " ".join(v) for k, v in cells.items()}
                    self.order.append(("row", tag, item))
            k = 0
            for i, (y, x, t, s) in enumerate(lines):
                if i not in used:
                    k += 1
                    self.free[f"{tag}:L{k}"] = (t, s)
                    self.order.append(("free", f"{tag}:L{k}"))

    def canonical(self, anchor, sheet=None):
        a = super().canonical(anchor, sheet)
        return f"{sheet}:{a}" if sheet and re.fullmatch(r"r\d+:(desc|rate|qty|total)", a) else a

    def _row(self, anchor):
        m = re.fullmatch(r"(p\d+):r(\d+):(desc|rate|qty|total)", anchor)
        return m and (self.rows.get((m.group(1), int(m.group(2)))), m.group(3))

    def text_at(self, anchor):
        r = self._row(anchor)
        if r:
            row, col = r
            return row and row["desc" if col == "rate" else col]
        return self.free.get(anchor, (None,))[0]

    def row_of(self, anchor):
        m = re.fullmatch(r"(p\d+):r(\d+):\w+", anchor)
        return (m.group(1), int(m.group(2))) if m else None

    def candidate_rows(self, sheets=None):
        return set(self.rows)

    def dump(self, focus=None, size_pattern=None):
        out = []
        for e in self.order:
            if e[0] == "row":
                _, tag, item = e
                if focus and tag in focus and item not in focus[tag]:
                    continue
                r = self.rows[(tag, item)]
                out.append(f"{tag}:r{item} (table row, printed item no. {item})\n  {tag}:r{item}:desc {r['desc']!r}\n"
                           f"  {tag}:r{item}:qty {r['qty']!r}\n  {tag}:r{item}:total {r['total']!r}")
            else:
                t, s = self.free[e[1]]
                out.append(f"{e[1]} [{s:g}pt] {t!r}")
        return "\n".join(out)


class DocxSource(TextSourceBase):
    kind = "docx"

    def __init__(self, path):
        import docx
        self.paras = {i: p.text for i, p in enumerate(docx.Document(path).paragraphs, 1) if p.text.strip()}

    def text_at(self, anchor):
        m = re.fullmatch(r"para(\d+)", anchor)
        return self.paras.get(int(m.group(1))) if m else None

    def row_of(self, anchor):
        m = re.fullmatch(r"para(\d+)", anchor)
        return ("doc", int(m.group(1))) if m else None

    def candidate_rows(self, sheets=None):
        return {("doc", i) for i, t in self.paras.items() if SIZE.search(t)}

    def dump(self, focus=None, size_pattern=None):
        return "\n".join(f"para{i}: {t!r}" for i, t in self.paras.items()
                         if not (focus and "doc" in focus and i not in focus["doc"] and SIZE.search(t)))


class EmailSource(TextSourceBase):
    kind = "email"

    def __init__(self, path):
        self.lines = {i: t for i, t in enumerate(open(path, encoding="utf-8").read().splitlines(), 1) if t.strip()}

    def text_at(self, anchor):
        m = re.fullmatch(r"L(\d+)", anchor)
        return self.lines.get(int(m.group(1))) if m else None

    def row_of(self, anchor):
        return None

    def dump(self, focus=None, size_pattern=None):
        return "\n".join(f"L{i}: {t!r}" for i, t in self.lines.items())
