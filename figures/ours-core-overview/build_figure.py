#!/usr/bin/env python3
"""Build native draw.io, then render its limited native-shape subset to SVG/PDF.

No raster reference is embedded. XML is the preview source of truth.
Usage: python build_figure.py [--export-only]
Export-only preserves edits made in draw.io within the supported shape subset.
For arbitrary new draw.io shapes, export with diagrams.net instead.
Dependencies for previews: cairosvg, pymupdf (see README).
"""
from pathlib import Path
import argparse
import json
import math
import xml.etree.ElementTree as E

OUT = Path(__file__).resolve().parent
W, H = 1200, 580
BLUE, INK, MUTED = '#216494', '#182D40', '#526577'
PALE, LINE, GRAY = '#E7F1F8', '#B4C8D8', '#EDF0F3'
ORANGE, OP = '#A85C16', '#F9E9D5'


def make_diagram():
    mx = E.Element('mxfile', {'host': 'app.diagrams.net', 'version': '24.7.17'})
    d = E.SubElement(mx, 'diagram', {'id': 'ours-core-overview', 'name': 'Figure 2'})
    model = E.SubElement(d, 'mxGraphModel', {'dx': str(W), 'dy': str(H),
        'grid': '1', 'gridSize': '10', 'guides': '1', 'tooltips': '1',
        'connect': '1', 'arrows': '1', 'fold': '1', 'page': '1',
        'pageScale': '1', 'pageWidth': str(W), 'pageHeight': str(H),
        'math': '0', 'shadow': '0', 'background': '#FFFFFF'})
    root = E.SubElement(model, 'root')
    E.SubElement(root, 'mxCell', {'id': '0'})
    E.SubElement(root, 'mxCell', {'id': '1', 'parent': '0', 'value': 'Background'})
    for name in ['a', 'b', 'c']:
        E.SubElement(root, 'mxCell', {'id': name, 'parent': '0', 'value': 'Panel '+name.upper()})
    layer = '1'
    counter = 0
    boxes = {}

    def shape(x, y, w, h, value='', fill='none', stroke='none', size=18,
              color=INK, bold=False, kind='rect', align='center', id=None):
        nonlocal counter
        counter += 1
        id = id or f'n{counter}'
        styles = {'shape': 'ellipse' if kind == 'ellipse' else 'rectangle',
            'rounded': '1' if kind == 'round' else '0', 'arcSize': '8',
            'whiteSpace': 'wrap', 'html': '0', 'fillColor': fill,
            'strokeColor': stroke, 'strokeWidth': '1.25',
            'fontFamily': 'Arial', 'fontSize': str(size), 'fontColor': color,
            'fontStyle': '1' if bold else '0', 'align': align,
            'verticalAlign': 'middle', 'spacing': '0', 'shadow': '0'}
        c = E.SubElement(root, 'mxCell', {'id': id, 'value': value,
            'style': ';'.join(f'{k}={v}' for k,v in styles.items())+';',
            'vertex': '1', 'parent': layer})
        E.SubElement(c, 'mxGeometry', {'x': str(x), 'y': str(y),
            'width': str(w), 'height': str(h), 'as': 'geometry'})
        boxes[id] = (x,y,w,h)
        return id

    def text(x,y,w,h,s,**kw):
        return shape(x,y,w,h,s,**kw)

    def edge(points, color=BLUE, width=1.7, arrow=True, dash=False,
             source=None, target=None, id=None):
        nonlocal counter
        counter += 1
        attrs = {'id': id or f'e{counter}', 'parent': layer, 'edge': '1'}
        style = f'edgeStyle=none;rounded=0;html=0;strokeColor={color};strokeWidth={width};endArrow={"block" if arrow else "none"};endFill=1;endSize=7;'
        if dash: style += 'dashed=1;dashPattern=4 3;'
        for key, cell, pt in [('source',source,points[0]),('target',target,points[-1])]:
            if cell:
                attrs[key] = cell
                x,y,w,h = boxes[cell]
                prefix = 'exit' if key == 'source' else 'entry'
                style += f'{prefix}X={(pt[0]-x)/w};{prefix}Y={(pt[1]-y)/h};{prefix}Dx=0;{prefix}Dy=0;{prefix}Perimeter=0;'
        attrs['style'] = style
        c = E.SubElement(root,'mxCell',attrs)
        g = E.SubElement(c,'mxGeometry',{'relative':'1','as':'geometry'})
        for kind, pt in [('sourcePoint',points[0]),('targetPoint',points[-1])]:
            E.SubElement(g,'mxPoint',{'x':str(pt[0]),'y':str(pt[1]),'as':kind})
        if len(points)>2:
            a = E.SubElement(g,'Array',{'as':'points'})
            for x,y in points[1:-1]: E.SubElement(a,'mxPoint',{'x':str(x),'y':str(y)})

    def bits(x,y,words,cell=15,height=30,gap=7,residual=False):
        ids=[]
        for j,word in enumerate(words):
            for i,b in enumerate(word):
                dark = i==0 and not residual
                ids.append(shape(x+j*(len(word)*cell+gap)+i*cell,y,cell,height,b,
                    fill=BLUE if dark else OP if residual else PALE,
                    stroke=ORANGE if residual else BLUE,
                    size=17,color='#FFFFFF' if dark else ORANGE if residual else BLUE))
        return ids

    # Background and panel boundaries.
    shape(0,0,W,H,fill='#FFFFFF')
    edge([(329,24),(329,554)],LINE,1,False)
    edge([(870,24),(870,554)],LINE,1,False)
    for x,letter,title,tw in [(20,'A','One code, two views',280),
                            (350,'B','Selective SSD access',490),
                            (891,'C','Precision by role',289)]:
        text(x,20,26,30,letter,size=23,bold=True,color=BLUE)
        text(x+34,20,tw-34,30,title,size=21,bold=True,align='left')

    # A: native bit cells; derivation is an offline relationship.
    layer='a'
    text(27,83,279,27,'Primary 4-bit',bold=True,size=20)
    primary=bits(29,125,['1010','0011','1110','0101'])
    shape(26,234,280,113,fill=PALE,stroke=LINE,kind='round')
    text(42,309,248,25,'DRAM routing + factors',bold=True,color=BLUE)
    for j in range(4):
        sx=36.5+j*67
        edge([(sx,155),(sx,173)],BLUE,1.3,False,source=primary[j*4])
    edge([(36.5,173),(237.5,173)],BLUE,1.3,False)
    edge([(166,173),(166,249)],BLUE,2.3,True)
    text(183,181,121,43,'Extract MSB\n1 bit / dim',size=17,color=BLUE,align='left')
    for j,b in enumerate('1010'):
        shape(54+j*56,259,38,32,b,BLUE,BLUE,20,'#FFFFFF')
    text(33,361,265,30,'Full primary stays on SSD',size=18,bold=True)
    shape(26,402,280,101,fill='#F6F8FA',stroke=LINE,kind='round')
    bits(33,430,['1010','0011','1110','0101'],cell=14,gap=8)
    text(40,471,251,23,'Complete 4-bit representation',size=16,color=MUTED)
    edge([(297,141),(316,141),(316,451),(306,451)],MUTED,1.4,True,True)
    text(27,524,280,27,'Shared representation, two views',size=17,color=MUTED)

    # B: region backgrounds precede all operations.
    layer='b'
    shape(344,77,515,299,fill='#F3F8FC',kind='round')
    shape(344,408,515,137,fill='#F3F5F7',kind='round')
    text(356,80,125,21,'DRAM / CPU',size=16,bold=True,color=BLUE,align='left')
    text(356,413,61,25,'SSD',size=18,bold=True,color=MUTED,align='left')
    edge([(344,390),(859,390)],LINE,1.2,False,True)
    query=shape(361,114,145,47,'Real-valued\nquery q','#FFFFFF',BLUE,18,BLUE,True,kind='round',id='real-query')
    side=shape(361,210,151,53,'Derived 1-bit\ncodes + factors',PALE,BLUE,18,BLUE,True,kind='round',id='routing-sidecar')
    text(529,85,190,25,'New graph neighbors',size=17,bold=True)
    neigh=[]
    for x,label in [(554,'v1'),(596,'v2'),(638,'v3'),(680,'v4')]:
        neigh.append(shape(x-15,126,30,30,label,'#FFFFFF',LINE,16,INK,kind='ellipse'))
    edge([(554,160),(554,178),(680,178),(680,160)],LINE,1.1,False)
    gate=shape(548,210,155,53,'Asymmetric\nscreening','#FFFFFF',BLUE,19,BLUE,True,kind='round',id='asymmetric-screening')
    edge([(619,178),(619,210)],BLUE,1.7,True,target=gate)
    edge([(506,137),(527,137),(527,223),(548,223)],BLUE,1.7,True,source=query,target=gate)
    edge([(512,249),(548,249)],BLUE,1.7,True,source=side,target=gate)
    frontier=shape(735,114,110,55,'Search\nfrontier','#FFFFFF',LINE,18,INK,True,kind='round',id='frontier')
    score=shape(735,219,110,58,'4-bit\nscoring','#FFFFFF',BLUE,19,BLUE,True,kind='round',id='primary-scoring')
    edge([(790,219),(790,169)],BLUE,1.9,True,source=score,target=frontier)
    text(731,184,52,22,'update',size=16,color=BLUE)
    edge([(735,137),(706,137)],BLUE,1.7,True,source=frontier)
    # Query scoring branch in an outer corridor; no crossing of neighbor edges.
    edge([(493,114),(493,67),(853,67),(853,248),(845,248)],BLUE,1.3,True,source=query,target=score)
    text(833,184,16,22,'q',size=17,color=BLUE)
    edge([(625,263),(625,286)],BLUE,1.7,False,source=gate)
    edge([(556,286),(676,286)],BLUE,1.3,False)
    survivors=[]
    for x,lab,keep in [(556,'v1',True),(596,'v2',False),(636,'v3',False),(676,'v4',True)]:
        n=shape(x-15,307,30,30,lab,BLUE if keep else GRAY,BLUE if keep else '#BAC4CE',16,'#FFFFFF' if keep else '#7C8995',kind='ellipse')
        edge([(x,286),(x,307)],BLUE if keep else '#9AA7B3',1.2,True,target=n)
        if not keep: edge([(x-12,310),(x+12,334)],'#8F9BA7',1.2,False)
        else: survivors.append(n)
    text(581,345,73,22,'pruned',size=16,color=MUTED)
    text(489,347,58,22,'fetch',size=16,color=BLUE)
    text(685,347,48,22,'fetch',size=16,color=BLUE)
    # A page contains multiple candidate records, not one page per node.
    page=shape(509,443,201,85,fill='#FFFFFF',stroke=LINE,kind='round',id='primary-page')
    text(514,449,191,21,'Primary 4-bit + metadata',size=16,bold=True,color=BLUE)
    for y,lab,word in [(481,'v1','1010'),(505,'v4','0101')]:
        text(523,y,27,19,lab,size=16,color=BLUE)
        bits(558,y,[word],cell=17,height=19)
        text(637,y,59,19,'...',size=17,color=MUTED)
    edge([(556,337),(556,443)],BLUE,2.1,True,source=survivors[0],target=page)
    edge([(676,337),(676,443)],BLUE,2.1,True,source=survivors[1],target=page)
    edge([(710,492),(790,492),(790,277)],BLUE,2,True,source=page,target=score)
    text(716,431,70,42,'primary\npayload',size=16,color=BLUE)
    shape(358,450,130,77,'Graph\nadjacency','#FFFFFF',LINE,18,MUTED,kind='round')
    text(370,296,138,53,'Promising\ncandidates get\nfine payloads',size=17,color=MUTED)

    # C: aligned native records convey roles, not an end-to-end flowchart.
    layer='c'
    text(893,86,284,30,'1-bit  /  screening',size=20,bold=True,color=BLUE,align='left')
    for j,b in enumerate('1010'):
        shape(909+j*48,134,33,31,b,BLUE,BLUE,19,'#FFFFFF')
    text(1106,134,46,31,'...',size=21,color=BLUE)
    text(894,180,282,27,'Coarse candidate assessment',size=17,color=MUTED,align='left')
    edge([(893,222),(1181,222)],LINE,1,False)
    text(893,246,286,30,'Primary 4-bit  /  navigation',size=19,bold=True,color=BLUE,align='left')
    bits(897,294,['1010','0011','1110','0101'],cell=15,gap=8)
    text(894,337,282,27,'Refine the search frontier',size=17,color=MUTED,align='left')
    edge([(893,380),(1181,380)],LINE,1,False)
    text(893,396,286,28,'Final shortlist reranking',size=20,bold=True,align='left')
    text(895,438,132,23,'Primary 4-bit',size=17,color=BLUE)
    text(1041,438,136,23,'Residual 4-bit',size=17,color=ORANGE)
    pb=bits(914,467,['1010'],cell=22,height=27)
    rb=bits(1065,467,['0110'],cell=22,height=27,residual=True)
    text(1020,468,30,24,'+',size=23,color=MUTED)
    rerank=shape(967,526,198,32,'Refined ranking','#FFFFFF',ORANGE,18,ORANGE,True,kind='round',id='residual-reranking')
    edge([(958,494),(958,508),(1065,508),(1065,526)],BLUE,1.5,True,target=rerank)
    edge([(1109,494),(1109,508),(1065,508)],ORANGE,1.5,False)
    text(898,530,24,24,'q',size=20,color=BLUE)
    edge([(928,542),(967,542)],BLUE,1.5,True,target=rerank)
    E.indent(mx)
    path=OUT/'ours-core-overview.drawio'
    E.ElementTree(mx).write(path,encoding='utf-8',xml_declaration=True)
    return path


def render(path):
    import cairosvg
    import pymupdf as fitz
    tree=E.parse(path)
    cells={c.get('id'):c for c in tree.findall('.//mxCell')}
    assert '0' in cells and '1' in cells
    assert len(cells)==len(tree.findall('.//mxCell')), 'Duplicate IDs'
    ns='http://www.w3.org/2000/svg'
    E.register_namespace('',ns)
    def sub(parent,tag,attrs): return E.SubElement(parent,'{'+ns+'}'+tag,{k:str(v) for k,v in attrs.items()})
    svg=E.Element('{'+ns+'}svg',{'width':'178mm','height':f'{178*H/W:.5f}mm','viewBox':f'0 0 {W} {H}','version':'1.1'})
    sub(svg,'title',{}).text='Primary-derived routing for selective SSD access'
    sub(svg,'desc',{}).text='Editable vector export rendered directly from native draw.io shapes. No raster images.'
    boxes={}
    errors=[]
    for cid,c in cells.items():
        if c.get('vertex')=='1':
            g=c.find('mxGeometry')
            boxes[cid]=tuple(float(g.get(k,'0')) for k in ['x','y','width','height'])
    styles={cid:dict(p.split('=',1) for p in c.get('style','').split(';') if '=' in p) for cid,c in cells.items()}
    for cid,c in cells.items():
        s=styles[cid]
        group=sub(svg,'g',{'id':cid})
        if c.get('vertex')=='1':
            x,y,w,h=boxes[cid]
            if x<0 or y<0 or x+w>W or y+h>H: errors.append('out of canvas: '+cid)
            kind=s.get('shape')
            if kind not in ['rectangle','ellipse']: raise ValueError('Unsupported native shape: '+str(kind))
            props={'fill':s['fillColor'],'stroke':s['strokeColor'],'stroke-width':s['strokeWidth']}
            if kind=='ellipse': sub(group,'ellipse',dict(cx=x+w/2,cy=y+h/2,rx=w/2,ry=h/2,**props))
            else:
                radius=min(w,h)*float(s.get('arcSize','0'))/100 if s.get('rounded')=='1' else 0
                sub(group,'rect',dict(x=x,y=y,width=w,height=h,rx=radius,**props))
            value=c.get('value','')
            if value:
                fs=float(s['fontSize']); lines=value.split('\n'); leading=1.17*fs
                font=fitz.Font('hebo' if s.get('fontStyle')=='1' else 'helv')
                align=s.get('align','center')
                tx=x if align=='left' else x+w/2
                for i,line in enumerate(lines):
                    tw=font.text_length(line,fontsize=fs)
                    if tw>w+1: errors.append(f'text exceeds cell {cid}: {line} ({tw:.1f}>{w})')
                    t=sub(group,'text',{'x':tx,'y':y+h/2-(len(lines)-1)*leading/2+i*leading+fs*.35,
                        'font-family':'Arial, Liberation Sans, sans-serif','font-size':fs,
                        'font-weight':'bold' if s.get('fontStyle')=='1' else 'normal',
                        'fill':s['fontColor'],'text-anchor':'start' if align=='left' else 'middle'})
                    t.text=line
        elif c.get('edge')=='1':
            g=c.find('mxGeometry'); assert g is not None
            def endpoint(which):
                ref=c.get(which)
                if ref:
                    assert ref in boxes
                    x,y,w,h=boxes[ref]; prefix='exit' if which=='source' else 'entry'
                    return (x+w*float(s.get(prefix+'X','.5')),y+h*float(s.get(prefix+'Y','.5')))
                p=g.find(f"mxPoint[@as='{which}Point']")
                return float(p.get('x')),float(p.get('y'))
            pts=[endpoint('source')]+[(float(p.get('x')),float(p.get('y'))) for p in g.findall('Array/mxPoint')]+[endpoint('target')]
            color=s['strokeColor']; width=float(s['strokeWidth'])
            attrs={'fill':'none','stroke':color,'stroke-width':width,'stroke-linejoin':'round','stroke-linecap':'round'}
            if s.get('dashed')=='1': attrs['stroke-dasharray']='5 4'
            drawpts=list(pts)
            if s.get('endArrow')!='none':
                end=pts[-1]; prev=pts[-2]; dist=math.hypot(end[0]-prev[0],end[1]-prev[1]); assert dist>0
                ux,uy=(end[0]-prev[0])/dist,(end[1]-prev[1])/dist
                size=6+width; bx,by=end[0]-size*ux,end[1]-size*uy
                drawpts[-1]=(bx+ux,by+uy)
                sub(group,'polygon',{'points':f'{end[0]},{end[1]} {bx-uy*size*.42},{by+ux*size*.42} {bx+uy*size*.42},{by-ux*size*.42}','fill':color})
            sub(group,'polyline',dict(points=' '.join(f'{x},{y}' for x,y in drawpts),**attrs))
    assert not errors, '\n'.join(errors)
    svg_path=OUT/'ours-core-overview.svg'
    E.ElementTree(svg).write(svg_path,encoding='utf-8',xml_declaration=True)
    pdf_path=OUT/'ours-core-overview.pdf'
    cairosvg.svg2pdf(url=str(svg_path),write_to=str(pdf_path))
    pdf=fitz.open(pdf_path); page=pdf[0]
    page.get_pixmap(matrix=fitz.Matrix(3,3),alpha=False).save(OUT/'ours-core-overview.png')
    extracted=page.get_text()
    for label in ['Extract MSB','Asymmetric','Residual 4-bit','Real-valued','Final shortlist']:
        assert label in extracted, 'Missing PDF text: '+label
    assert not page.get_images(), 'PDF unexpectedly contains images'
    (OUT/'pdf-text.txt').write_text(extracted)
    report={'drawio_cells':len(cells),'native_vertices':len(boxes),
        'native_edges':sum(c.get('edge')=='1' for c in cells.values()),
        'svg_text_nodes':len(svg.findall('.//{'+ns+'}text')),
        'pdf_pages':len(pdf),'pdf_images':len(page.get_images()),
        'pdf_vector_paths':len(page.get_drawings()),'pdf_fonts':page.get_fonts(),
        'width_mm':178,'height_mm':178*H/W,'min_font_pt':16*178/25.4*72/W,
        'renderer':'native draw.io XML -> subset SVG renderer -> CairoSVG PDF',
        'drawio_desktop_export':False,'xml_and_text_checks':'pass',
        'visual_review':'pending','scientific_scope':'resident-routing, illustrative candidates'}
    (OUT/'qa.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--export-only',action='store_true')
    args=parser.parse_args()
    render(OUT/'ours-core-overview.drawio' if args.export_only else make_diagram())
