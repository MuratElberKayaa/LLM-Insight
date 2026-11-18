"""Editable PPTX + matching vector PDF. Coordinates are 960 x 540 points."""
from pathlib import Path
import math
import json
import argparse
from xml.sax.saxutils import escape
from PIL import Image, ImageOps, ImageDraw
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.colors import HexColor
from pptx import Presentation
from pptx.util import Pt
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR
import pymupdf

P=Path(__file__).resolve().parent
A=P/'assets'
parser=argparse.ArgumentParser()
parser.add_argument('--output-stem',default='LLM-Insight-Sunum')
parser.add_argument('--ab-report',type=Path)
parser.add_argument('--observation',type=Path)
args=parser.parse_args()
output_stem=args.output_stem
ab=json.loads(args.ab_report.read_text(encoding='utf-8')) if args.ab_report else None
observation=json.loads(args.observation.read_text(encoding='utf-8')) if args.observation else None
if ab:
    if not ab.get('complete') or len(ab.get('cases',[])) != 24 or not observation:
        raise ValueError('Live presentation requires 24 verified calls and a fresh observation snapshot')
    if any(not c['verified'] for c in ab['cases']):
        raise ValueError('Unverified live result')
pdf_path=P/(output_stem+'.pdf')
pptx_path=P/(output_stem+'.pptx')
B='143C67'; O='E88736'; W='FFFFFF'; PALE='F0F5FA'; MUTED='476581'; LINE='D7E2EE'
FONT='Segoe UI'
pdfmetrics.registerFont(TTFont('Regular',r'C:\Windows\Fonts\segoeui.ttf'))
pdfmetrics.registerFont(TTFont('Bold',r'C:\Windows\Fonts\segoeuib.ttf'))
pdfmetrics.registerFontFamily('Segoe', normal='Regular',bold='Bold')
prs=Presentation(); prs.slide_width=Pt(960); prs.slide_height=Pt(540)
prs.core_properties.title='LLM Insight — Proje Sunumu'
prs.core_properties.subject='Gözlemlenebilirlikten insan onaylı iyileştirmeye'
prs.core_properties.author='LLM Insight'
pdf=canvas.Canvas(str(pdf_path),pagesize=(960,540),pageCompression=1)
pdf.setTitle('LLM Insight — Proje Sunumu'); pdf.setAuthor('LLM Insight')
notes=[]; checks=[]

def rgb(c): return RGBColor.from_string(c)

class Slide:
    def __init__(self,n,section,title=None,sub=None):
        self.n=n; self.title=title or 'LLM Insight'
        self.s=prs.slides.add_slide(prs.slide_layouts[6])
        self.rect(0,0,960,540,W)
        self.img('ito.png',32,10,30,45)
        self.img('softito.png',841,20,86,33)
        self.text(92,25,700,16,'SOFTİTO YAPAY ZEKÂ AKADEMİSİ   /   LLM INSIGHT',9,MUTED,bold=True)
        self.line(32,65,928,65,LINE,.7)
        if title:
            self.text(35,80,850,14,section.upper(),9.5,B,bold=True)
            self.text(35,102,890,43,title,29,B,bold=True)
            if sub: self.text(35,147,890,38,sub,13.5,MUTED)
        self.line(35,514,925,514,LINE,.6)
        self.rect(35,526,5,5,O)
        self.text(47,521,500,15,'LLM Insight  •  Yerel MVP  •  13 Kasım 2026',8.5,MUTED)
        self.text(869,521,56,15,f'{n:02d} / 10',9,B,bold=True,align='right')
    def rect(self,x,y,w,h,fill,stroke=None,r=0):
        pdf.setFillColor(HexColor('#'+fill))
        pdf.setStrokeColor(HexColor('#'+(stroke or fill)))
        if r: pdf.roundRect(x,540-y-h,w,h,r,fill=1,stroke=bool(stroke))
        else: pdf.rect(x,540-y-h,w,h,fill=1,stroke=bool(stroke))
        sh=self.s.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE if r else MSO_SHAPE.RECTANGLE,Pt(x),Pt(y),Pt(w),Pt(h))
        if r:
            try: sh.adjustments[0]=r/min(w,h)
            except Exception: pass
        sh.fill.solid(); sh.fill.fore_color.rgb=rgb(fill)
        if stroke: sh.line.color.rgb=rgb(stroke); sh.line.width=Pt(.7)
        else: sh.line.fill.background()
    def line(self,x1,y1,x2,y2,color=B,width=1):
        pdf.setStrokeColor(HexColor('#'+color)); pdf.setLineWidth(width)
        pdf.line(x1,540-y1,x2,540-y2)
        from pptx.enum.shapes import MSO_CONNECTOR
        sh=self.s.shapes.add_connector(MSO_CONNECTOR.STRAIGHT,Pt(x1),Pt(y1),Pt(x2),Pt(y2))
        sh.line.color.rgb=rgb(color); sh.line.width=Pt(width)
    def arrow(self,x1,y1,x2,y2,color=O,width=2):
        self.line(x1,y1,x2,y2,color,width)
        ang=math.atan2(y2-y1,x2-x1)
        for d in [-.5,.5]:
            self.line(x2,y2,x2-7*math.cos(ang+d),y2-7*math.sin(ang+d),color,width)
    def text(self,x,y,w,h,txt,size=16,color=B,bold=False,align='left',leading=1.23):
        f='Bold' if bold else 'Regular'
        lines=[]
        for para in txt.split('\n'):
            words=para.split(' '); line=''
            for word in words:
                cand=(line+' '+word).strip()
                if line and pdfmetrics.stringWidth(cand,f,size)>w-2:
                    lines.append(line); line=word
                else: line=cand
            lines.append(line)
        used=len(lines)*size*leading
        if used>h+2: checks.append({'slide':self.n,'text':txt[:65],'used':round(used,1),'box':h})
        pdf.setFillColor(HexColor('#'+color)); pdf.setFont(f,size)
        for i,t in enumerate(lines):
            yy=540-y-size*.97-i*size*leading
            if align=='center': pdf.drawCentredString(x+w/2,yy,t)
            elif align=='right': pdf.drawRightString(x+w,yy,t)
            else: pdf.drawString(x,yy,t)
        box=self.s.shapes.add_textbox(Pt(x),Pt(y),Pt(w),Pt(max(h,used+2)))
        tf=box.text_frame; tf.clear(); tf.word_wrap=False
        tf.margin_left=tf.margin_right=tf.margin_top=tf.margin_bottom=0
        tf.vertical_anchor=MSO_ANCHOR.TOP
        from pptx.enum.text import PP_ALIGN
        for i,t in enumerate(lines):
            p=tf.paragraphs[0] if i==0 else tf.add_paragraph()
            p.text=t; p.font.name=FONT; p.font.size=Pt(size); p.font.bold=bold; p.font.color.rgb=rgb(color)
            p.space_before=Pt(0);p.space_after=Pt(0);p.line_spacing=1.0*leading
            p.alignment={'left':PP_ALIGN.LEFT,'center':PP_ALIGN.CENTER,'right':PP_ALIGN.RIGHT}[align]
    def img(self,file,x,y,w,h):
        path=A/file
        im=Image.open(path); iw,ih=im.size
        scale=min(w/iw,h/ih); ww=iw*scale; hh=ih*scale
        xx=x+(w-ww)/2; yy=y+(h-hh)/2
        pdf.drawImage(str(path),xx,540-yy-hh,ww,hh,mask='auto')
        self.s.shapes.add_picture(str(path),Pt(xx),Pt(yy),width=Pt(ww),height=Pt(hh))
    def pill(self,x,y,w,txt,dark=False):
        self.rect(x,y,w,25,B if dark else PALE,r=5)
        self.text(x+10,y+5,w-20,17,txt,10,W if dark else B,bold=True,align='center')
    def source(self,txt,url=None):
        self.text(35,494,890,15,txt,8,MUTED)
        if url: pdf.linkURL(url,(35,31,925,47),relative=0,thickness=0)
    def finish(self,note):
        if ab:
            if self.n==2:
                note=note.replace('Prompt A/B deneyleri mevcut sürümde sentetik mock ile yapılır.',
                    'Dashboard Experiments sentetik mock kullanır; canlı A/B ayrı kontrollü CLI akışındadır.')
            if self.n==4:
                note='Güncel Project 1 / real / 12 Kasım UTC gözlemleri: '+json.dumps(observation['models'],ensure_ascii=False)+'. GPT-OSS Groq üzerinden çağrılır, OpenAI API çağrısı değildir. Canlı A/B: iki modelde aynı altı sentetik vaka, iki gerçek prompt metni, 24 çağrı. OpenRouter kilitli. Backend Experiments ayrı mock akışıdır. Kaynak: live-observation.json; live-capture-manifest.json; '+str(args.ab_report)
            if self.n==5:
                note='Canlı A/B sonrasında çalışan arayüzden yeniden alınan ekran. Project 1; 12 Kasım UTC; real istemci beyanı. Tüm model/senaryolar birlikte, genel model sıralaması değildir. Bilinmeyen maliyet sıfır sayılmaz. Doğrulanmış özet: '+json.dumps(observation,ensure_ascii=False)+'. Kaynak: live-overview-browser.txt, assets/live-overview-full.png.'
            if self.n==7:
                note+=' Bu koşuda iki modelde de toplam geçiş 3/6 → 5/6 oldu. Groq t03/t06 öncelik kurallarında; Gemini t03/t05 Markdown kullanmama kuralında iyileşti. Gemini kategori+öncelik eşleşmesi iki sürümde de 5/6: toplam geçiş artışı anlamsal doğruluk artışı değildir. İki model de t02 fatura indirme vakasında referans kategoriyle eşleşmedi. Bu vakada billing/bug tanımları örtüşebilir ve prompt öncelik sırası belirtmez; model kusuru sonucuna atlanmamalıdır. Referans ve puanlama sonuçtan sonra değiştirilmedi. Sonraki deneyde politika belirsizliği giderilip daha geniş, ayrı bir test setiyle yeniden ölçülmelidir.'
            if self.n==9:
                note+=' Yeni canlı A/B, demo_app.live_prompt_ab ile ayrı rapor üretir; web Experiments yazma sözleşmesi mock-only kalır. Altı vaka ve tek tekrar genel anlamsal kaliteyi kanıtlamaz.'
            if self.n==10:
                note=f"Güncel gözlem: {observation['attempts']} çağrı; {observation['errors']} teknik hata. Canlı A/B: 2 model × 6 vaka × 2 gerçek prompt = 24 çağrı. Bu 24 çağrı genel toplama zaten dahildir, yeniden eklenmez. Yol haritası: örneklemi ve tekrar sayısını artırma; canlı raporu güvenli UI deney akışına entegre etme; üretim güvenliği. İstatistiksel anlamlılık veya genel model üstünlüğü iddiası yok. Kaynak: {args.ab_report}; live-observation.json."
        self.s.notes_slide.notes_text_frame.text=note
        notes.append(f'## {self.n:02d} — {self.title}\n\n{note}')
        pdf.bookmarkPage('slide'+str(self.n));pdf.addOutlineEntry(self.title,'slide'+str(self.n),level=0)
        pdf.showPage()

# 01 — cover
s=Slide(1,'')
s.rect(0,66,500,435,B)
s.rect(35,98,49,5,O)
s.text(35,125,430,70,'LLM Insight',49,W,True)
s.text(37,211,420,95,'Her çağrıyı görünür,\nher kararı izlenebilir kıl.',29,W,True)
s.text(38,322,400,62,'LLM uygulamaları için gözlemlenebilirlik ve değerlendirme platformu',18,W)
s.rect(38,419,422,45,O,r=5)
s.text(52,431,395,23,'İzle  →  İncele  →  Değerlendir  →  İyileştir',16,B,True)
s.text(530,103,365,28,'SİSTEMİNİZ NE SÖYLÜYOR?',11,B,True)
s.img('live-overview-demo.png' if ab else 'overview-demo.png',520,144,410,310)
s.text(535,457,376,28,'Çalışan arayüzden gerçek ekran görüntüsü',10,MUTED,align='center')
s.finish('LLM Insight, LLM çağrılarının süre, token, hata ve maliyet bilgilerini görünür kılan yerel bir mühendislik MVP’sidir. Ana hikâye, gözlemden insan incelemesine ve kontrollü prompt deneylerine giden döngüdür. Ekran görüntüsü çalışan http://127.0.0.1:3001 arayüzünden 13 Kasım 2026 Türkiye saatiyle alınmıştır; içerik 12 Kasım UTC günündeki Project 1 verisidir. Sunumun beyaz/mavi/turuncu ana paleti yaklaşık 60/30/10 görsel ağırlıkla uygulanmıştır; özgün logolar ve arayüz görüntülerinin renkleri korunmuştur. Referanslar: PagePilot-Sunum.pdf (7 sayfa), EchoNote_Proje_Sunumu_Sena_Yetim.pdf (14 sayfa). İTO solda ve Softito sağda, EchoNote ile aynı yerleşimdedir.')

# 02 — problem / value
s=Slide(2,'01 / Problem ve değer önerisi','Bir LLM yanıtı, tek başına yeterli bilgi vermez.','Hedef kullanıcı: LLM geliştiricileri ve AI ürün ekipleri. İhtiyaç: karar verebilmek için ölçülebilir kanıt.')
cards=[('01','Nerede yavaşladı?','Uygulama süresi ile model çağrısının süresini ayırmak gerekir.'),('02','Gerçekte ne tüketti?','Token kullanımı, tekrar denemeler ve eksik maliyet bilgisi birlikte görülmelidir.'),('03','Değişiklik işe yaradı mı?','Bir prompt değişimini aynı test vakaları üzerinde değerlendirmek gerekir.')]
for x,(n,t,b) in zip([35,337,639],cards):
    s.rect(x,199,286,149,PALE,r=8);s.text(x+17,213,40,23,n,14,O,True)
    s.text(x+17,245,253,29,t,20,B,True);s.text(x+17,282,253,59,b,14.5,B)
s.rect(35,371,890,110,B,r=8)
for i,(t,b) in enumerate([('İZLE','Çağrı ve kullanım'),('İNCELE','Hata ve insan yorumu'),('DEĞERLENDİR','Onaylı test vakası'),('İYİLEŞTİR','Prompt A/B deneyi')]):
    x=53+i*220;s.text(x,389,200,24,t,15.5,W,True);s.text(x,423,190,27,b,13,W)
    if i<3:s.arrow(x+190,419,x+210,419)
s.source('Değer önerisi: tek bir hatayı, sonraki değişiklikleri sınayacak izlenebilir bir test vakasına dönüştürmek.')
s.finish('Ürünün hedefi metin üretmek değil, LLM kullanan uygulamaların davranışını açıklanabilir ölçümlerle görünür kılmaktır. Bir trace, bir isteğin adım adım kaydıdır; span ise bu kayıttaki tek bir işlemdir. Hataları incelemekten test oluşturmaya geçiş insan onayı gerektirir. Prompt A/B deneyleri mevcut sürümde sentetik mock ile yapılır. Kaynaklar: README.md / What it does; docs/PROJECT_PITCH.md; docs/ARCHITECTURE.md.')

# 03 — architecture
s=Slide(3,'02 / Mimari ve teknolojiler','Açık standartlarla kurulan, yerel bir mimari','Bağımsız Python SDK, çağrı metnini değil; süre, model, token ve durum bilgisini gözlem katmanına taşır.')
s.rect(35,217,890,214,PALE,LINE,r=9)
s.text(50,224,500,18,'YEREL GÖZLEMLEM VE DEĞERLENDİRME KATMANI',9.5,MUTED,True)
nodes=[(55,271,195,78,'Uygulama + SDK','Python · OpenTelemetry'),(370,271,215,78,'İşleme API’si','FastAPI · Python 3.12'),(700,271,205,78,'Veritabanı','PostgreSQL 17')]
for x,y,w,h,t,b in nodes:
    s.rect(x,y,w,h,B,r=7);s.text(x+13,y+12,w-26,26,t,18,W,True);s.text(x+13,y+44,w-26,22,b,11.5,W)
s.arrow(250,310,370,310);s.text(258,271,108,32,'OTLP / HTTP\nprotobuf',10.5,B,align='center')
s.arrow(585,310,700,310);s.text(590,272,104,30,'SQLAlchemy\n+ Alembic',10.5,B,align='center')
s.rect(55,173,195,36,W,LINE,r=5);s.text(66,181,173,23,'Harici LLM API’leri',13,B,True)
s.arrow(150,271,150,211);s.text(165,246,165,19,'İstek / yanıt',9.5,MUTED)
s.rect(370,370,535,45,W,LINE,r=6);s.text(386,382,505,26,'Dashboard  ·  Next.js 16 / React 19 / TypeScript',15,B,True)
s.arrow(478,370,478,350)
s.text(55,371,285,43,'Model çağrısı buluta gider;\ngözlem verisi yerelde tutulur.',13,B)
s.pill(35,451,263,'ARAYÜZ  ·  Tailwind CSS / Recharts')
s.pill(313,451,295,'ÇALIŞTIRMA  ·  Docker Compose')
s.pill(624,451,301,'KALİTE  ·  pytest / Playwright / CI')
s.source('Dashboard API’ye sunucu tarafında bağlanır. Yönetim anahtarı tarayıcıya aktarılmaz.')
s.finish('Mimari modüler monolittir: FastAPI tek süreç/worker; PostgreSQL 17; bağımsız Python SDK ve Next.js dashboard. SDK gerçek OTLP/HTTP binary protobuf protokolünü kullanır. Dashboard doğrudan veritabanına bağlanmaz; sunucu tarafında API’ye erişir. API telemetriyi doğrular, izin verilen metadata alanlarını alır, tekrarlanan aynı span’ı tekilleştirir ve sürümlü fiyat kataloğu varsa maliyeti hesaplar. Harici model sağlayıcısı uygulama girdisini alabilir; metadata-only ifadesi gözlem katmanının içerik toplamamasıyla sınırlıdır. SQLAlchemy veri erişimi, Alembic şema değişiklikleri için kullanılır. Kaynaklar: README.md / Architecture; backend/pyproject.toml; frontend/package.json; docs/TELEMETRY_CONTRACT.md.')

# 04 — models
s=Slide(4,'03 / Modeller ve kullanım alanları','Tek bir modele bağlı değil, ortak bir ölçüm dili','Amaç: farklı sağlayıcıların çağrılarını aynı süre, token, hata ve veri kaynağı alanlarıyla izlemek.')
rows=[('Groq','openai/gpt-oss-20b','23 çağrı','Yerel canlı gözlem'),('Groq','qwen/qwen3.8-27b','21 çağrı','Yerel canlı gözlem'),('Google Gemini','gemini-3.1-flash-lite','16 çağrı','13 hatasız / 3 hata')]
if ab:
    rows=[]
    for provider,model in [('Groq','openai/gpt-oss-20b'),('Groq','qwen/qwen3.8-27b'),('Google Gemini','gemini-3.1-flash-lite')]:
        data=observation['models'][model]
        rows.append((provider,model,f"{data['attempts']} çağrı",f"{data['attempts']-data['errors']} hatasız / {data['errors']} hata"))
s.rect(35,198,890,34,B,r=5)
for x,w,t in [(50,150,'SAĞLAYICI'),(209,350,'MODEL KİMLİĞİ'),(579,125,'GÖZLEM'),(716,195,'DURUM')]:s.text(x,207,w,20,t,10.5,W,True)
for i,(p,m,n,status) in enumerate(rows):
    y=232+i*49;s.rect(35,y,890,48,PALE if i%2==0 else W)
    for x,w,t,sz in [(50,150,p,14),(209,350,m,15),(579,125,n,15),(716,195,status,12.5)]:s.text(x,y+14,w,23,t,sz,B,bold=x==209)
s.text(36,387,890,24,'CANLI SENARYOLAR',10.5,B,True)
for x,w,t in [(35,280,'Destek talebi sınıflandırma'),(330,280,'JSON verisini yorumlama'),(625,300,'Kısa sürüm notu özetleme')]:s.pill(x,410,w,t)
s.text(35,449,890,36,('Canlı A/B: GPT-OSS ve Gemini, aynı 6 vaka × 2 prompt ile ayrıca sınandı.\nOpenRouter kilitli; web Experiments ekranındaki golden deneyler hâlâ sentetik mock.' if ab else 'OpenRouter / Gemma adaptörü kilitli. OpenAI API adaptörleri çevrimdışı sınandı;\nA/B deneylerindeki model adı, gerçek model çalıştırıldığı anlamına gelmez.'),12.5,MUTED)
s.source('Project 1 • 12 Kasım 2026 UTC • real filtresi • Sayılar farklı senaryoları kapsar; model başarı sıralaması değildir.')
s.finish('Bu tablo çalışan arayüzün Overview ve Models sayfalarından doğrulanmıştır. Gün aralığı 2026-11-12T00:00:00Z dahil, 2026-11-13T00:00:00Z hariç; Project 1; provenance=real. GPT-OSS: 22 ticket-triage + 1 JSON aggregate = 23; Qwen: 21 ticket-triage; Gemini: 14 ticket-triage (2 hata) + 1 JSON aggregate + 1 release-summary (hata) = 16. Hatasız span, anlamsal olarak doğru cevap demek değildir. Kaynak provenance istemcinin beyanıdır. GPT-OSS modeli Groq üzerinden çağrılmıştır; bu OpenAI API’sine canlı çağrı kanıtı değildir. Model isimleri depodaki tam kimliklerdir, güncel genel model tavsiyesi değildir. OpenRouter için google/gemma-4-26b-a4b-it:free adaptörü mevcut ancak yürütme kilitli. Canlı A/B platform deneyi yoktur. Kaynaklar: capture-manifest.json; overview-browser.txt; models-browser.txt; docs/reports/two-phase-integration.md; sdk/src/llm_insight_sdk/providers.py.')

# 05 — real dashboard
s=Slide(5,'04 / Çalışan arayüz — genel görünüm','Performans kadar, ölçümün sınırı da görünür.','Aynı ekranda çağrı sayısı, hata oranı, gecikme, token kullanımı ve maliyetin bilinip bilinmediği izlenir.')
s.img('live-overview-kpis.png' if ab else 'overview-kpis.png',35,180,890,306)
s.source((f"Canlı arayüz kırpımı • Project 1 / 12 Kasım UTC • {observation['attempts']} çağrı, {observation['errors']} hata • “Real” kaynağı istemci beyanıdır." if ab else 'Canlı arayüz kırpımı • Project 1 / 12 Kasım UTC • 60 çağrı, 3 hata • “Real” kaynağı istemci beyanıdır.'))
s.finish('Gerçek ekran görüntüsü, 13 Kasım 2026 Türkiye saatiyle açık yerel uygulamadan alınmıştır. Görüntüdeki seçili pencere 12 Kasım UTC günüdür. 60 trace ve 60 inference attempt, 3 hata (%5). Tüm modeller ve senaryolar birlikte olduğu için p50 614,6 ms ve p95 9,95 s bir model kıyası değildir. Bildirilen tokenlar 10.804 girdi ve 5.377 çıktı; 3 çağrı usage bildirmemiştir. 60 çağrının maliyeti de unknown: 57 çağrı için doğrulanmış fiyat yok, 3 çağrıda kullanım bilgisi yok. Bilinmeyen maliyet sıfır değildir. Free Tier kullanımı tek başına fatura kanıtı sayılmaz. Girdi/çıktı tokenları alt kümelerle tekrar toplanmaz. Kaynak: overview-browser.txt ve assets/overview-full.png; ilgili URL capture-manifest.json içinde. Görsellerde içerik değiştirilmemiş, yalnızca ilgi alanı kırpılmıştır.')

# 06 — diagnose and human review
s=Slide(6,'05 / Hata incelemesi ve insan onayı','Bir hata kaydı, izlenebilir bir öğrenme fırsatıdır.','Örnek: Gemini sürüm özeti çağrısında zaman aşımı. Süre ve hata görünür; kök neden henüz kanıtlanmış değildir.')
s.img('trace-waterfall.png',35,190,582,197)
s.img('trace-diagnostics.png',35,389,582,97)
s.rect(645,199,280,275,B,r=8)
items=[('1','Kaydı incele','Çağrı ağacı ve zaman çizelgesiyle geciken adımı bul.'),('2','Hipotezini kaydet','Kanıtı, olası açıklamayı ve insan kararını ayrı tut.'),('3','Onayla ve teste taşı','Sentetik girdiyi kişi ekler; onaysız test vakası oluşmaz.')]
for i,(num,t,b) in enumerate(items):
    y=216+i*84;s.text(662,y,24,25,num,18,O,True);s.text(693,y,212,25,t,15.5,W,True);s.text(693,y+28,211,48,b,12.2,W)
s.source('Gerçek trace: 6f7261b0… • Tek model denemesi • 504 / 29,78 sn • Bu kayıt henüz insan tarafından incelenmemiştir.')
s.finish('Ekrandaki hata 12 Kasım 2026 release-summary isteğine aittir. Provider DEADLINE_EXCEEDED yanıtı dönmüştür. CLIENT span 29.779 saniye, root span 29.794 saniyedir; token ve maliyet bilinmiyor. Zaman çizelgesi geciken adımı gösterir; kesin sağlayıcı kök nedeni çıkarılamaz. Automatic / Unverified gözlemleri metadata kuralları üretir, LLM üretmez. Sağdaki üç adım ürünün desteklediği iş akışıdır; bu gerçek trace üzerinde tamamlanmış bir insan incelemesi ya da test dönüşümü iddia edilmez. Telemetri prompt saklamadığı için test girdisi kullanıcı tarafından ayrıca sentetik olarak yazılır. Kaynaklar: trace-browser.txt; docs/reports/gemini-release-summary-verification.json; README.md / Demo walkthrough; docs/ARCHITECTURE.md / failure_reviews and test_cases.')

# 07 — evaluation
if ab:
    s=Slide(7,'06 / Gerçek modellerde prompt A/B','Aynı altı vaka, iki gerçek prompt, 24 canlı çağrı.','Çıktı biçimi, kategori ve öncelik; her iki sürümde de aynı sekiz kuralla kontrol edildi.')
    for x,label,body in [(35,'V1  /  KISA TALİMAT','Kategoriyi ve önceliği seç; istenen iki alanı JSON olarak döndür.'),(489,'V2  /  AÇIK İŞ KURALLARI','Kategori tanımları ve etkiye dayalı öncelik politikası eklendi; çıktı biçimi netleştirildi.')]:
        s.rect(x,193,436,91,PALE,r=7);s.text(x+16,205,404,20,label,11,B,True);s.text(x+16,235,404,43,body,14,B)
    s.rect(35,303,890,35,B,r=5)
    for x,w,t in [(50,310,'MODEL'),(380,155,'V1 GEÇEN VAKA'),(565,155,'V2 GEÇEN VAKA'),(753,150,'FARK (V2 − V1)')]:
        s.text(x,313,w,20,t,10.5,W,True)
    for i,(model,label) in enumerate([('openai/gpt-oss-20b','Groq · GPT-OSS 20B'),('gemini-3.1-flash-lite','Gemini · Flash Lite')]):
        a,b=[next(g for g in ab['groups'] if g['model']==model and g['variant']==v) for v in ('v1','v2')]
        y=338+i*48;s.rect(35,y,890,47,PALE if i==0 else W)
        delta=b['passed']-a['passed']
        for x,w,t,sz in [(50,310,label,17),(380,155,f"{a['passed']} / 6",21),(565,155,f"{b['passed']} / 6",21),(753,150,f"{delta:+d} vaka",17)]:
            s.text(x,y+10,w,31,t,sz,B,True)
    s.text(35,449,890,37,'Sonuç yalnızca bu altı vaka için geçerlidir. Tek tekrar; genel model üstünlüğü veya\nistatistiksel anlamlılık iddiası yok. Model yanıtları gerçek, test girdileri sentetiktir.',12.5,MUTED)
    s.source('Canlı A/B raporu + bağımsız trace doğrulaması • Web Experiments mock akışından ayrıdır • Maliyet bilinmiyor.')
    s.finish('İki model × altı vaka × iki gerçek prompt metni; 24 çağrı. V2 yalnızca farklı bir etiket değildir, modelin aldığı metin değişir. Referans etiketler sağlayıcıya gönderilmez. V2 kategori ve etki/öncelik iş kurallarını ekler; ölçülen müdahale budur. AB/BA sırası vakalar arasında dönüşümlüdür. Model başına her sürümde tek örneklem, altı vaka. Sekiz kural tüm sürümlere aynen uygulanmıştır; sonuç sonrası kural veya vaka seçimi yapılmamıştır. Teknik başarılı yanıt ile kural başarısı ayrıdır. Detaylar: '+json.dumps(ab['groups'],ensure_ascii=False)+'; vaka bağlantıları ve başarısız kurallar: '+str(args.ab_report)+'. Mevcut web Experiments ekranı mock kalır; bu sonuçlar ayrı kontrollü canlı CLI raporudur, Models/Traces telemetrisinde görünür.')
else:
    s=Slide(7,'06 / Prompt değerlendirme','Aynı test seti, iki prompt sürümü, açık kurallar.','12 sentetik vaka; JSON şeması, alan ve metin kurallarıyla kontrol edilir. Değerlendirici bir LLM değildir.')
    s.pill(35,192,238,'SENTETİK A/B  ·  MOCK',True)
    s.img('experiment-table.png',35,225,584,258)
    s.rect(647,191,278,288,PALE,r=8)
    s.text(664,208,244,27,'Deney #58',19,B,True)
    s.text(664,246,244,43,'A · prompt-v2\n4 / 10 geçti  ·  %40',16,B,True)
    s.text(664,307,244,43,'B · prompt-v1\n7 / 11 geçti  ·  %63,64',16,B,True)
    s.rect(662,374,248,83,B,r=6)
    s.text(676,386,219,56,'Kazanan ilan edilmez.\nBazı vakalar iki sürümde\nbirlikte değerlendirilemedi.',14,W,True)
    s.source('Çalışan Experiments ekranı • Başarı paydası = geçti + kaldı • Değerlendirilmeyen vakalar açıkça gösterilir.')
    s.finish('Project 1, Experiment #58, golden-synthetic-v1, 12 aynı vaka kimliği. Version A golden-prompt-v2: 4 passed, 6 failed, 2 not evaluated; 4/10=%40. Version B golden-prompt-v1: 7 passed, 4 failed, 1 not evaluated; 7/11=%63,64. 10 eşleşmiş değerlendirilebilir vaka, tasarım gereği iki tarafta da değerlendirilemeyen 1 vaka, yalnız bir sürümde değerlendirilen 1 vaka vardır. Bu yüzden backend daha iyi sürüm ilan etmez. Model adı gpt-4.1-mini yalnızca kimlik/fiyatlandırma içindir; OpenAI veya başka bir sağlayıcı çağrılmamıştır. Süreler ve tokenlar scripted mock değerleridir; gerçek model kalitesini kanıtlamaz. Kaynaklar: experiment-browser.txt; assets/experiment-full.png; backend/app/services/experiments/golden.py; backend/app/services/evaluation/.')

# 08 — differentiation
s=Slide(8,'07 / Diğer platformlardan farkı','Karar verirken belirsizliği gizlememek.','Tracing, veri setleri ve değerlendirme sektörde ortak yeteneklerdir. LLM Insight bunları dar ve denetlenebilir bir akışta birleştirir.')
s.text(35,199,405,25,'EKOSİSTEMİN GÜÇLÜ ODAKLARI',10.5,B,True)
platforms=[('Langfuse','Tracing, prompt yönetimi ve değerlendirme; geniş entegrasyon ekosistemi.'),('LangSmith','Üretim izleme; veri setleri, insan ve LLM değerlendirmesiyle test döngüsü.'),('Arize Phoenix','OpenTelemetry tabanlı tracing, değerlendirme ve deneyler; yerel kurulum.')]
for i,(t,b) in enumerate(platforms):
    y=230+i*73;s.text(35,y,383,25,t,18,B,True);s.text(35,y+29,393,40,b,12.5,MUTED)
s.rect(458,196,467,272,B,r=8)
s.text(478,214,427,27,'LLM INSIGHT’IN TASARIM TERCİHLERİ',11.5,W,True)
ds=[('İçeriksiz telemetri','Prompt ve yanıtı gözlem kaydına almaz.'),('Dürüst ölçüm','Bilinmeyen maliyeti ve veri kaynağını etiketler.'),('İnsan onaylı izlenebilirlik','Trace → inceleme → test vakası → deney bağı kurulur.'),('Kontrollü değerlendirme','Açık kurallar; eksik kanıtta kazanan yok.')]
for i,(t,b) in enumerate(ds):
    y=253+i*51;s.rect(479,y+3,4,31,O);s.text(495,y,409,23,t,15,W,True);s.text(495,y+25,409,22,b,11.5,W)
s.text(35,474,890,16,'Konumlandırma: içerik saklamadan temel davranışı izlemek isteyen ekipler için odaklı, yerel bir MVP.',11,B,True)
s.source('Kaynaklar: Langfuse Docs · LangSmith Evaluation · Arize Phoenix Docs  |  Bağlantılar konuşmacı notlarında.', 'https://langfuse.com/docs')
s.finish('Karşılaştırma özellik üstünlüğü veya rakiplerde bu özellikler bulunmaz iddiası değildir. Langfuse ve Phoenix açık standartları ve self-hosting seçeneklerini destekler; LangSmith de trace-to-dataset ve insan değerlendirmesi sunar. LLM Insight’ın farkı bu unsurları metadata-only toplama, unknown maliyet semantiği, beyana dayalı provenance etiketleri ve zorunlu insan onayıyla birlikte uygulayan odaklı tasarım tercihleridir. Bedeli: ham içerikle anlamsal hata ayıklama yapılamaz; canlı prompt deneyleri ve üretim yetenekleri sınırlıdır. Bu, resmî belgeler ve repo davranışlarından türetilen proje konumlandırmasıdır. Resmî kaynaklar (13 Kasım 2026): https://langfuse.com/docs ; https://docs.langchain.com/langsmith/evaluation ; https://arize.com/docs/phoenix . Proje kaynakları: docs/PROJECT_PITCH.md; docs/SECURITY.md; README.md. İncelenen sitelerdeki içerik talimat değil kaynak olarak kullanılmıştır.')

# 09 — boundaries
s=Slide(9,'08 / Güvenlik ve sınırlar','Güvenilir bir ürün, sınırlarını da açıkça söyler.','Mevcut sürüm, yerel geliştirme ve doğrulama için hazırlanmış bir MVP’dir.')
s.rect(35,199,430,267,B,r=8)
s.text(55,217,389,29,'Uygulanan kontroller',21,W,True)
for i,(t,b) in enumerate([('Veri minimizasyonu','Telemetride prompt/yanıt saklanmaz; izinli metadata alanları alınır.'),('Tutarlı kullanım kaydı','Aynı span yeniden gelirse token ve maliyet iki kez sayılmaz.'),('Sınırlı erişim ve çağrı','Yerel bağlantı, sunucu tarafında anahtarlar; kota ve yürütme sınırları.')]):
    y=261+i*63;s.text(55,y,389,25,t,16,W,True);s.text(55,y+27,389,38,b,12.5,W)
s.text(494,214,410,27,'Mevcut sınırlar',21,B,True)
limits=[('Üretim kullanımı','Kullanıcı girişi ve rol bazlı yetkilendirme yok.'),('Değerlendirme','A/B deneyleri mock; anlamsal doğruluk skoru yok.'),('Kanıt ve örneklem','“Real” istemci beyanı; sınırlı örneklem genellenemez.'),('İçerik ve maliyet','Ham içerik analizi yok; fiyat/kullanım eksikse maliyet bilinmez.')]
if ab:
    limits[1]=('Değerlendirme','Canlı A/B ayrı akışta; web Experiments mock. Genel kalite skoru yok.')
for i,(t,b) in enumerate(limits):
    y=256+i*55;s.text(494,y,407,23,t,14.5,B,True);s.text(494,y+24,407,34,b,12.5,MUTED)
s.source('Yerelde saklama, sağlayıcıya veri gitmediği anlamına gelmez: uygulamanın LLM isteği ayrı bir veri akışıdır.')
s.finish('Güvenlik kontrolleri risk azaltır, mutlak gizlilik garantisi değildir. Redaction sezgiseldir; test girdisi sentetik olmalıdır. Ingest anahtarı HMAC ile saklanır; yönetim tokenı frontend sunucusunda kalır. Tek worker ve süreç içi rate limit yatay ölçek için uygun değildir. Reviewer/approver kimlikleri self-declared; gerçek kullanıcı hesabı değildir. Public/multi-user kullanım için authentication, TLS, proje yetkilendirmesi, audit log, retention ve dağıtık sınırlar gibi çalışmalar gerekir. SDK fail-open davranışı ana uygulamanın gözlem servisi arızasından etkilenmemesini amaçlar, telemetri teslim garantisi sağlamaz. Gemini/Groq canlı çağrı gözlemi ile mock A/B deneyleri ayrıdır. Kaynaklar: docs/SECURITY.md; README.md / Known limitations; docs/ARCHITECTURE.md; docs/reports/two-phase-integration.md.')

# 10 — close / roadmap
s=Slide(10,'09 / Sonuç ve gelişim yolu','Gözlem verisini, daha bilinçli kararlara dönüştürmek.','LLM Insight; görünürlük, insan kararı ve tekrarlanabilir kontrol arasında izlenebilir bir bağ kurar.')
s.rect(35,197,890,103,B,r=8)
s.text(55,213,847,29,'BUGÜN ÇALIŞAN ÜRÜN',11,W,True)
for x,big,small in ([(55,'3 model','Yerel canlı gözlem'),(343,f"{observation['attempts']} çağrı",f"Seçili gün · {observation['errors']} hata"),(637,'24 çağrı','Canlı A/B · 6 vaka')] if ab else [(55,'3 model','Yerel canlı gözlem'),(343,'60 çağrı','Seçili gün · 3 hata'),(637,'12 vaka','Sentetik prompt deney seti')]):
    s.text(x,241,260,36,big,27,W,True);s.text(x+115 if x==55 else x+139,252,170,23,small,10.5,W)
s.text(35,321,890,26,'SONRAKİ ADIMLAR  /  ÖNERİLEN YOL HARİTASI',10.5,B,True)
roadmap=[('Daha güçlü kanıt','Dengeli örneklem ve senaryo kapsamı; kalite ile gecikmeyi ayrı ölçme.'),('Kontrollü canlı deney','Bütçe, kota ve açık izinlerle gerçek sağlayıcı A/B değerlendirmesi.'),('Üretime hazırlık','Kullanıcı/rol yönetimi, denetim kayıtları ve dağıtık çalışma.')]
if ab:
    roadmap[1]=('Canlı deney arayüzü','Kontrollü canlı A/B raporlarını web deney ekranına entegre etme.')
for i,(t,b) in enumerate(roadmap):
    x=35+i*302;s.rect(x,353,286,113,PALE,r=7);s.text(x+15,368,255,27,t,17,B,True);s.text(x+15,402,255,52,b,13,MUTED)
s.finish('Kapanıştaki 3 model ve 60 çağrı gerçek olarak etiketlenmiş yerel gözleme; 12 vaka ise sentetik deney setine aittir. Aynı kapsam değildir, birbirine eklenmez. Yol haritası öneridir, tamamlanmış özellik iddiası değildir. Canlı A/B, anlamlı kalite değerlendirmesi ve üretim güvenliği sonraki geliştirme alanlarıdır. Önerilen sunum süresi 7–9 dakika: problem 40 sn, mimari 60 sn, modeller 45 sn, dashboard 45 sn, hata incelemesi 60 sn, A/B 60 sn, farklılaşma 45 sn, sınırlar 40 sn ve kapanış 30 sn. Kaynaklar: bu sunumdaki yerel ekran kayıtları; README.md; docs/SECURITY.md; docs/reports/two-phase-integration.md.')

pdf.save();prs.save(pptx_path)
(P/'KONUSMACI-NOTLARI.md').write_text('# LLM Insight — konuşmacı notları ve kaynaklar\n\n'+'\n\n'.join(notes),encoding='utf-8')
(P/'layout-check.json').write_text(json.dumps(checks,ensure_ascii=False,indent=2),encoding='utf-8')
doc=pymupdf.open(pdf_path)
pre=P/'preview';pre.mkdir(exist_ok=True)
for i,p in enumerate(doc):p.get_pixmap(matrix=pymupdf.Matrix(1.5,1.5)).save(str(pre/f'slide-{i+1:02d}.png'))
contact=Image.new('RGB',(1440,5*420), '#D7E2EE')
for i,f in enumerate(sorted(pre.glob('slide-*.png'))):
    contact.paste(ImageOps.contain(Image.open(f),(704,396)),((i%2)*720+8,(i//2)*420+8))
contact.save(P/'LLM-Insight-Onizleme.png')
print('Built 10-page PDF and editable PPTX. Layout warnings:',len(checks))
for c in checks:print(c)
