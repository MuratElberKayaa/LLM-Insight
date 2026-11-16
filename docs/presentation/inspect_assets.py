from pathlib import Path
import json
import urllib.request
from bs4 import BeautifulSoup
import pymupdf as fitz
from PIL import Image, ImageOps, ImageDraw

ROOT = Path(__file__).resolve().parent
A = ROOT / 'assets'
doc = fitz.open(r'C:\Users\PC\Downloads\EchoNote_Proje_Sunumu_Sena_Yetim.pdf')
for name, xref, smask in [('ito',4,6), ('softito',7,8)]:
    p = fitz.Pixmap(doc, xref)
    p = fitz.Pixmap(p, fitz.Pixmap(doc, smask))
    p.save(str(A / (name+'.png')))

base = 'http://127.0.0.1:3001'
query = '?project=1&from=2026-11-12T00%3A00%3A00Z&to=2026-11-13T00%3A00%3A00Z&provenance=real'
pages = {'overview': '/'+query, 'models':'/models'+query, 'traces':'/traces'+query,
         'trace':'/traces/6f7261b074977c1b8a4804d43b103add?project=1',
         'experiments':'/experiments?project=1', 'datasets':'/datasets?project=1'}
for name, url in pages.items():
    raw = urllib.request.urlopen(base+url, timeout=30).read().decode()
    soup = BeautifulSoup(raw, 'html.parser')
    for s in soup(['script', 'style']): s.decompose()
    (ROOT/(name+'-live.txt')).write_text(soup.get_text('\n',strip=True), encoding='utf-8')
    print(name, ' '.join(soup.stripped_strings)[:180] )
    if name == 'experiments':
        print('experiment links',[(x.get('href'),x.text) for x in soup.select('a[href^="/experiments/"]')])
(ROOT/'capture-urls.json').write_text(json.dumps({k:base+v for k,v in pages.items()},indent=2),encoding='utf-8')

fs=list(Path('docs/reports/phase-5b-screenshots').glob('*.png'))
out=Image.new('RGB',(1200,((len(fs)+2)//3)*270),'#ddd')
d=ImageDraw.Draw(out)
for i,p in enumerate(fs):
    out.paste(ImageOps.contain(Image.open(p),(390,240)),((i%3)*400,(i//3)*270))
    d.text(((i%3)*400,(i//3)*270+244),p.stem,fill='black')
out.save(A/'old-ui-contact.png')
