"""마크다운 → 스타일 적용 HTML → PDF 변환 (Chrome headless)""" 
import subprocess, sys
from pathlib import Path
import markdown

DOCS = Path(__file__).parent
NAME = sys.argv[1] if len(sys.argv) > 1 else "meeting"

md_text = (DOCS / (NAME + ".md")).read_text(encoding="utf-8")
body = markdown.markdown(md_text, extensions=["tables", "fenced_code"])

head = """<!DOCTYPE html>
<html lang="ko"><head><meta charset="utf-8"><style>
@page { size: A4; margin: 14mm 14mm; }
body { font-family: 'Apple SD Gothic Neo', sans-serif; max-width: 760px;
       margin: 0; line-height: 1.5; color: #1a1a1a; font-size: 13.5px; }
h1 { font-size: 21px; border-bottom: 3px solid #2c5f8a; padding-bottom: 8px;
     color: #2c5f8a; margin: 0 0 10px; }
h2 { font-size: 18px; border-bottom: 1.5px solid #ccc; padding-bottom: 5px;
     margin-top: 22px; color: #2c5f8a; page-break-after: avoid; }
h3 { font-size: 14px; color: #444; margin-top: 16px; page-break-after: avoid; }
table { border-collapse: collapse; width: 100%; margin: 8px 0; }
th, td { border: 1px solid #bbb; padding: 4px 7px; text-align: left;
         font-size: 11px; vertical-align: top; line-height: 1.4; }
th { background: #eef3f8; font-weight: 600; }
tr { page-break-inside: avoid; }
thead { display: table-header-group; }
tr:nth-child(even) td { background: #fafbfd; }
code { background: #f2f2f2; padding: 2px 4px; border-radius: 3px;
       font-family: Menlo, monospace; font-size: 11.5px; }
pre { background: #f6f6f6; border: 1px solid #ddd; border-radius: 6px;
      padding: 8px 10px; font-size: 11px; }
pre code { background: none; white-space: pre-wrap; }
hr { border: none; border-top: 1px solid #ddd; margin: 20px 0; }
li { margin: 3px 0; }
strong { color: #111; }
ul, ol { padding-left: 20px; margin: 8px 0; }
</style></head><body>"""

html = head + body + "</body></html>"
html_path = DOCS / (NAME + ".html")
html_path.write_text(html, encoding="utf-8")
print("HTML ok:", html_path)

chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
pdf_path = DOCS / (NAME + ".pdf")
r = subprocess.run([chrome, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    "--print-to-pdf=" + str(pdf_path), "file://" + str(html_path)],
                   capture_output=True, text=True)
if pdf_path.exists():
    print("PDF ok:", pdf_path, pdf_path.stat().st_size, "bytes")
else:
    print("PDF fail:", r.stderr[-400:])
    sys.exit(1)
