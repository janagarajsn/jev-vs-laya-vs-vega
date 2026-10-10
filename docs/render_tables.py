"""Render every Markdown table in medium-article.md as a PNG, for Medium.

Medium's editor has no tables, so each table is screenshotted with headless
Chrome and a Medium-ready copy of the article is written with each table
swapped for its image:

    python3 docs/render_tables.py

Outputs:
    docs/images/tables/table-NN.png   one image per table, in article order
    docs/medium-article.medium.md     the article with tables replaced by images
"""
import html
import re
import subprocess
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
SOURCE = DOCS / "medium-article.md"
MEDIUM_COPY = DOCS / "medium-article.medium.md"
OUT_DIR = DOCS / "images" / "tables"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
SCALE = 2  # render at 2x so images stay sharp on retina screens

CSS = """
body { margin: 0; background: #ffffff; font-family: -apple-system, BlinkMacSystemFont,
       "Segoe UI", Helvetica, Arial, sans-serif; color: #242424; }
.wrap { padding: 20px; }
table { border-collapse: collapse; width: 100%; font-size: 17px; line-height: 1.4; }
th { background: #f2f2f2; font-weight: 600; text-align: left; }
th, td { border: 1px solid #dcdcdc; padding: 10px 12px; vertical-align: top; }
tr:nth-child(even) td { background: #fafafa; }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 15px;
       background: #f0f0f0; padding: 1px 5px; border-radius: 4px; }
"""

# Measure the rendered height with --dump-dom, which runs this script first.
MEASURE_JS = """
<script>
addEventListener("load", () => {
  const h = Math.ceil(document.querySelector(".wrap").getBoundingClientRect().height);
  document.body.setAttribute("data-height", h);
});
</script>
"""


def inline_md(text):
    """Escape a cell, then turn **bold**, *italic* and `code` into HTML."""
    text = html.escape(text.strip())
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<![*\w])\*([^*]+)\*(?![*\w])", r"<i>\1</i>", text)
    return text


def split_row(line):
    return [c for c in line.strip().strip("|").split("|")]


def table_html(rows):
    header, body = rows[0], rows[2:]  # rows[1] is the |---| separator
    # Size by the longest row's text, so short numeric tables aren't stretched wide.
    longest = max(len(re.sub(r"[*`]", "", "".join(r))) for r in [header, *body])
    width = 720 if longest <= 80 else 980
    th = "".join(f"<th>{inline_md(c)}</th>" for c in header)
    trs = "".join("<tr>" + "".join(f"<td>{inline_md(c)}</td>" for c in r) + "</tr>" for r in body)
    page = (f"<!doctype html><html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body><div class='wrap'><table><thead><tr>{th}</tr></thead>"
            f"<tbody>{trs}</tbody></table></div>{MEASURE_JS}</body></html>")
    return page, width


def chrome(*args):
    return subprocess.run([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                           *args], capture_output=True, text=True, check=True).stdout


def render(page, width, out_png, tmp):
    src = Path(tmp) / "table.html"
    src.write_text(page, encoding="utf-8")
    dom = chrome(f"--window-size={width},4000", "--dump-dom", src.as_uri())
    height = int(re.search(r'data-height="(\d+)"', dom).group(1))
    chrome(f"--window-size={width},{height}", f"--force-device-scale-factor={SCALE}",
           f"--screenshot={out_png}", src.as_uri())


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    lines = SOURCE.read_text(encoding="utf-8").splitlines()
    out_lines, n, i = [], 0, 0
    with tempfile.TemporaryDirectory() as tmp:
        while i < len(lines):
            is_table = (lines[i].startswith("|") and i + 1 < len(lines)
                        and re.match(r"^\|[\s:|-]+\|$", lines[i + 1].strip()))
            if not is_table:
                out_lines.append(lines[i])
                i += 1
                continue
            rows = []
            while i < len(lines) and lines[i].startswith("|"):
                rows.append(split_row(lines[i]))
                i += 1
            n += 1
            name = f"table-{n:02d}.png"
            page, width = table_html(rows)
            render(page, width, OUT_DIR / name, tmp)
            out_lines.append(f"![Table {n}](images/tables/{name})")
            print(f"{name}  {len(rows) - 2} rows x {len(rows[0])} cols")
    MEDIUM_COPY.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    print(f"{n} tables rendered; wrote {MEDIUM_COPY.name}")


if __name__ == "__main__":
    main()
