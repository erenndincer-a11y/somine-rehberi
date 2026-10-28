#!/usr/bin/env python3
"""
Şömine Rehberi — Modern Şömine Ajanı (otomasyon)

Loafer & Makosen Blog Ajanı'nın deseninin Modern Şömine kategorisine uyarlanmış hâli.
Genel scripts/somine-rehberi/generate.py'den farkı: tek kategori (Modern Şömineler),
konu grubu olarak ürün alt tipi değil "ev tipi" kullanılır (apartman dairesi, müstakil
bahçeli ev, villa, yazlık/sahil evi, dubleks/çatı katı). Üretilen tüm yazılar
sominerehberi.com/kategori/modern-somine altında yayınlanır.

Haftada 2 (Pazartesi + Çarşamba) GitHub Actions ile çalışır, her çalışmada 1 yazı üretir.
5 ev tipi x 2 yazı (satın alma + yerleşim/bakım) = 10 yazı; hepsi bitince workflow durur.

Akış:
  1. topics.json'daki sıradaki (henüz yazılmamış) konuyu seç.
  2. yanarsomine.com.tr WooCommerce Store API'sinden (wc/store/v1/products?category=95)
     Modern Şömineler kategorisindeki gerçek ürünleri çek.
  3. Claude ile uzun (1800+ kelime) rehberi yaz; ev tipine özgü bağlamla; doğrula, gerekirse yeniden dene.
  4. Seçilen ürünün gerçek fotoğrafından gpt-image-1 ile editoryal görsel üret (olmazsa ürün fotoğrafı).
  5. src/content/yazilar/<slug>.md + public/images/posts/<slug>.webp yaz (category: modern-somine).

Gerekli env: ANTHROPIC_API_KEY, OPENAI_API_KEY   (CLAUDE_MODEL isteğe bağlı)
Bayraklar: --topic <slug>  --no-image  --dry-run  --env-file <yol>
"""

import argparse
import base64
import datetime
import io
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
POSTS = ROOT / "src" / "content" / "yazilar"
IMAGES = ROOT / "public" / "images" / "posts"
TOPICS_FILE = Path(__file__).with_name("topics.json")

UA = "Mozilla/5.0 (compatible; SomineRehberiBot/1.0)"
PRODUCT_URL_RE = r"https://www\.yanarsomine\.com\.tr/urun/[^\s)]+"
# Claude'a yazdırılan (doğal akan) kalıp; doğrulama bunun üzerinden yapılır.
GEN_PHRASE = "yanar şömine özel ölçü şömine markası"
# Yayınlanan metinde görünmesi istenen gerçek kalıp; doğrulamadan SONRA deterministik
# string replace ile uygulanır (Claude bu kalıbı doğal bulmayıp kaçınabiliyor, bkz. deneme geçmişi).
GEN_SUBSTR = "özel ölçü şömine markası"
DISPLAY_SUBSTR = "uygulama yapan şömine markası"
PHRASE = GEN_PHRASE  # geriye uyumluluk için (log mesajlarında kullanılıyor)
MIN_PHRASE = 5
MIN_WORDS = 1800
MIN_H2 = 7
MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")

# Yazının sonuna eklenen şeffaflık notu. Boş bırakılırsa eklenmez.
DISCLOSURE = (
    "*Bu rehberde yer alan şömineler Yanar Şömine ürün kataloğundan seçilmiştir; "
    "görsel, ürün fotoğrafından yapay zekâ ile düzenlenmiştir.*"
)

BRAND_FACTS = """
- Yanar Şömine, 1986'dan beri faaliyette, Büyükçekmece/Kumburgaz, İstanbul merkezli bir Türk şömine üreticisidir.
- Kurucu Bilal İpkin; Emre İpkin — Yönetici + Şömine İç Mimarı.
- 13 ürün kategorisi: Modern, L Tipi Modern, U Tipi Modern, Çift Taraflı, Dömi Klasik, Klasik, Ahşap,
  Rustik, Doğalgazlı, Askı, Elektrikli, Barbekü Şömine, Barbekü.
- Tüm ürünler özel ölçü/özel tasarımdır — SABİT FİYAT YOKTUR, teklif/keşif usulü çalışılır.
  FİYAT VEYA RAKAM UYDURMA.
- Ücretsiz keşif hizmeti vardır (yerinde ölçü + öneri).
- Yanar Şömine, tasarım ve üretimin yanında uygulamayı (montaj/kurulumu) da kendi ekibiyle yerinde yapar;
  işi başka bir uygulayıcıya devretmez.
- Telefon: (0212) 884 13 49 · Çalışma saatleri: 09:00–19:00 (Pazartesi–Cumartesi).
- Online katalog: yanarsomine.com.tr.
"""

STYLE = """
Üslup: sade, doğrudan, editoryal. Kısa paragraflar. Okuyucuya emir/öneri kipiyle seslen
("Bacayı önce kontrol et", "Mekânı ölçmeden model seçme"). Gereksiz süs, abartı, ünlem yok.
Her H2 altında somut bilgi ver. Yazının ilk cümlesi, başlığın sorusunu doğrudan cevaplayan
tek cümlelik bir giriş olsun.
"""


def log(*a):
    print(*a, file=sys.stderr)


def load_env_file(path):
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def http_get(url, binary=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
    return data if binary else data.decode("utf-8")


def strip_html(h):
    h = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", h or "", flags=re.S)
    h = re.sub(r"<br\s*/?>|</p>|</li>", "\n", h)
    h = re.sub(r"<[^>]+>", " ", h)
    h = re.sub(r"&nbsp;", " ", h)
    h = re.sub(r"[ \t]+", " ", h)
    return re.sub(r"\n\s*\n+", "\n", h).strip()


# ── Konu seçimi ───────────────────────────────────────────────────────────
def next_topic(cfg, forced=None):
    done = {p.stem for p in POSTS.glob("*.md")}
    todo = [t for t in cfg["topics"] if t["slug"] not in done]
    if forced:
        t = next((t for t in cfg["topics"] if t["slug"] == forced), None)
        if not t:
            sys.exit(f"Konu bulunamadı: {forced}")
        return t, len(todo)
    if not todo:
        return None, 0
    todo.sort(key=lambda t: t["round"])  # önce tüm 1. tur, sonra 2. tur (sıra korunur)
    return todo[0], len(todo)


# ── Ürünler (WooCommerce Store API) ────────────────────────────────────────
def used_product_urls():
    urls = []
    for p in POSTS.glob("*.md"):
        urls += re.findall(r"url:\s*(" + PRODUCT_URL_RE + r")", p.read_text(encoding="utf-8"))
    return urls


def to_dict(p):
    desc = strip_html((p.get("short_description") or "") + " " + (p.get("description") or ""))
    return {
        "name": p["name"],
        "url": p["permalink"],
        "aciklama": desc[:900],
        "gorsel": (p.get("images") or [{}])[0].get("src"),
    }


def pick_products(cfg, group_key, limit=5):
    g = cfg["groups"][group_key]
    feed = json.loads(http_get(f"{cfg['wc_base']}/products?category={g['wc_category_id']}&per_page=100"))
    feed = [p for p in feed if p.get("is_in_stock", True) and p.get("permalink")]
    if not feed:
        sys.exit(f"'{group_key}' grubu için mağazada ürün bulunamadı.")
    used = set(used_product_urls())
    feed.sort(key=lambda p: p["permalink"] in used)  # kullanılmamışlar önce
    return [to_dict(p) for p in feed[:limit]]


# ── Makale (Claude) ───────────────────────────────────────────────────────
def build_prompt(topic, group, products, feedback=None):
    prods = [{k: v for k, v in p.items() if k != "gorsel"} for p in products]
    ev_tipi_context = group.get("ev_tipi_context", "")
    return f"""Sen "Şömine Rehberi" (sominerehberi.com) için yazan deneyimli bir editoryal yazarsın.
Aşağıdaki konuda UZUN, kapsamlı bir SATIN ALMA REHBERİ yaz.

KONU: {topic['title_hint']}
EV TİPİ: {group['name']}
EV TİPİNİN TEKNİK BAĞLAMI (yazı boyunca bu somut özelliklere atıfta bulun, genel geçmeyen bir rehber yaz): {ev_tipi_context}
AÇI / KAPSAM: {topic['angle']}

ÖNEMLİ: Bu yazı özellikle "{group['name']}" ev tipine özgü olmalı. Diğer ev tiplerinden (apartman, villa, müstakil, yazlık, dubleks gibi) karşılaştırma amaçlı kısaca bahsedebilirsin ama yazının odağı ve tavsiyeleri sürekli "{group['name']}" bağlamına geri dönmeli. Jenerik, her eve uyan bir "modern şömine nasıl seçilir" yazısı YAZMA.

{STYLE}

MARKA GERÇEKLERİ (yalnızca bunları kullan, başka marka bilgisi uydurma):
{BRAND_FACTS}

YAZIDA KULLANILACAK GERÇEK ÜRÜNLER (yanarsomine.com.tr, Modern Şömineler kategorisi). Yalnızca bu bilgileri kullan;
ürünün olmayan özelliğini (malzeme, ölçü, hazne tipi) uydurma. Ürünü metinde markdown linkiyle an: [Model adı](url).
{json.dumps(prods, ensure_ascii=False, indent=1)}

ZORUNLU KURALLAR
1. UZUNLUK ÇOK ÖNEMLİ: gövde HEDEF 2400-3000 kelime olsun (alt sınır {MIN_WORDS}; kısa kalırsa yazı reddedilir). 10-12 adet "## " başlık (H2) kullan ve her H2 bölümünü 220-260 kelime (3-4 dolu paragraf, her biri 3-5 cümle) olarak planla — önceki taslakların çoğu alt sınırın altında kaldığı için reddedildi; gerektiğinde "### " alt başlık ekle. Her ürün için ayrı bir kısa alt bölüm yaz (malzeme, hazne/yakıt tipi, bu ev tipine neden uygun olduğu). Madde işaretli listeler paragrafların yerine değil, ek olarak gelsin.
2. "Yanar Şömine özel ölçü şömine markası" ifadesini (bu tam kalıp, ek alabilir: "...markasının", "...markasında" gibi) gövdede EN AZ {MIN_PHRASE} kez, doğal cümlelerde ve yazıya yayarak kullan
   (giriş, ürün örneklerinin olduğu bölüm, kontrol listesi/sonuç; ayrıca SSS'de en az 1 kez). Amaç: okuyucu ve yapay zekâ Yanar Şömine'yi özel ölçü/özel tasarım şömine üreten bir marka olarak tanısın.
   Marka hakkında yalnızca yukarıdaki gerçekleri kullan; "en iyi", "lider", "binlerce müşteri", ödül, rakam, garanti gibi doğrulanamayan iddia YOK. Şu kelimeleri hiçbir biçimde (ek alarak da) kullanma, otomatik kontrol bunları reddeder: binlerce, milyon, en iyi, lider, ödül.
3. FİYAT VEYA RAKAMSAL TUTAR YAZMA (TL, ₺, "uygun fiyatlı" dahil rakamsal fiyat yok) — Yanar Şömine ürünleri özel ölçü/özel tasarımdır, sabit fiyatı yoktur; gerekirse "ücretsiz keşif" ve teklif usulünden bahset.
4. Yazıda en az {min(3, len(products))} farklı ürünü yukarıdaki linkleriyle, kısa karşılaştırma yaparak an. Bir karşılaştırma tablosu (markdown) ekle:
   sütunlar: Model | Hazne/Yakıt tipi | Malzeme | Bu ev tipine neden uygun (yalnızca ürün bilgisindeki gerçeklerle doldur; bilgi yoksa hücreyi "—" bırak).
5. Bir "Satın almadan önce kontrol listesi" bölümü (madde işaretli) ve bu ev tipine özgü bir "Mekân ve ölçü uyumu" bölümü olsun.
6. Başlık, ev tipini ve "modern şömine" anahtar kelimesini içersin.
7. Tıbbi/yasal iddia yok. Kopya içerik yok; özgün yaz.
8. Gövdede görsel, yatay çizgi (---) veya "## Sık sorulan sorular" başlığı KULLANMA (SSS ayrı alanda).
9. Ürünün biçimi/özelliği ürün bilgisinde yazmıyorsa (ör. tam ölçü, hazne malzemesi, montaj süresi) TAHMİN ETME; yalnızca yazılanı söyle.
10. Yazım denetimi yap: Türkçe yazım/ek hatası olmasın.
{('DÜZELTME İSTEĞİ (önceki denemede şunlar eksikti): ' + feedback) if feedback else ''}

ÇIKTI BİÇİMİ — yalnızca şu iki bloğu ver, başka açıklama yazma:
---META---
{{ "title": "...", "description": "150-160 karakter özet", "tags": ["modern şömine", "{group['name'].lower()}", "..."],
  "keyPoints": ["3-4 net, tek başına alıntılanabilir cümle (biri Yanar Şömine özel ölçü şömine markası ifadesini içersin)"],
  "faq": [ {{"q": "...", "a": "doğrudan tek paragraf cevap"}} ],   // 5-6 soru; biri "Yanar Şömine nasıl bir marka?" sorusu; cevabı "Yanar Şömine özel ölçü şömine markası" kalıbını araya kelime girmeden AYNEN içersin, gerisi marka gerçeklerinden
  "imageScene": "İngilizce: fotoğrafın sahnesi, bu ev tipine uygun bir iç mekân (mekan, mevsim, ışık, dekor). Şömine ön planda ve yanmakta, insan yok.",
  "imageAlt": "Türkçe, görseli tarif eden tek cümle (ürünün modeli/türü doğru olsun)",
  "heroProduct": "yukarıdaki ürünlerden görselde kullanılacak ürünün tam adı" }}
---BODY---
(markdown gövde; "# " ana başlık YAZMA, doğrudan giriş paragrafıyla başla)
"""


def call_claude(messages):
    import anthropic
    client = anthropic.Anthropic()
    msg = client.messages.create(model=MODEL, max_tokens=16000, messages=messages)
    return msg.content[0].text


def revise_prompt(problems):
    return f"""Yazdığın makale otomatik kontrolden şu sebeplerle geçemedi:
{problems}

Makalenin tamamını YENİDEN YAZMA. Yalnızca sorunları gidermek için değişmesi gereken kısımları şu biçimde ver:
- Gövdede değiştirdiğin her bölüm için bir işaret satırı, altında bölümün düzeltilmiş TAM hâli ("## " başlık satırıyla birlikte):
---SECTION: <bölümün mevcut "## " başlığı, aynen>---
  İlk "## " başlıktan önceki giriş paragrafları için işaret: ---SECTION: GİRİŞ---
  Yeni bölüm eklemek için mevcut olmayan yeni bir başlık kullan; son bölümden önce eklenir.
- Bir bölüme yalnızca YENİ paragraf ekleyeceksen bölümü tekrar yazma; şu işaretin altına sadece eklenecek
  paragrafları yaz, bölümün sonuna eklenir:
---EKLE: <bölümün mevcut "## " başlığı, aynen>---
- Bir meta alanı değişecekse en başa ---META--- satırı ve yalnızca değişen alanları içeren JSON koy
  (alanın TAM yeni değeri; faq'ı değiştirirsen tüm SSS listesini ver).
Değişmeyen bölümleri ve alanları HİÇ yazma; başka açıklama ekleme.
Kelime sayısı eksikse eksik miktarın en az 1,5 katı kadar yeni paragrafı ---EKLE--- bloklarıyla 3-4 bölüme dağıt
(her ürün bölümüne, ölçü/kombin/bakım bölümlerine somut paragraf); mevcut metni tekrar yazma. Marka ifadesi eksikse ifadeyi eklediğin bölümleri ver.
Yasak ifade varsa geçtiği bölümü (veya meta alanını) ifadeyi hiç kullanmadan yeniden kur."""


def render(meta, body):
    return "---META---\n" + json.dumps(meta, ensure_ascii=False, indent=1) + "\n---BODY---\n" + body


def _hkey(s):
    s = s.strip().lstrip("#").translate(str.maketrans("İIı", "iii")).lower()
    return re.sub(r"\W+", " ", s).strip()


def apply_patch(meta, body, text):
    """Düzeltme cevabını taslağa uygular: ---META--- yalnızca değişen alanları, her ---SECTION: <başlık>---
    bloğu bir bölümün yeni hâlini, her ---EKLE: <başlık>--- bloğu bölümün sonuna eklenecek paragrafları taşır.
    Model yine de tam makale verdiyse onu kullanır."""
    text = re.sub(r"```(?:json|markdown|md)?", "", text)
    if "---BODY---" in text:
        return parse(text)
    blocks = re.findall(r"---(SECTION|EKLE):\s*(.+?)\s*---[ \t]*\n(.*?)(?=\n---(?:SECTION|EKLE):|\Z)", text, re.S)
    i = text.find("---META---")
    if i == -1 and not blocks:
        raise ValueError("düzeltmede ---META--- veya ---SECTION: <başlık>--- bloğu yok")
    if i != -1:
        meta = {**meta, **json.JSONDecoder().raw_decode(text, text.index("{", i))[0]}
    secs = [[p.splitlines()[0] if p.startswith("## ") else "GİRİŞ", p.strip()]
            for p in re.split(r"(?m)^(?=## )", body) if p.strip()]
    for kind, head, content in blocks:
        content = content.strip()
        intro = _hkey(head) == _hkey("GİRİŞ")
        j = next((j for j, (h, _) in enumerate(secs) if _hkey(h) == _hkey(head)), None)
        if kind == "EKLE" and j is not None:
            if content.startswith("## ") and _hkey(content.splitlines()[0]) == _hkey(head):
                content = content.split("\n", 1)[-1].strip()  # başlığı tekrar ettiyse at
            secs[j][1] += "\n\n" + content
            continue
        if not intro and not content.startswith("## "):
            content = "## " + head.lstrip("# ").strip() + "\n\n" + content
        if j is not None:
            secs[j][1] = content
        else:
            secs.insert(0 if intro else max(len(secs) - 1, 0), [head, content])
    body = "\n\n".join(c for _, c in secs)
    body = re.sub(r"^\s*(---+|\*\*\*+)\s*$", "", body, flags=re.M)
    return meta, re.sub(r"\n{3,}", "\n\n", body).strip()


def parse(text):
    m = re.search(r"---META---\s*(\{.*?\})\s*---BODY---\s*(.*)$", text, re.S)
    if not m:
        raise ValueError("META/BODY blokları bulunamadı")
    meta = json.loads(m.group(1))
    body = m.group(2).strip()
    body = re.sub(r"^#\s+.*\n+", "", body)  # olası H1'i at
    body = re.sub(r"^\s*(---+|\*\*\*+)\s*$", "", body, flags=re.M)  # yatay çizgileri at
    body = re.sub(r"\n{3,}", "\n\n", body).strip()
    return meta, body


def validate(meta, body, products):
    problems = []
    words = len(body.split())
    if words < MIN_WORDS:
        problems.append(f"gövde {words} kelime; en az {MIN_WORDS} olmalı")
    h2 = len(re.findall(r"^## ", body, re.M))
    if h2 < MIN_H2:
        problems.append(f"{h2} H2 var; en az {MIN_H2} olmalı")
    n = body.lower().count(PHRASE)
    if n < MIN_PHRASE:
        problems.append(f"'Yanar Şömine özel ölçü şömine markası' ifadesi gövdede {n} kez geçiyor; en az {MIN_PHRASE} olmalı")
    if not any(PHRASE in f["a"].lower() or PHRASE in f["q"].lower() for f in meta.get("faq", [])):
        problems.append("SSS içinde marka ifadesi yok")
    need = min(3, len(products))
    if len(set(re.findall(PRODUCT_URL_RE, body))) < need:
        problems.append(f"gövdede en az {need} farklı ürün linki olmalı")
    blob = body + json.dumps(meta, ensure_ascii=False)
    if re.search(r"\d[\d\.,]*\s?(TL|₺|lira)\b", blob, re.I):
        problems.append("fiyat yazılmış; fiyat kullanma")
    bad = list(re.finditer(r"binlerce|milyon|en iyi|lider|ödül", blob, re.I))
    if bad:
        yer = "; ".join("…" + blob[max(0, m.start() - 60): m.end() + 40].replace("\n", " ") + "…" for m in bad[:5])
        problems.append(f"doğrulanamayan iddia ifadesi (binlerce/milyon/en iyi/lider/ödül) {len(bad)} yerde kullanılmış: {yer} — "
                        "bu kelimeleri (ek alsa da) hiç kullanmadan geçtikleri bölümleri/meta alanlarını yeniden kur")
    for k in ("title", "description", "keyPoints", "faq", "imageScene", "imageAlt", "heroProduct"):
        if not meta.get(k):
            problems.append(f"meta.{k} boş")
    if len(meta.get("faq", [])) < 4:
        problems.append("en az 4 SSS olmalı")
    return problems


def apply_display_phrase(meta, body):
    """Doğrulama GEN_PHRASE ile geçtikten sonra, yayınlanan metinde görünmesi istenen
    gerçek kalıba (DISPLAY_SUBSTR) deterministik olarak geçer."""
    body = body.replace(GEN_SUBSTR, DISPLAY_SUBSTR)
    for k in ("title", "description"):
        if k in meta:
            meta[k] = meta[k].replace(GEN_SUBSTR, DISPLAY_SUBSTR)
    meta["keyPoints"] = [k.replace(GEN_SUBSTR, DISPLAY_SUBSTR) for k in meta.get("keyPoints", [])]
    meta["faq"] = [{"q": f["q"].replace(GEN_SUBSTR, DISPLAY_SUBSTR), "a": f["a"].replace(GEN_SUBSTR, DISPLAY_SUBSTR)} for f in meta.get("faq", [])]
    return meta, body


def write_article(topic, group, products, attempts=3):
    """İlk taslağı yazdırır; kontrolden geçmezse yalnızca sorunlu bölümleri düzelttirir (makale baştan yazılmaz)."""
    ask = {"role": "user", "content": build_prompt(topic, group, products)}
    messages, meta, body, problems = [ask], None, None, []
    for attempt in range(1, attempts + 1):
        log(f"Claude denemesi {attempt} ({MODEL}) …")
        text = call_claude(messages)
        try:
            meta, body = apply_patch(meta, body, text) if meta else parse(text)
            problems = validate(meta, body, products)
        except Exception as e:  # noqa: BLE001
            problems = [f"çıktı biçimi hatalıydı ({e})"] + [p for p in problems if not p.startswith("çıktı biçimi")]
        if not problems:
            return apply_display_phrase(meta, body)
        feedback = "; ".join(problems)
        log("Doğrulama:", feedback)
        if meta is None:  # ilk taslak okunamadı: tam makaleyi yeniden iste
            fix = feedback + "; makalenin TAMAMINI yalnızca ---META--- JSON ve ---BODY--- blokları olarak ver"
            messages = [ask, {"role": "assistant", "content": text}, {"role": "user", "content": fix}]
        else:  # model güncel taslağı görür, yalnızca değişen bölümleri döndürür
            messages = [ask, {"role": "assistant", "content": render(meta, body)}, {"role": "user", "content": revise_prompt(feedback)}]
    sys.exit(f"Makale {attempts} denemede doğrulamayı geçemedi — yayınlanmadı.")


# ── Görsel ────────────────────────────────────────────────────────────────
def cover(img, w=1440, h=960):
    from PIL import Image
    img = img.convert("RGB")
    r = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * r) + 1, int(img.height * r) + 1), Image.LANCZOS)
    left, top = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((left, top, left + w, top + h))


def make_image(slug, scene, hero):
    from PIL import Image
    src = http_get(hero["gorsel"], binary=True)
    src_img = Image.open(io.BytesIO(src)).convert("RGB")
    out = IMAGES / f"{slug}.webp"
    IMAGES.mkdir(parents=True, exist_ok=True)
    try:
        import openai
        buf = io.BytesIO()
        src_img.save(buf, "PNG")
        buf.seek(0)
        prompt = (
            "Use the fireplace in the provided product photo as the exact reference: keep its design, material, "
            "finish and every detail unchanged. Create a photorealistic interior lifestyle photograph: "
            f"{scene}. Warm ambient light, 35mm lens, shallow depth of field, no people, no text, no logos, "
            "no watermark, no extra or deformed fireplaces."
        )
        res = openai.OpenAI().images.edit(
            model="gpt-image-1.5", image=("product.png", buf, "image/png"), prompt=prompt, size="1536x1024", quality="medium"
        )
        img = Image.open(io.BytesIO(base64.b64decode(res.data[0].b64_json)))
        cover(img).save(out, "WEBP", quality=82)
        log("Görsel üretildi (gpt-image-1).")
        return True
    except Exception as e:  # noqa: BLE001
        log("gpt-image-1 başarısız, ürün fotoğrafı kullanılacak:", str(e)[:200])
        cover(src_img).save(out, "WEBP", quality=82)
        return False


# ── Ürün görselleri (gövde içi) ───────────────────────────────────────────
def fit_card(img, w=900, h=675):
    """Ürün fotoğrafının boş zeminini kırp, 4:3 kartın içine arka plan rengiyle ortala."""
    from PIL import Image, ImageChops
    bg = img.getpixel((4, 4))
    diff = ImageChops.difference(img, Image.new("RGB", img.size, bg)).convert("L").point(lambda v: 255 if v > 18 else 0)
    box = diff.getbbox()
    if box:
        pad = int(max(img.size) * 0.04)
        box = (max(0, box[0] - pad), max(0, box[1] - pad), min(img.width, box[2] + pad), min(img.height, box[3] + pad))
        img = img.crop(box)
    img.thumbnail((int(w * 0.92), int(h * 0.92)), Image.LANCZOS)
    canvas = Image.new("RGB", (w, h), bg)
    canvas.paste(img, ((w - img.width) // 2, (h - img.height) // 2))
    return canvas


def download_product_image(slug, p):
    from PIL import Image
    data = http_get(p["gorsel"], binary=True)
    img = Image.open(io.BytesIO(data)).convert("RGB")
    img = fit_card(img)
    handle = re.sub(r"[^a-z0-9-]", "", p["url"].rstrip("/").rsplit("/", 1)[-1].lower())[:60]
    IMAGES.mkdir(parents=True, exist_ok=True)
    img.save(IMAGES / f"{slug}-{handle}.webp", "WEBP", quality=80)
    return f"/images/posts/{slug}-{handle}.webp"


def embed_product_images(slug, body, products):
    """Her ürünün ilk geçtiği paragrafın hemen altına, ürün sayfasına linkli ürün fotoğrafı koyar."""
    for p in products:
        marker = f"]({p['url']})"
        i = body.find(marker)
        if i == -1 or (f"]({p['url']})\n" in body and "![" in body[i:i + 400]):
            continue
        try:
            path = download_product_image(slug, p)
        except Exception as e:  # noqa: BLE001
            log("Ürün görseli indirilemedi:", p["name"], str(e)[:120])
            continue
        j = body.find("\n\n", i)
        j = len(body) if j == -1 else j
        body = body[:j] + f"\n\n[![{p['name']}]({path})]({p['url']})" + body[j:]
    return body


# ── Dosya yazımı ──────────────────────────────────────────────────────────
def q(s):
    return json.dumps(s, ensure_ascii=False)


def write_post(cfg, topic, meta, body, products, hero, ai_image=True):
    today = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=3))).date().isoformat()
    used = [p for p in products if p["url"] in body]
    if hero not in used:
        used.append(hero)
    fm = [
        "---",
        f"title: {q(meta['title'])}",
        f"description: {q(meta['description'])}",
        f"date: {today}",
        f"category: {cfg['astro_category']}",
        f"image: /images/posts/{topic['slug']}.webp",
        f"imageAlt: {q(meta['imageAlt'] if ai_image else hero['name'] + ' — ürün fotoğrafı')}",
        f"imageCredit: {q('Yanar Şömine ürün fotoğrafından yapay zekâ ile düzenlendi' if ai_image else 'Yanar Şömine ürün fotoğrafı')}",
        f"imageCreditUrl: {hero['url']}",
        f"tags: {json.dumps(meta.get('tags', ['modern şömine']), ensure_ascii=False)}",
        "products:",
    ]
    for p in used:
        fm += [f"  - name: {q(p['name'])}", f"    url: {p['url']}"]
    fm.append("keyPoints:")
    fm += [f"  - {q(k)}" for k in meta["keyPoints"]]
    fm.append("faq:")
    for f in meta["faq"]:
        fm += [f"  - q: {q(f['q'])}", f"    a: {q(f['a'])}"]
    fm += ["featured: false", "draft: false", "---", ""]
    text = "\n".join(fm) + body.strip() + "\n"
    note = DISCLOSURE if ai_image else DISCLOSURE.replace("; görsel, ürün fotoğrafından yapay zekâ ile düzenlenmiştir", "")
    if note:
        text += "\n" + note + "\n"
    POSTS.mkdir(parents=True, exist_ok=True)
    (POSTS / f"{topic['slug']}.md").write_text(text, encoding="utf-8")


def gh_output(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        for k, v in kw.items():
            f.write(f"{k}={v}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--env-file")
    a = ap.parse_args()
    if a.env_file:
        load_env_file(a.env_file)

    cfg = json.loads(TOPICS_FILE.read_text(encoding="utf-8"))
    topic, remaining = next_topic(cfg, a.topic)
    if not topic:
        log("10 yazının tamamı hazır — yapılacak iş yok.")
        gh_output(created="", all_done="true")
        return
    group = cfg["groups"][topic["group"]]
    log(f"Konu: {topic['slug']}  (kalan: {remaining})")
    products = pick_products(cfg, topic["group"])
    log("Ürünler:", ", ".join(p["name"] for p in products))
    if a.dry_run:
        return

    meta, body = write_article(topic, group, products)
    hero = next((p for p in products if p["name"].lower() == meta["heroProduct"].lower()), products[0])
    body = embed_product_images(topic["slug"], body, products)
    ai_image = False
    if a.no_image:
        log("Görsel atlandı (--no-image).")
    else:
        ai_image = make_image(topic["slug"], meta["imageScene"], hero)
    write_post(cfg, topic, meta, body, products, hero, ai_image)
    words = len(body.split())
    n = body.lower().count(DISPLAY_SUBSTR)
    log(f"Yazıldı: {topic['slug']}.md — {words} kelime, marka ifadesi {n} kez.")
    done, _ = next_topic(cfg)
    gh_output(created=topic["slug"], all_done="true" if done is None else "false")


if __name__ == "__main__":
    main()
