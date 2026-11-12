import re, json
import pdfplumber
import numpy as np
from paddleocr import PaddleOCR
from tqdm import tqdm

# ocr = PaddleOCR(use_textline_orientation=True, lang='ch')  # 家电说明书多为中文
ocr = PaddleOCR(
    # 上mobile模型, 适用于印刷规整的
    text_detection_model_name='PP-OCRv5_mobile_det',
    text_recognition_model_name='PP-OCRv5_mobile_rec',
    use_doc_orientation_classify=False,   # 说明书扫描件方向正常，可关
    use_doc_unwarping=False,              # 页面平整，可关
    use_textline_orientation=False,       # 印刷体正放，可关
    lang='ch',
)

PDF_FILE = "家用冰箱通用说明书_N.30-2-31.pdf"

PAGE_NUM_PATTERNS = [r'^\s*-?\s*\d{1,4}\s*-?\s*$', r'^\s*第\s*\d+\s*页\s*$',
                     r'^\s*\d+\s*/\s*\d+\s*$', r'^\s*page\s+\d+.*$']

def is_garbled_native(text):
    """
        text:每一页文档
        判断内容是否原生乱码
    """
    if not text: return False
    n = len(text)
    bad = text.count('\ufffd') + len(re.findall(r'\(cid:\d+\)', text))
    weird = len(re.findall(r'[^\u4e00-\u9fff\u3000-\u303fa-zA-Z0-9\s，。：；、！？（）《》""'']', text))
    return bad / n > 0.10 or weird / n > 0.30

def is_page_num_only(text):
    t = text.strip()
    if not t: return False
    residue = t
    # 内容匹配页码正则
    for p in PAGE_NUM_PATTERNS:
        residue = re.sub(p, '', residue, flags=re.IGNORECASE)
    return len(residue.strip()) < 10

def ocr_page(page_img):
    if hasattr(page_img, 'convert'):
        page_img = np.array(page_img.convert('RGB'))
    results = list(ocr.predict(page_img))
    if not results:
        return '', 0.0
    res = results[0]
    texts  = res.get('rec_texts',  []) or []
    scores = res.get('rec_scores', []) or []
    conf = sum(scores) / len(scores) if scores else 0.0
    return ''.join(texts), conf

def inspect_page(page):
    issues = []
    native = page.extract_text() or ''

    if len(native) >= 30 and not is_garbled_native(native):
        text, conf = native, 1.0            # 原生层可信
        source = 'native'
    else:
        page_img = page.to_image(resolution=200).original  # 200dpi 扫描, 效果与300相当，但是耗时短
        text, conf = ocr_page(page_img)
        source = 'ocr'
        if conf < 0.50: issues.append('low_ocr_confidence')
        elif conf < 0.60: issues.append('suspect_ocr')

    # 对文本层有较多内容的处理
    eff_len = len(re.sub(r'\s', '', text))
    if eff_len < 5:                          issues.append('blank')
    elif is_page_num_only(text):             issues.append('page_num_only')
    elif source == 'native' and is_garbled_native(text): issues.append('garbled')
    elif eff_len < 30:                       issues.append('too_short')

    if not issues and source == 'ocr' and eff_len < 30:
        issues.append('image_only')          # 图示页，转 VLM 生成 caption
    return {'text': text, 'source': source, 'ocr_conf': round(conf, 3),
            'eff_len': eff_len, 'issues': issues}

report = []
with pdfplumber.open(PDF_FILE) as pdf:
    # for i, page in enumerate(pdf.pages, 1):
    pages = list(pdf.pages)
    for i, page in enumerate(tqdm(pages, desc="质检进度", unit='页'), 1):
        r = inspect_page(page); r['page'] = i
        r['page'] = i
        report.append(r)
        print(f"第 {i} 页: source={r['source']}, conf={r.get('ocr_conf')}, issues={r['issues']}")


with open('quality_report.json', 'w', encoding='utf-8') as f:
    json.dump(report, f, ensure_ascii=False, indent=2)
