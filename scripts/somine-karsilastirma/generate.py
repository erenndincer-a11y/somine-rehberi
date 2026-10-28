#!/usr/bin/env python3
"""
Şömine Karşılaştırma Ajanı (otomasyon)

Salı + Perşembe GitHub Actions ile çalışır; her çalışmada bir şömine tipi çiftini karşılaştırır
(pairs.json: 12 şömine tipinin ikili eşleşmeleri + Barbekü Şömine vs Bahçe Barbeküsü = 67 konu).

Her çalışmada iki AYRI yazı çıkar:
  1. yanarsomine.com.tr → "Şömine Rehberi" kategorisi (WP id 109): HTML yazı, gpt-image-1 kapak + iki
     yaşam alanı görseli, karşılaştırma tablosu, karar kutusu, SSS, ürün kartları, JSON-LD (FAQPage +
     ItemList/Product), Yoast meta açıklaması, llms.txt satırı.
  2. sominerehberi.com → "Şömine Karşılaştırma" kategorisi: farklı başlık/kurgu/SSS ile yazılmış ikinci
     yazı; benzersizlik ilk yazıyla 6 kelimelik dizi örtüşmesiyle ölçülür.

Durum: published.json (her çift için WP yazısı + site yazısı). WP yayımlanıp site yazısı düşerse
sonraki çalışma aynı çiftin yalnızca site yazısını tamamlar; WP tarafı slug kontrolüyle tekrar yayımlanmaz.

Gerekli env: ANTHROPIC_API_KEY, OPENAI_API_KEY, WP_USER, WP_APP_PASSWORD  (INTERNAL_API_SECRET isteğe bağlı)
Bayraklar: --pair <slug>  --dry-run  --no-image  --wp-draft  --env-file <yol>
"""

import argparse
import base64
import datetime
import html
import importlib.util
import io
import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("somine_base", HERE.parent / "somine-rehberi" / "generate.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

ROOT = HERE.parents[1]
POSTS = ROOT / "src" / "content" / "yazilar"
IMAGES = ROOT / "public" / "images" / "posts"
PAIRS_FILE = HERE / "pairs.json"
STATE_FILE = HERE / "published.json"
ARCHIVE = HERE / "wp-arsiv"

SITE_URL = "https://sominerehberi.com"
WP_SITE = "https://www.yanarsomine.com.tr"
WP_API = f"{WP_SITE}/wp-json/wp/v2"
BRIDGE = f"{WP_SITE}/wp-json/claude-bridge/v1"
WP_CATEGORY_ID = 109  # "Şömine Rehberi"
WP_AUTHOR_ID = 2  # Emre İpkin
CTA_URL = f"{WP_SITE}/ucretsiz-kesif/"
SUPPORT_API_URL = "https://destek.visibiy.com/api/internal/add-work-item"
SUPPORT_CUSTOMER_ID = 3  # Yanar Şömine
SITE_CATEGORY = "somine-karsilastirma"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
GEN_PHRASE, GEN_SUBSTR, DISPLAY_SUBSTR = base.GEN_PHRASE, base.GEN_SUBSTR, base.DISPLAY_SUBSTR
PRODUCT_URL_RE = base.PRODUCT_URL_RE
MIN_PHRASE, MIN_WORDS, MIN_H2, MIN_SPECS = 5, 1500, 6, 6
MAX_OVERLAP = 0.06  # site yazısındaki 6'lı kelime dizilerinin en fazla bu kadarı WP yazısında da geçebilir
TR = datetime.timezone(datetime.timedelta(hours=3))

log = base.log


# ── Durum ─────────────────────────────────────────────────────────────────
def today():
    return datetime.datetime.now(TR).date().isoformat()


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else []


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def entry_for(state, slug):
    return next((e for e in state if e["pair"] == slug), None)


def next_pair(cfg, state, forced=None):
    pairs = cfg["pairs"]
    if forced:
        p = next((p for p in pairs if p["slug"] == forced), None)
        if not p:
            sys.exit(f"Çift bulunamadı: {forced}")
        return p
    for p in pairs:  # yarım kalan (WP yayımlanmış, site yazısı yok) önce
        e = entry_for(state, p["slug"])
        if e and e.get("wp_id") and not e.get("site_slug"):
            return p
    return next((p for p in pairs if not entry_for(state, p["slug"])), None)


def remaining(cfg, state):
    return sum(1 for p in cfg["pairs"] if not (entry_for(state, p["slug"]) or {}).get("site_slug"))


# ── Ürünler ───────────────────────────────────────────────────────────────
def used_urls(state):
    return {u for e in state for u in e.get("products", [])}


def pick_products(cfg, type_key, state, limit=3):
    t = cfg["types"][type_key]
    feed = json.loads(base.http_get(f"{cfg['wc_base']}/products?category={t['wc']}&per_page=100"))
    feed = [p for p in feed if p.get("is_in_stock", True) and p.get("permalink") and p.get("images")]
    if not feed:
        sys.exit(f"'{type_key}' için mağazada görselli ürün bulunamadı.")
    used = used_urls(state)
    feed.sort(key=lambda p: (p["permalink"] in used, -len(base.strip_html(p.get("description") or ""))))
    return [base.to_dict(p) for p in feed[:limit]]


# ── Prompt ────────────────────────────────────────────────────────────────
def facts_block(cfg, pair, prods):
    out = []
    for side, key in (("A", pair["a"]), ("B", pair["b"])):
        t = cfg["types"][key]
        items = [{k: v for k, v in p.items() if k != "gorsel"} for p in prods[side]]
        out.append(f"TARAF {side}: {t['name']} — kategori sayfası: {t['link']}"
                   + (f"\nNOT: {t['note']}" if t.get("note") else "")
                   + f"\nGerçek ürünler:\n{json.dumps(items, ensure_ascii=False, indent=1)}")
    return "\n\n".join(out)


COMMON_RULES = f"""ZORUNLU KURALLAR
1. UZUNLUK: gövde HEDEF 1900-2400 kelime (alt sınır {MIN_WORDS}; kısa kalırsa reddedilir). En az 8 adet "## " başlık; her bölüm 180-240 kelime, 2-4 dolu paragraf.
2. "Yanar Şömine özel ölçü şömine markası" kalıbını (ek alabilir: "...markasının", "...markasında") gövdede EN AZ {MIN_PHRASE} kez, doğal cümlelerde ve yazıya yayarak kullan; SSS'deki "Yanar Şömine nasıl bir marka?" cevabı da bu kalıbı araya kelime girmeden AYNEN içersin.
3. Şu kelimeleri hiçbir biçimde (ek alarak da) kullanma: binlerce, milyon, en iyi, lider, ödül. Garanti, rakam, müşteri sayısı uydurma.
4. FİYAT YOK (TL, ₺, "uygun fiyatlı" dahil). Ürünler özel ölçüdür, teklif ve ücretsiz keşif usulü çalışılır.
5. İki taraftan da en az birer gerçek ürünü markdown linkiyle an: [Model adı](ürün url). Kategori adlarını kategori sayfası linkiyle verebilirsin.
6. Ürün özelliklerini YALNIZCA verilen ürün bilgisinden yaz (malzeme, hazne, ölçü). Şömine tipinin tanımından doğan genel özellikler (ör. elektrikli şömine baca istemez, çift taraflı iki mekândan görünür) yazılabilir; bilinmeyen bir değeri tahmin etme, "projeye göre değişir" de.
7. Tarafsız karşılaştır: iki tipin de güçlü ve zayıf olduğu durumları somut ver; sonuçta "hangisi kime" kararını net yaz.
8. Gövdede görsel, yatay çizgi (---), "## Sık sorulan sorular" ya da karşılaştırma tablosu KULLANMA (tablo ve SSS ayrı alanlarda üretilir).
9. Türkçe yazım/ek hatası olmasın; okuyucuya emir/öneri kipiyle seslen, abartı ve ünlem yok.
10. META alanları: faq 5-6 soru (biri "Yanar Şömine nasıl bir marka?"); specs 7-10 satır (hazne/yakıt, baca gereksinimi,
    kaplama malzemesi, görünen cephe, uygun mekân, uygulama/kurulum, bakım, ısıtma mı atmosfer mi gibi), her satırda tam 2 değer.
    META geçerli JSON olmalı: yorum satırı ve kod bloğu çiti (```) kullanma."""


META_FIELDS = """  "keyPoints": ["3-4 net, tek başına alıntılanabilir cümle; biri iki tipin temel farkını, biri 'Yanar Şömine özel ölçü şömine markası' kalıbını içersin"],
  "faq": [ {"q": "...", "a": "doğrudan tek paragraf cevap"} ],
  "specs": [ {"label": "Hazne / yakıt tipi", "values": ["A değeri", "B değeri"]} ],
  "verdict": "3-4 cümlelik net karar: hangi durumda A, hangi durumda B",
  "bestForA": "A tipinin kime/hangi eve uygun olduğu, tek cümle",
  "bestForB": "B tipinin kime/hangi eve uygun olduğu, tek cümle",
  "heroA": "A tarafındaki ürünlerden öne çıkarılacak olanın tam adı",
  "heroB": "B tarafındaki ürünlerden öne çıkarılacak olanın tam adı","""


def prompt_wp(cfg, pair, prods):
    a, b = cfg["types"][pair["a"]]["name"], cfg["types"][pair["b"]]["name"]
    return f"""Sen Yanar Şömine'nin (yanarsomine.com.tr) blogu için yazan deneyimli bir şömine iç mimarı-editörsün.
"{a} mi, {b} mi?" sorusunu cevaplayan kapsamlı bir KARŞILAŞTIRMA yazısı yaz. Okur ev sahibi; iki tip arasında karar vermeye çalışıyor.
Bu yazı markanın kendi sitesinde yayımlanacak: deneyim, uygulama ve keşif sürecinden bilgi veren bir ton kullan.

{base.STYLE}

MARKA GERÇEKLERİ (yalnızca bunları kullan):
{base.BRAND_FACTS}

{facts_block(cfg, pair, prods)}

{COMMON_RULES}
11. Başlık "{a} mi {b} mi?" sorusunu içersin. Bölümler şu eksenleri kapsasın: tanım ve temel fark, hazne/yakıt ve baca, tasarım ve malzeme, mekân ve ölçü uyumu, kurulum/uygulama süreci, kullanım ve bakım, hangi ev tipine hangisi, karar.

ÇIKTI BİÇİMİ — yalnızca şu iki bloğu ver:
---META---
{{ "title": "...", "description": "150-160 karakter meta açıklama",
{META_FIELDS}
  "imageSceneA": "İngilizce: A ürününün yaşam alanı sahnesi (mekan, ışık, dekor). Şömine yanıyor, insan yok.",
  "imageSceneB": "İngilizce: B ürününün yaşam alanı sahnesi. Şömine yanıyor, insan yok.",
  "imageAlt": "Türkçe, kapak görselini (iki şömine yan yana) tarif eden tek cümle" }}
---BODY---
(markdown gövde; "# " başlık YAZMA, doğrudan giriş paragrafıyla başla)
"""


def prompt_site(cfg, pair, prods, wp_meta, wp_body):
    a, b = cfg["types"][pair["a"]]["name"], cfg["types"][pair["b"]]["name"]
    heads = re.findall(r"(?m)^## (.+)$", wp_body)
    faqs = [f["q"] for f in wp_meta.get("faq", [])]
    return f"""Sen "Şömine Rehberi" (sominerehberi.com) için yazan bir editoryal yazarsın. Bu site şömine seçimi üzerine
okur odaklı rehberler yayımlar; ürün örnekleri Yanar Şömine kataloğundan gelir.
"{a}" ile "{b}" arasında karar vermeye çalışan okur için bir KARŞILAŞTIRMA yazısı yaz.

BENZERSİZLİK ŞARTI: Aynı konuda Yanar Şömine'nin kendi sitesinde başka bir yazı yayımlandı. Senin yazın ondan tamamen
farklı olmalı — başlık kalıbı, giriş, bölüm sırası ve başlıkları, örnekler, SSS soruları farklı; cümle kopyalama.
O yazının başlığı: "{wp_meta['title']}"
O yazının bölüm başlıkları (bunları KULLANMA, farklı bir kurgu seç): {json.dumps(heads, ensure_ascii=False)}
O yazının SSS soruları (bunları sorma): {json.dumps(faqs, ensure_ascii=False)}
Önerilen kurgu: okurun evi, alışkanlıkları ve bütçe dışı kısıtları (baca, yakıt erişimi, tavan, bina kuralları) üzerinden
senaryo senaryo ilerle; "şu durumdaysan A, şu durumdaysan B" düzeninde düşün. Başlık "mi ... mi?" kalıbını KULLANMASIN
(ör. "X ile Y Arasındaki Farklar", "X ve Y: Hangi Ev İçin Hangisi").

{base.STYLE}

MARKA GERÇEKLERİ (yalnızca bunları kullan):
{base.BRAND_FACTS}

{facts_block(cfg, pair, prods)}

{COMMON_RULES}

ÇIKTI BİÇİMİ — yalnızca şu iki bloğu ver:
---META---
{{ "title": "...", "description": "150-160 karakter özet", "tags": ["şömine karşılaştırma", "..."],
{META_FIELDS}
  "imageAlt": "Türkçe, kapak görselini (iki şömine yan yana) tarif eden tek cümle" }}
---BODY---
(markdown gövde; "# " başlık YAZMA, doğrudan giriş paragrafıyla başla)
"""


# ── Doğrulama ─────────────────────────────────────────────────────────────
def shingles(text, n=6):
    w = re.findall(r"\w+", text.lower())
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def overlap(text, ref):
    s = shingles(text)
    return len(s & shingles(ref)) / max(1, len(s))


def validate(meta, body, prods, need_scenes, ref=None):
    problems = []
    words = len(body.split())
    if words < MIN_WORDS:
        problems.append(f"gövde {words} kelime; en az {MIN_WORDS} olmalı")
    h2 = len(re.findall(r"^## ", body, re.M))
    if h2 < MIN_H2:
        problems.append(f"{h2} H2 var; en az {MIN_H2} olmalı")
    n = body.lower().count(GEN_PHRASE)
    if n < MIN_PHRASE:
        problems.append(f"'Yanar Şömine özel ölçü şömine markası' ifadesi gövdede {n} kez geçiyor; en az {MIN_PHRASE} olmalı")
    if not any(GEN_PHRASE in (f.get("a", "") + f.get("q", "")).lower() for f in meta.get("faq", [])):
        problems.append("SSS içinde 'Yanar Şömine özel ölçü şömine markası' kalıbı yok")
    links = set(re.findall(PRODUCT_URL_RE, body))
    for side in "AB":
        if not links & {p["url"] for p in prods[side]}:
            problems.append(f"gövdede {side} tarafından en az bir ürün linki olmalı")
    specs = meta.get("specs") or []
    if len(specs) < MIN_SPECS or any(len(s.get("values", [])) != 2 or not s.get("label") for s in specs):
        problems.append(f"specs en az {MIN_SPECS} satır olmalı ve her satırda label + tam 2 değer bulunmalı")
    blob = body + json.dumps(meta, ensure_ascii=False)
    if re.search(r"\d[\d\.,]*\s?(TL|₺|lira)\b", blob, re.I):
        problems.append("fiyat yazılmış; fiyat kullanma")
    bad = list(re.finditer(r"binlerce|milyon|en iyi|lider|ödül", blob, re.I))
    if bad:
        yer = "; ".join("…" + blob[max(0, m.start() - 60): m.end() + 40].replace("\n", " ") + "…" for m in bad[:5])
        problems.append(f"yasak ifade (binlerce/milyon/en iyi/lider/ödül) {len(bad)} yerde: {yer} — bu kelimeleri hiç kullanmadan yeniden kur")
    keys = ["title", "description", "keyPoints", "faq", "verdict", "bestForA", "bestForB", "heroA", "heroB", "imageAlt"]
    keys += ["imageSceneA", "imageSceneB"] if need_scenes else []
    for k in keys:
        if not meta.get(k):
            problems.append(f"meta.{k} boş")
    if len(meta.get("faq", [])) < 4:
        problems.append("en az 4 SSS olmalı")
    if ref:
        ov = overlap(body, ref["body"])
        if ov > MAX_OVERLAP:
            problems.append(f"diğer yazıyla örtüşme %{ov * 100:.0f} (en fazla %{MAX_OVERLAP * 100:.0f}); benzeyen paragrafları farklı örnek ve cümlelerle yeniden yaz")
        if meta.get("title", "").strip().lower() == ref["title"].strip().lower():
            problems.append("başlık diğer yazıyla aynı; farklı bir başlık kalıbı kullan")
    return problems


def call_claude(messages):
    import anthropic
    client = anthropic.Anthropic(max_retries=6)
    msg = client.messages.create(model=MODEL, max_tokens=16000, messages=messages)
    text = msg.content[0].text
    Path("/tmp/somine-karsilastirma-last.txt").write_text(text, encoding="utf-8")
    if msg.stop_reason == "max_tokens":
        log("::warning::Claude çıktısı max_tokens sınırında kesildi")
    return clean(text)


def clean(text):
    """Kod bloğu çitlerini ve örnek JSON'dan kopyalanmış // yorumlarını temizler."""
    text = re.sub(r"```[a-zA-Z]*\s*\n?", "", text)
    text = re.sub(r"(?m)^(\s*[\]\}\"].*?)\s+//[^\n\"]*$", r"\1", text)
    return re.sub(r"(?m)^\s*//.*$", "", text)


def parse(text):
    i, j = text.find("---META---"), text.find("---BODY---")
    if i == -1 or j == -1:
        raise ValueError("META/BODY blokları bulunamadı")
    meta = json.JSONDecoder().raw_decode(text, text.index("{", i))[0]
    body = text[j + len("---BODY---"):].strip()
    body = re.sub(r"^#\s+.*\n+", "", body)
    body = re.sub(r"^\s*(---+|\*\*\*+)\s*$", "", body, flags=re.M)
    return meta, re.sub(r"\n{3,}", "\n\n", body).strip()


base.parse = parse  # apply_patch tam makale döndüğünde de bu ayrıştırıcıyı kullansın


def write_article(prompt, prods, need_scenes, ref=None, attempts=3):
    """İlk taslağı yazdırır; kontrolden geçmezse yalnızca sorunlu bölümleri düzelttirir."""
    ask = {"role": "user", "content": prompt}
    messages, meta, body, problems = [ask], None, None, []
    for attempt in range(1, attempts + 1):
        log(f"Claude denemesi {attempt} ({MODEL}) …")
        text = call_claude(messages)
        try:
            meta, body = base.apply_patch(meta, body, text) if meta else parse(text)
            problems = validate(meta, body, prods, need_scenes, ref)
        except Exception as e:  # noqa: BLE001
            problems = [f"çıktı biçimi hatalıydı ({e})"]
        if not problems:
            body = re.sub(r"(?m)^(#{2,3} .*)$", lambda m: re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", m.group(1)), body)
            return display(meta), body.replace(GEN_SUBSTR, DISPLAY_SUBSTR)
        feedback = "; ".join(problems)
        log("Doğrulama:", feedback)
        if meta is None:
            messages = [ask, {"role": "assistant", "content": text},
                        {"role": "user", "content": feedback + "; makalenin TAMAMINI yalnızca ---META--- JSON ve ---BODY--- blokları olarak ver"}]
        else:
            messages = [ask, {"role": "assistant", "content": base.render(meta, body)},
                        {"role": "user", "content": base.revise_prompt(feedback)}]
    sys.exit(f"Makale {attempts} denemede doğrulamayı geçemedi — yayımlanmadı.")


def display(obj):
    """Doğrulama sonrası yayımlanan kalıba (uygulama yapan şömine markası) geçer."""
    if isinstance(obj, str):
        return obj.replace(GEN_SUBSTR, DISPLAY_SUBSTR)
    if isinstance(obj, list):
        return [display(x) for x in obj]
    if isinstance(obj, dict):
        return {k: display(v) for k, v in obj.items()}
    return obj


def hero_of(prods, name):
    return next((p for p in prods if p["name"].lower() == (name or "").lower()), prods[0])


# ── Görseller (gpt-image-1) ───────────────────────────────────────────────
def lifestyle(product, scene, no_image=False):
    from PIL import Image
    src = Image.open(io.BytesIO(base.http_get(product["gorsel"], binary=True))).convert("RGB")
    if not no_image:
        try:
            import openai
            buf = io.BytesIO()
            src.save(buf, "PNG")
            buf.seek(0)
            prompt = ("Use the fireplace in the provided product photo as the exact reference: keep its design, material, "
                      "finish and every detail unchanged. Create a photorealistic interior lifestyle photograph: "
                      f"{scene}. Warm ambient light, 35mm lens, no people, no text, no logos, no watermark, "
                      "no extra or deformed fireplaces.")
            res = openai.OpenAI().images.edit(model="gpt-image-1.5", image=("product.png", buf, "image/png"),
                                              prompt=prompt, size="1536x1024", quality="medium")
            log("Görsel üretildi (gpt-image-1):", product["name"])
            return Image.open(io.BytesIO(base64.b64decode(res.data[0].b64_json))).convert("RGB"), True
        except Exception as e:  # noqa: BLE001
            log("gpt-image-1 başarısız, ürün fotoğrafı kullanılacak:", str(e)[:200])
    return src, False


def composite(img_a, img_b, w=1440, h=960, gap=8):
    from PIL import Image
    half = (w - gap) // 2
    canvas = Image.new("RGB", (w, h), (250, 250, 249))
    canvas.paste(base.cover(img_a, half, h), (0, 0))
    canvas.paste(base.cover(img_b, half, h), (w - half, 0))
    return canvas


def jpeg(img, w=None, h=None):
    if w:
        img = base.cover(img, w, h)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=86)
    return buf.getvalue()


# ── WordPress ─────────────────────────────────────────────────────────────
def wp_headers(extra=None):
    tok = base64.b64encode(f"{os.environ['WP_USER']}:{os.environ['WP_APP_PASSWORD']}".encode()).decode()
    return {"Authorization": f"Basic {tok}", "User-Agent": base.UA, **(extra or {})}


def wp_json(url, data=None, method="GET"):
    req = urllib.request.Request(url, method=method, headers=wp_headers({"Content-Type": "application/json"}))
    if data is not None:
        req.data = json.dumps(data).encode("utf-8")
    with urllib.request.urlopen(req, timeout=90) as r:
        body = r.read()
        return json.loads(body) if body else None


def wp_upload(data, filename, alt):
    req = urllib.request.Request(f"{WP_API}/media", data=data, method="POST", headers=wp_headers({
        "Content-Disposition": f"attachment; filename={filename}", "Content-Type": "image/jpeg"}))
    with urllib.request.urlopen(req, timeout=120) as r:
        media = json.loads(r.read())
    wp_json(f"{WP_API}/media/{media['id']}", {"alt_text": alt}, "POST")
    return media


def md_to_html(md):
    import markdown
    return markdown.markdown(md, extensions=["extra", "sane_lists"])


def faq_html(faq):
    items = "".join(
        f'<details class="yss-faq-item"><summary class="yss-faq-q">{html.escape(f["q"])}</summary>'
        f'<div class="yss-faq-a">{html.escape(f["a"])}</div></details>' for f in faq)
    return f'<div class="yss-faq-wrap"><h2>Sık Sorulan Sorular</h2>{items}</div>'


def specs_html(specs, short_a, short_b):
    th = 'style="text-align:left;padding:10px 12px;border-bottom:2px solid #B8461F;"'
    td = 'style="padding:10px 12px;border-bottom:1px solid #e6dcd5;vertical-align:top;"'
    rows = "".join(f'<tr><th scope="row" {td}>{html.escape(s["label"])}</th>'
                   + "".join(f"<td {td}>{html.escape(v)}</td>" for v in s["values"]) + "</tr>" for s in specs)
    return ('<h2>Karşılaştırma Tablosu</h2><div style="overflow-x:auto;margin:16px 0 28px;">'
            '<table style="width:100%;border-collapse:collapse;font-size:15px;">'
            f'<thead><tr><th {th}>Özellik</th><th {th}>{html.escape(short_a)}</th><th {th}>{html.escape(short_b)}</th></tr></thead>'
            f"<tbody>{rows}</tbody></table></div>")


def box_html(title, inner, strong=False):
    border = "2px solid #B8461F" if strong else "1px solid #e6dcd5"
    return (f'<div style="border:{border};background:#fbf6f3;border-radius:10px;padding:20px 22px;margin:24px 0;">'
            f'<p style="margin:0 0 8px;font-weight:700;color:#B8461F;">{title}</p>{inner}</div>')


def cards_html(cfg, pair, prods):
    cards = []
    for side, key in (("A", pair["a"]), ("B", pair["b"])):
        t = cfg["types"][key]
        for p in prods[side]:
            cards.append(f'<a href="{p["url"]}" style="text-decoration:none;color:inherit;display:block;">'
                         f'<img src="{p["gorsel"]}" alt="{html.escape(p["name"])} — {html.escape(t["name"])}" '
                         'style="width:100%;height:auto;border-radius:8px;" loading="lazy" />'
                         f'<p style="margin:8px 0 0;font-weight:600;text-align:center;">{html.escape(p["name"])}'
                         f'<br><span style="font-weight:400;font-size:13px;color:#77685f;">{html.escape(t["name"])}</span></p></a>')
    grid = "".join(f"<div>{c}</div>" for c in cards)
    return ('<h2>Karşılaştırmadaki Modeller</h2>'
            f'<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(180px,1fr));gap:16px;margin:16px 0;">{grid}</div>')


def schema_json(meta, cfg, pair, prods, url, image_url):
    names = (cfg["types"][pair["a"]]["name"], cfg["types"][pair["b"]]["name"])
    heroes = (hero_of(prods["A"], meta["heroA"]), hero_of(prods["B"], meta["heroB"]))
    bests = (meta["bestForA"], meta["bestForB"])
    graph = [
        {"@type": "FAQPage", "mainEntity": [{"@type": "Question", "name": f["q"],
                                              "acceptedAnswer": {"@type": "Answer", "text": f["a"]}} for f in meta["faq"]]},
        {"@type": "ItemList", "name": meta["title"], "description": meta["verdict"], "url": url, "numberOfItems": 2,
         "itemListElement": [{"@type": "ListItem", "position": i + 1, "item": {
             "@type": "Product", "name": f"{heroes[i]['name']} ({names[i]})", "url": heroes[i]["url"],
             "image": heroes[i]["gorsel"], "category": names[i], "brand": {"@type": "Brand", "name": "Yanar Şömine"},
             "description": f"Kime uygun: {bests[i]}",
             "additionalProperty": [{"@type": "PropertyValue", "name": s["label"], "value": s["values"][i]} for s in meta["specs"]],
         }} for i in range(2)]},
        {"@type": "Article", "headline": meta["title"], "description": meta["description"], "image": image_url,
         "datePublished": datetime.datetime.now(TR).isoformat(), "inLanguage": "tr", "mainEntityOfPage": url,
         "abstract": " ".join(meta["keyPoints"]), "about": [{"@type": "Thing", "name": n} for n in names],
         "author": {"@type": "Person", "name": "Emre İpkin", "jobTitle": "Şömine İç Mimarı"},
         "publisher": {"@type": "Organization", "name": "Yanar Şömine", "url": WP_SITE}},
    ]
    data = json.dumps({"@context": "https://schema.org", "@graph": graph}, ensure_ascii=False).replace("</", "<\\/")
    return f'<script type="application/ld+json">{data}</script>'


def insert_images(body_html, img_a, img_b, alts):
    tag = lambda u, a: (f'<figure style="margin:24px 0;"><img src="{u}" alt="{html.escape(a)}" '
                        'style="width:100%;height:auto;border-radius:8px;" loading="lazy" /></figure>')
    parts = re.split(r"(?=<h2>)", body_html)
    if len(parts) > 2:
        parts.insert(2, tag(img_a, alts[0]))
    if len(parts) > 5:
        parts.insert(len(parts) // 2 + 1, tag(img_b, alts[1]))
    else:
        parts.append(tag(img_b, alts[1]))
    return "".join(parts)


def wp_find(slug):
    found = wp_json(f"{WP_API}/posts?slug={slug}&status=any&context=edit&_fields=id,link,status")
    return found[0] if found else None


def publish_wp(cfg, pair, prods, meta, body, imgs, draft=False):
    slug = f"{pair['slug']}-somine-karsilastirmasi"
    existing = wp_find(slug)
    if existing:
        log("WP yazısı zaten var, tekrar yayımlanmıyor:", existing["link"])
        return existing
    na, nb = cfg["types"][pair["a"]]["short"], cfg["types"][pair["b"]]["short"]
    cover = wp_upload(jpeg(imgs["cover"]), f"{slug}.jpg", meta["imageAlt"])
    alt_a = f"{hero_of(prods['A'], meta['heroA'])['name']} — {cfg['types'][pair['a']]['name']}"
    alt_b = f"{hero_of(prods['B'], meta['heroB'])['name']} — {cfg['types'][pair['b']]['name']}"
    ma = wp_upload(jpeg(imgs["A"], 1200, 800), f"{slug}-{pair['a']}.jpg", alt_a)
    mb = wp_upload(jpeg(imgs["B"], 1200, 800), f"{slug}-{pair['b']}.jpg", alt_b)
    url = f"{WP_SITE}/{slug}/"
    points = "<ul>" + "".join(f"<li>{html.escape(k)}</li>" for k in meta["keyPoints"]) + "</ul>"
    cta = ('<div style="text-align:center;margin:32px 0;">'
           f'<a href="{CTA_URL}" style="display:inline-block;background:#B8461F;color:#fff;font-weight:600;font-size:15px;'
           'padding:14px 30px;border-radius:6px;text-decoration:none;">Ücretsiz Keşif İste</a></div>')
    content = "\n".join([
        f"<!-- yss-karsilastirma: {pair['slug']} -->",
        box_html("Kısaca", points),
        specs_html(meta["specs"], na, nb),
        insert_images(md_to_html(body), ma["source_url"], mb["source_url"], (alt_a, alt_b)),
        box_html("Karar", f"<p style=\"margin:0;\">{html.escape(meta['verdict'])}</p>", strong=True),
        cards_html(cfg, pair, prods),
        faq_html(meta["faq"]),
        cta,
        schema_json(meta, cfg, pair, prods, url, cover["source_url"]),
    ])
    post = wp_json(f"{WP_API}/posts", {
        "title": meta["title"], "slug": slug, "content": content, "excerpt": meta["description"],
        "status": "draft" if draft else "publish", "author": WP_AUTHOR_ID,
        "categories": [WP_CATEGORY_ID], "featured_media": cover["id"],
    }, "POST")
    try:
        wp_json(f"{BRIDGE}/yoast-meta", {"post_id": post["id"], "metadesc": meta["description"]}, "POST")
    except Exception as e:  # noqa: BLE001
        log("::warning::Yoast meta yazılamadı:", str(e)[:150])
    log("WP yayımlandı:", post["link"])
    return post


def update_llms(title, link):
    try:
        content = (wp_json(f"{BRIDGE}/root-file?name=llms.txt") or {}).get("content", "")
        line = f"- [{title}]({link})"
        if line in content:
            return
        marker = "## Şömine Karşılaştırmaları"
        if marker in content:
            i = content.index(marker) + len(marker)
            content = content[:i] + "\n\n" + line + content[i:].replace("\n\n", "\n", 1)
        else:
            section = f"\n{marker}\n\n{line}\n"
            content = (content.replace("\n## İletişim", section + "\n## İletişim", 1)
                       if "\n## İletişim" in content else content.rstrip("\n") + "\n" + section)
        wp_json(f"{BRIDGE}/root-file", {"name": "llms.txt", "content": content}, "POST")
        log("llms.txt güncellendi.")
    except Exception as e:  # noqa: BLE001
        log("::warning::llms.txt güncellenemedi:", str(e)[:150])


def support_log(title, notes):
    secret = os.environ.get("INTERNAL_API_SECRET")
    if not secret:
        return
    try:
        req = urllib.request.Request(SUPPORT_API_URL, method="POST", data=json.dumps({
            "customerId": SUPPORT_CUSTOMER_ID, "title": title, "notes": notes, "completed": True}).encode(),
            headers={"x-internal-secret": secret, "Content-Type": "application/json", "User-Agent": base.UA})
        urllib.request.urlopen(req, timeout=30).read()
        log("destek.visibiy.com Yapılanlar'a eklendi.")
    except Exception as e:  # noqa: BLE001
        log("::warning::destek paneline eklenemedi:", str(e)[:150])


# ── sominerehberi.com yazısı ──────────────────────────────────────────────
def q(s):
    return json.dumps(s, ensure_ascii=False)


def card_image(slug, side, product):
    from PIL import Image
    img = Image.open(io.BytesIO(base.http_get(product["gorsel"], binary=True))).convert("RGB")
    IMAGES.mkdir(parents=True, exist_ok=True)
    base.fit_card(img, 900, 900).save(IMAGES / f"{slug}-{side.lower()}.webp", "WEBP", quality=82)
    return f"/images/posts/{slug}-{side.lower()}.webp"


def write_site_post(cfg, pair, prods, meta, body, cover_img, ai, wp_post):
    slug = f"{pair['slug']}-karsilastirma"
    IMAGES.mkdir(parents=True, exist_ok=True)
    cover_img.save(IMAGES / f"{slug}.webp", "WEBP", quality=82)
    ha, hb = hero_of(prods["A"], meta["heroA"]), hero_of(prods["B"], meta["heroB"])
    ta, tb = cfg["types"][pair["a"]], cfg["types"][pair["b"]]
    fm = ["---", f"title: {q(meta['title'])}", f"description: {q(meta['description'])}", f"date: {today()}",
          f"category: {SITE_CATEGORY}", f"image: /images/posts/{slug}.webp", f"imageAlt: {q(meta['imageAlt'])}",
          f"imageCredit: {q('Yanar Şömine ürün fotoğraflarından yapay zekâ ile düzenlendi' if ai else 'Yanar Şömine ürün fotoğrafları')}",
          "imageCreditUrl: https://www.yanarsomine.com.tr/urunler/",
          f"tags: {json.dumps(meta.get('tags') or ['şömine karşılaştırma'], ensure_ascii=False)}", "comparison:"]
    for side, h, t, best in (("A", ha, ta, meta["bestForA"]), ("B", hb, tb, meta["bestForB"])):
        fm += [f"  - name: {q(h['name'] + ' — ' + t['name'])}", f"    short: {q(h['name'] + ' · ' + t['short'])}",
               f"    url: {h['url']}", f"    image: {card_image(slug, side, h)}", f"    bestFor: {q(best)}"]
    fm.append("specs:")
    for s in meta["specs"]:
        fm += [f"  - label: {q(s['label'])}", f"    values: {json.dumps(s['values'], ensure_ascii=False)}"]
    fm.append(f"verdict: {q(meta['verdict'])}")
    fm.append("products:")
    for p in [x for side in "AB" for x in prods[side] if x["url"] in body] or [ha, hb]:
        fm += [f"  - name: {q(p['name'])}", f"    url: {p['url']}"]
    fm.append("keyPoints:")
    fm += [f"  - {q(k)}" for k in meta["keyPoints"]]
    fm.append("faq:")
    for f in meta["faq"]:
        fm += [f"  - q: {q(f['q'])}", f"    a: {q(f['a'])}"]
    fm += ["featured: false", "draft: false", "---", ""]
    tail = f"\n\nYanar Şömine'nin aynı konudaki yazısı: [{wp_post['title']}]({wp_post['link']})\n" if wp_post else "\n"
    note = ("*Bu karşılaştırmadaki şömineler Yanar Şömine ürün kataloğundan seçilmiştir"
            + ("; kapak görseli ürün fotoğraflarından yapay zekâ ile düzenlenmiştir.*" if ai else ".*"))
    POSTS.mkdir(parents=True, exist_ok=True)
    (POSTS / f"{slug}.md").write_text("\n".join(fm) + body.strip() + tail + "\n" + note + "\n", encoding="utf-8")
    return slug


def gh_output(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in kw.items():
                f.write(f"{k}={v}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--wp-draft", action="store_true", help="WP yazısını taslak olarak kaydet (deneme)")
    ap.add_argument("--env-file")
    a = ap.parse_args()
    if a.env_file:
        base.load_env_file(a.env_file)

    cfg = json.loads(PAIRS_FILE.read_text(encoding="utf-8"))
    state = load_state()
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and any(e.get("date") == today() and e.get("site_slug") for e in state):
        log("Bugün zaten bir karşılaştırma yayımlandı (yedek tetikleme) — çıkılıyor.")
        gh_output(created="", all_done="false")
        return
    pair = next_pair(cfg, state, a.pair)
    if not pair:
        log("67 karşılaştırmanın tamamı yayımlandı — yapılacak iş yok.")
        gh_output(created="", all_done="true")
        return
    log(f"Çift: {pair['slug']}  (kalan: {remaining(cfg, state)})")
    entry = entry_for(state, pair["slug"])
    if entry and entry.get("products_a"):
        prods = {"A": entry["products_a"], "B": entry["products_b"]}
    else:
        prods = {"A": pick_products(cfg, pair["a"], state), "B": pick_products(cfg, pair["b"], state)}
    log("A:", ", ".join(p["name"] for p in prods["A"]), "| B:", ", ".join(p["name"] for p in prods["B"]))
    if a.dry_run:
        return

    ARCHIVE.mkdir(exist_ok=True)
    arch = ARCHIVE / f"{pair['slug']}.json"
    if entry and entry.get("wp_id") and arch.exists():  # WP tamam, yalnızca site yazısı kaldı
        saved = json.loads(arch.read_text(encoding="utf-8"))
        wp_meta, wp_body = saved["meta"], saved["body"]
        wp_post = {"title": entry["wp_title"], "link": entry["wp_link"]}
        imgs, ai = None, False
    else:
        wp_meta, wp_body = write_article(prompt_wp(cfg, pair, prods), prods, need_scenes=True)
        ha, hb = hero_of(prods["A"], wp_meta["heroA"]), hero_of(prods["B"], wp_meta["heroB"])
        img_a, ai_a = lifestyle(ha, wp_meta["imageSceneA"], a.no_image)
        img_b, ai_b = lifestyle(hb, wp_meta["imageSceneB"], a.no_image)
        imgs, ai = {"A": img_a, "B": img_b, "cover": composite(img_a, img_b)}, ai_a and ai_b
        post = publish_wp(cfg, pair, prods, wp_meta, wp_body, imgs, a.wp_draft)
        wp_title = post.get("title", {}).get("rendered", wp_meta["title"]) if isinstance(post.get("title"), dict) else wp_meta["title"]
        wp_post = {"title": html.unescape(wp_title), "link": post["link"]}
        arch.write_text(json.dumps({"meta": wp_meta, "body": wp_body}, ensure_ascii=False, indent=1), encoding="utf-8")
        state = [e for e in state if e["pair"] != pair["slug"]] + [{
            "pair": pair["slug"], "date": today(), "wp_id": post["id"], "wp_link": post["link"], "wp_title": wp_post["title"],
            "products": [p["url"] for side in "AB" for p in prods[side]], "products_a": prods["A"], "products_b": prods["B"]}]
        save_state(state)
        if not a.wp_draft:
            update_llms(wp_post["title"], wp_post["link"])
            support_log("Karşılaştırma blogu eklendi", f"{wp_post['title']} — {wp_post['link']}")

    ref = {"title": wp_meta["title"], "body": wp_body}
    site_meta, site_body = write_article(prompt_site(cfg, pair, prods, wp_meta, wp_body), prods, need_scenes=False, ref=ref)
    if imgs is None:
        ha, hb = hero_of(prods["A"], site_meta["heroA"]), hero_of(prods["B"], site_meta["heroB"])
        img_a, ai_a = lifestyle(ha, f"modern living room with the fireplace burning, evening", a.no_image)
        img_b, ai_b = lifestyle(hb, f"cozy living room with the fireplace burning, evening", a.no_image)
        imgs, ai = {"cover": composite(img_a, img_b)}, ai_a and ai_b
    site_slug = write_site_post(cfg, pair, prods, site_meta, site_body, imgs["cover"], ai, wp_post)
    for e in state:
        if e["pair"] == pair["slug"]:
            e.update(site_slug=site_slug, date=today())
    save_state(state)
    log(f"Site yazısı: {site_slug}.md — {len(site_body.split())} kelime; WP: {wp_post['link']}; "
        f"örtüşme %{overlap(site_body, wp_body) * 100:.1f}")
    gh_output(created=site_slug, wp_link=wp_post["link"], all_done="true" if not next_pair(cfg, state) else "false")


if __name__ == "__main__":
    main()
