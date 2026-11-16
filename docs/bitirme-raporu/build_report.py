"""Build the report locally; no application writes or provider requests."""
from pathlib import Path
import argparse
import re
import json
import hashlib
from html import escape
from io import BytesIO

from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.enums import TA_JUSTIFY, TA_CENTER
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer, PageBreak,
                               Table, TableStyle, Image, Preformatted, KeepTogether)
from reportlab.graphics.shapes import Drawing, Rect, String, Line, Polygon
from reportlab.graphics import renderPDF
from docx import Document
from docx.shared import Pt, Cm, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from PIL import Image as PILImage, ImageOps, ImageDraw
import pymupdf

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--output-stem', default='LLM-Insight-Bitirme-Raporu')
STEM = parser.parse_args().output_stem
if Path(STEM).name != STEM:
    parser.error('--output-stem must be a filename without directories')
TEMPLATE = Path('C:/Users/PC/Desktop/PPE_Bitirme_Raporu/PPE-Safety-Monitor-Bitirme-Raporu.docx')
FONT = Path('C:/Windows/Fonts')
for name, file in [('TNR','times.ttf'),('TNRB','timesbd.ttf'),('TNRI','timesi.ttf'),
                   ('TNRBI','timesbi.ttf'),('Mono','consola.ttf')]:
    pdfmetrics.registerFont(TTFont(name, str(FONT/file)))
pdfmetrics.registerFontFamily('TNR',normal='TNR',bold='TNRB',italic='TNRI',boldItalic='TNRBI')
WIDTH = 595.276 - 5*cm
COVER_LOGO = ROOT / 'docs/presentation/assets/softito.png'
with PILImage.open(COVER_LOGO) as logo_image:
    LOGO_WIDTH = 190
    LOGO_HEIGHT = LOGO_WIDTH * logo_image.height / logo_image.width
styles = {
 'p': ParagraphStyle('p',fontName='TNR',fontSize=12,leading=14.4,spaceAfter=8,alignment=TA_JUSTIFY),
 'h1': ParagraphStyle('h1',fontName='TNRB',fontSize=15,leading=18,spaceAfter=12,textColor=colors.HexColor('#203c70'),keepWithNext=True),
 'h2': ParagraphStyle('h2',fontName='TNRBI',fontSize=13,leading=16,spaceAfter=9,textColor=colors.HexColor('#1670c1'),keepWithNext=True),
 'caption': ParagraphStyle('caption',fontName='TNRI',fontSize=10,leading=12,spaceAfter=10,alignment=TA_CENTER),
 'table': ParagraphStyle('table',fontName='TNR',fontSize=9.5,leading=11.4),
 'code': ParagraphStyle('code',fontName='Mono',fontSize=8,leading=10,backColor=colors.HexColor('#e4e4e4'),borderPadding=7,spaceAfter=10),
 'cover': ParagraphStyle('cover',fontName='TNRB',fontSize=21,leading=27,spaceAfter=20,alignment=TA_CENTER),
 'toc': ParagraphStyle('toc',fontName='TNR',fontSize=11,leading=14,spaceAfter=4),
}

def rich(s):
    s = escape(s)
    s = re.sub(r'\*\*(.+?)\*\*',r'<b>\1</b>',s)
    s = re.sub(r'(https?://[^\s]+)',r'<link href="\1" color="#1766a3">\1</link>',s)
    return s

def parse(source):
    lines=source.splitlines(); result=[]; i=0
    while i<len(lines):
        line=lines[i].strip()
        if not line: i+=1; continue
        if line.startswith('```'):
            lang=line[3:]; i+=1; code=[]
            while i<len(lines) and not lines[i].startswith('```'):
                code.append(lines[i]); i+=1
            result.append(('code', '\n'.join(code),lang))
        elif line.startswith('|'):
            rows=[]
            while i<len(lines) and lines[i].startswith('|'):
                rows.append([x.strip() for x in lines[i].strip('|').split('|')]); i+=1
            result.append(('table',rows)); continue
        elif line.startswith('!['):
            m=re.match(r'!\[(.*?)\]\((.*?)\)',line); result.append(('image',m[2],m[1]))
        elif line.startswith('@'): result.append((line[1:],))
        elif line.startswith('## '): result.append(('h2',line[3:]))
        elif line.startswith('# '): result.append(('h1',line[2:]))
        else: result.append(('p',line))
        i+=1
    return result

def architecture():
    d=Drawing(WIDTH,240)
    def box(x,y,w,h,title,sub):
        d.add(Rect(x,y,w,h,fillColor=colors.HexColor('#edf3f9'),strokeColor=colors.HexColor('#315a80'),radius=4))
        d.add(String(x+w/2,y+h-17,title,fontName='TNRB',fontSize=11,textAnchor='middle'))
        d.add(String(x+w/2,y+12,sub,fontName='TNR',fontSize=9,textAnchor='middle'))
    def arrow(x1,y1,x2,y2):
        d.add(Line(x1,y1,x2,y2,strokeColor=colors.HexColor('#315a80')))
        if y2<y1: pts=[x2,y2,x2-3,y2+7,x2+3,y2+7]
        elif x2>x1: pts=[x2,y2,x2-7,y2-3,x2-7,y2+3]
        else: pts=[x2,y2,x2+7,y2-3,x2+7,y2+3]
        d.add(Polygon(pts,fillColor=colors.HexColor('#315a80'),strokeColor=None))
    box(0,185,210,50,'Gözlenen uygulama + Python SDK','Model çağrısı → metadata-only span')
    box(WIDTH-210,185,210,50,'Tamamlanmış canlı A/B raporu','CLI → sabit kural kanıtı → import')
    arrow(105,185,105,150); arrow(WIDTH-105,185,WIDTH-105,150)
    d.add(String(115,165,'OTLP/HTTP',fontName='TNR',fontSize=9))
    box(0,100,WIDTH,50,'FastAPI: doğrula → normalize et → tekilleştir → commit','/v1/traces  |  /api/v1/*  |  teşhis, inceleme ve deney servisleri')
    arrow(100,100,100,55); arrow(WIDTH-100,100,WIDTH-100,55)
    box(0,5,200,50,'PostgreSQL 17','Trace / span / usage / review / experiment')
    box(WIDTH-225,5,225,50,'Next.js → Tarayıcı','Yönetim anahtarı yalnız sunucuda')
    return d

def footer(c,doc):
    c.setFont('TNR',10); c.drawCentredString(595.276/2,34,str(doc.page))

class Report(SimpleDocTemplate):
    def afterFlowable(self,flow):
        if isinstance(flow,Paragraph) and flow.style.name in ('h1','h2'):
            text=flow.getPlainText(); self.heading_pages[text]=self.page
            self.canv.bookmarkPage(text)
            self.canv.addOutlineEntry(text,text,level=0 if flow.style.name=='h1' else 1,closed=False)

blocks=parse((HERE/'rapor.md').read_text(encoding='utf-8'))
headings=[b for b in blocks if b[0] in ('h1','h2')]

def pdf_build(toc):
    story=[]; cover=False; cover_count=0
    for b in blocks:
        kind=b[0]
        if kind=='cover':
            cover=True
            story.extend([Spacer(1,25), Image(str(COVER_LOGO),LOGO_WIDTH,LOGO_HEIGHT),Spacer(1,18)])
            continue
        if kind=='page': story.append(PageBreak()); cover=False; continue
        if kind=='toc':
            story.append(Paragraph('İÇİNDEKİLER',styles['h1']))
            for h in headings:
                title=h[1]; indent='' if h[0]=='h1' else '&#160;&#160;&#160;'
                label=f'{indent}{escape(title)}'
                row=Table([[Paragraph(label,styles['toc']),Paragraph(str(toc.get(title,'')),styles['toc'])]],colWidths=[WIDTH-24,24])
                row.setStyle(TableStyle([('VALIGN',(0,0),(-1,-1),'TOP'),('LEFTPADDING',(0,0),(-1,-1),0),('RIGHTPADDING',(0,0),(-1,-1),0)])); story.append(row)
            continue
        if kind=='architecture': story.append(architecture()); story.append(Spacer(1,8)); continue
        if kind in ('p','h1','h2'):
            st=styles[kind]
            if cover and cover_count<2: st=styles['cover']
            if cover: cover_count+=1
            if b[1].startswith('Şekil '): st=styles['caption']
            story.append(Paragraph(rich(b[1]),st))
        elif kind=='code':
            for line in b[1].splitlines():
                assert pdfmetrics.stringWidth(line,'Mono',8) < WIDTH-14, ('code too wide',line)
            codebox=Table([[Preformatted(b[1],styles['code'])]],colWidths=[WIDTH])
            codebox.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,-1),colors.HexColor('#e4e4e4')),('LEFTPADDING',(0,0),(-1,-1),7),('RIGHTPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('BOTTOMPADDING',(0,0),(-1,-1),7)]))
            story.append(KeepTogether([Paragraph(b[2],styles['caption']),codebox,Spacer(1,10)]))
        elif kind=='table':
            rows=[[Paragraph(rich(x),styles['table']) for x in row] for row in b[1]]
            widths=[WIDTH/len(rows[0])]*len(rows[0])
            if b[1][0][0]=='Vaka': widths=[35,240,WIDTH-275]
            table=Table(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
            table.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#e8edf3')),('GRID',(0,0),(-1,-1),.4,colors.grey),('VALIGN',(0,0),(-1,-1),'TOP'),('TOPPADDING',(0,0),(-1,-1),5),('BOTTOMPADDING',(0,0),(-1,-1),5)]))
            story.extend([table,Spacer(1,10)])
        elif kind=='image':
            path=(HERE/b[1]).resolve(); w,h=PILImage.open(path).size
            limit=420 if any(x in b[1] for x in ('live-models-full','experiments-groq-full','experiments-gemini-full')) else 275
            scale=min(WIDTH/w,limit/h)
            story.append(KeepTogether([Image(str(path),w*scale,h*scale),Spacer(1,5),Paragraph(escape(b[2]),styles['caption'])]))
    doc=Report(str(HERE/(STEM+'.pdf')),pagesize=(595.276,841.89),rightMargin=2.5*cm,leftMargin=2.5*cm,topMargin=2.5*cm,bottomMargin=2.5*cm,title='LLM-Insight — Bitirme Projesi Raporu',author='Murat Elber Kaya')
    doc.heading_pages={}; doc.build(story,onFirstPage=footer,onLaterPages=footer)
    return doc.heading_pages

toc=pdf_build({}); toc=pdf_build(toc)

# Render architecture through PDF, preserving a vector drawing in the final PDF.
buf=BytesIO(); renderPDF.drawToFile(architecture(),buf)
ad=pymupdf.open(stream=buf.getvalue(),filetype='pdf')
ad[0].get_pixmap(matrix=pymupdf.Matrix(3,3)).save(str(HERE/'mimari.png'))

doc=Document()  # Recreate inspected template styles without unrelated embedded media.
for child in list(doc._element.body):
    if child.tag!=qn('w:sectPr'): doc._element.body.remove(child)
for s in doc.sections:
    for hf in (s.header,s.footer):
        for child in list(hf._element): hf._element.remove(child)
    p=s.footer.add_paragraph(); p.alignment=1
    fld=OxmlElement('w:fldSimple'); fld.set(qn('w:instr'),'PAGE'); p._p.append(fld)
    s.page_width=Cm(21); s.page_height=Cm(29.7)
    s.top_margin=s.bottom_margin=s.left_margin=s.right_margin=Cm(2.5)
doc.core_properties.author='Murat Elber Kaya'
doc.core_properties.last_modified_by='Murat Elber Kaya'
doc.core_properties.title='LLM-Insight — Bitirme Projesi Raporu'
doc.core_properties.subject='Gözlemlenebilirlik ve prompt değerlendirme platformu'
doc.core_properties.comments='LLM-Insight kaynak kodu ve kayıtlı proje kanıtlarından hazırlanmıştır.'
normal=doc.styles['Normal']; normal.font.name='Times New Roman'; normal.font.size=Pt(12)
normal.paragraph_format.space_after=Pt(8); normal.paragraph_format.line_spacing=1.0
for name,size,color in [('Heading 1',15,'203C70'),('Heading 2',13,'1670C1')]:
    s=doc.styles[name]; s.font.name='Times New Roman'; s.font.size=Pt(size); s.font.color.rgb=RGBColor.from_string(color)
    s.font.bold=True; s.font.italic=(name=='Heading 2')

def wordtext(p,text):
    for i,part in enumerate(re.split(r'\*\*(.*?)\*\*',text)):
        r=p.add_run(part); r.bold=(i%2==1)

cover=False; count=0
for b in blocks:
    kind=b[0]
    if kind=='cover':
        cover=True
        p=doc.add_paragraph(); p.alignment=1
        p.paragraph_format.space_before=Pt(25)
        p.paragraph_format.space_after=Pt(18)
        p.paragraph_format.keep_with_next=True
        p.add_run().add_picture(str(COVER_LOGO),width=Pt(LOGO_WIDTH),height=Pt(LOGO_HEIGHT))
        continue
    if kind=='page': doc.add_page_break(); cover=False; continue
    if kind=='toc':
        doc.add_heading('İÇİNDEKİLER',0)
        for h in headings:
            p=doc.add_paragraph(h[1]); p.paragraph_format.space_after=Pt(3)
            if h[0]=='h2': p.paragraph_format.left_indent=Cm(.5)
        continue
    if kind=='architecture': doc.add_picture(str(HERE/'mimari.png'),width=Cm(16)); continue
    if kind in ('h1','h2'): doc.add_heading(b[1],1 if kind=='h1' else 2); continue
    if kind=='p':
        p=doc.add_paragraph(); wordtext(p,b[1]); p.alignment=3
        if cover and count<2:
            p.alignment=1
            for r in p.runs: r.font.size=Pt(21); r.bold=True
        if cover:
            for r in p.runs: r.font.color.rgb=RGBColor(0,0,0)
            count+=1
    elif kind=='table':
        t=doc.add_table(rows=0,cols=len(b[1][0])); t.style='Table Grid'
        for ri,row in enumerate(b[1]):
            cells=t.add_row().cells
            for cell,text in zip(cells,row):
                p=cell.paragraphs[0]; wordtext(p,text)
                for r in p.runs: r.font.size=Pt(9.5); r.bold=ri==0
                p.paragraph_format.space_after=Pt(3)
                if ri==0:
                    shade=OxmlElement('w:shd'); shade.set(qn('w:fill'),'E8EDF3'); cell._tc.get_or_add_tcPr().append(shade)
        doc.add_paragraph().paragraph_format.space_after=Pt(2)
    elif kind=='code':
        p=doc.add_paragraph(b[2]); p.paragraph_format.keep_with_next=True
        p=doc.add_paragraph(); r=p.add_run(b[1]); r.font.name='Consolas'; r.font.size=Pt(8)
        p.paragraph_format.keep_together=True; p.paragraph_format.line_spacing=Pt(10)
        shade=OxmlElement('w:shd'); shade.set(qn('w:fill'),'E4E4E4'); p._p.get_or_add_pPr().append(shade)
    elif kind=='image':
        path=(HERE/b[1]).resolve(); w,h=PILImage.open(path).size
        limit=420 if any(x in b[1] for x in ('live-models-full','experiments-groq-full','experiments-gemini-full')) else 275
        scale=min(WIDTH/w,limit/h)
        p=doc.add_paragraph(); p.alignment=1; p.paragraph_format.keep_with_next=True
        p.add_run().add_picture(str(path),width=Pt(w*scale),height=Pt(h*scale))
        p=doc.add_paragraph(b[2]); p.alignment=1
        for r in p.runs: r.italic=True; r.font.size=Pt(10)
doc.save(str(HERE/(STEM+'.docx')))

# Verify generated PDF text, geometry, pages and asset provenance.
pdf=pymupdf.open(str(HERE/(STEM+'.pdf')))
alltext='\n'.join(p.get_text() for p in pdf)
assert 'Murat Elber Kaya' in alltext
assert all(h[1] in alltext.replace('\n',' ') for h in headings if len(h[1])<70)
assert '\ufffd' not in alltext
out=[]
for n,p in enumerate(pdf,1):
    for block in p.get_text('dict')['blocks']:
        if block['type']!=0: continue
        for line in block['lines']:
            for span in line['spans']:
                x0,y0,x1,y1=span['bbox']
                if x0<25 or x1>p.rect.width-25 or y0<20 or y1>p.rect.height-18:
                    out.append([n,span['text'],span['bbox']])
assert not out,out
preview=HERE/'preview'; preview.mkdir(exist_ok=True)
thumbs=[]
for i,p in enumerate(pdf):
    pix=p.get_pixmap(matrix=pymupdf.Matrix(.65,.65))
    image=PILImage.frombytes('RGB',[pix.width,pix.height],pix.samples)
    image.save(preview/f'page-{i+1:02}.png')
    thumb=ImageOps.contain(image,(198,280)); tile=PILImage.new('RGB',(214,305),'#dce2e8')
    tile.paste(thumb,((214-thumb.width)//2,5)); ImageDraw.Draw(tile).text((98,287),str(i+1),fill='black'); thumbs.append(tile)
sheet=PILImage.new('RGB',(214*5,305*((len(thumbs)+4)//5)),'white')
for i,t in enumerate(thumbs): sheet.paste(t,((i%5)*214,(i//5)*305))
sheet.save(HERE/'Onizleme.png')
manifest={'pdf_pages':len(pdf),'headings':toc,'code_blocks':sum(b[0]=='code' for b in blocks),'figures':sum(b[0] in ('image','architecture') for b in blocks),'out_of_page_text':out,'source_date':'2026-11-13','author':'Murat Elber Kaya','application_tests_rerun':False,'assets':[]}
for b in blocks:
    if b[0]=='image':
        path=(HERE/b[1]).resolve(); manifest['assets'].append({'file':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'caption':b[2]})
manifest['cover_logo']={'file':str(COVER_LOGO.relative_to(ROOT)),'sha256':hashlib.sha256(COVER_LOGO.read_bytes()).hexdigest(),'placement':'centered above academy title'}
(HERE/'dogrulama.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'pages':len(pdf),'headings':len(toc),'code_blocks':manifest['code_blocks'],'figures':manifest['figures'],'pdf':str(HERE/(STEM+'.pdf'))},ensure_ascii=False))
