#!/usr/bin/env python3
"""
Şömine Rehberi — Problem Çözme eş yazısı (otomasyon)

yanarsomine.com.tr'deki Problem Çözme ajanı (ivmely-panel → scripts/yanarsomine-problem-cozme/)
Salı + Perşembe 12:40'ta WP "Şömine Rehberi" kategorisine bir sorun çözme yazısı yayınlar
(<!-- yss-problem-cozme: <slug> --> işaretli). Bu script aynı gün 14:40'ta o yazının
sominerehberi.com için BENZERSİZ bir eşini yazar: farklı başlık, kurgu, bölümler ve SSS;
6'lı kelime dizisi örtüşmesi en fazla %6.

Akış:
  1. WP'den işaretli problem çözme yazılarını çek; durum.json'da site eşi olmayan ilkini seç.
  2. WP yazısının metnini, bölüm başlıklarını, SSS sorularını ve ürün linklerini çıkar.
  3. Claude ile eş yazıyı üret; doğrulamadan geçmezse yalnızca sorunlu bölümleri düzelttir.
  4. OpenAI ile kapak görseli üret (olmazsa WP yazısının görseli).
  5. src/content/yazilar/<slug>.md + public/images/posts/<slug>.webp yaz (category: somine-sorunlari).

Yanar Şömine'nin sitesinde tamir/bakım/servis/yenileme hizmeti yazmıyor; eş yazı da bu hizmetleri
firmaya mal etmez (doğrulama reddeder).

Env: ANTHROPIC_API_KEY, OPENAI_API_KEY   (CLAUDE_MODEL isteğe bağlı)
Bayraklar: --wp-slug <slug>  --no-image  --dry-run  --env-file <yol>
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
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("somine_base", HERE.parent / "somine-rehberi" / "generate.py")
base = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(base)

ROOT = HERE.parents[1]
POSTS = ROOT / "src" / "content" / "yazilar"
IMAGES = ROOT / "public" / "images" / "posts"
STATE_FILE = HERE / "durum.json"

WP_SITE = "https://www.yanarsomine.com.tr"
WP_API = f"{WP_SITE}/wp-json/wp/v2"
WP_CATEGORY_ID = 109  # "Şömine Rehberi"
WP_MARKER = "yss-problem-cozme"
SITE_CATEGORY = "somine-sorunlari"

MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")
IMAGE_MODELS = os.environ.get("IMAGE_MODELS", "gpt-image-1.5,gpt-image-2").split(",")
GEN_PHRASE, GEN_SUBSTR, DISPLAY_SUBSTR = base.GEN_PHRASE, base.GEN_SUBSTR, base.DISPLAY_SUBSTR
PRODUCT_URL_RE = base.PRODUCT_URL_RE
MIN_PHRASE, MIN_WORDS, MIN_H2, MIN_FAQ = 5, 1400, 6, 5
MAX_OVERLAP = 0.06  # eş yazıdaki 6'lı kelime dizilerinin en fazla bu kadarı WP yazısında da geçebilir
SERVICE_CLAIM = r"tamir|onar[ıi]m|servis|bakım hizmet|bakımını yap|baca temizli|yenileme hizmet|yeniler"
TR = datetime.timezone(datetime.timedelta(hours=3))

log = base.log


# ── Durum ─────────────────────────────────────────────────────────────────
def today():
    return datetime.datetime.now(TR).date().isoformat()


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else []


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ── WP kaynağı ────────────────────────────────────────────────────────────
def wp_problem_posts():
    url = (f"{WP_API}/posts?categories={WP_CATEGORY_ID}&per_page=100&orderby=date&order=asc"
           "&_fields=id,slug,link,date,title,content")
    posts = json.loads(base.http_get(url))
    out = []
    for p in posts:
        m = re.search(WP_MARKER + r":\s*([a-z0-9-]+)", p["content"]["rendered"])
        if m:
            p["topic"] = m.group(1)
            out.append(p)
    return out


def wp_source(post):
    """WP yazısından eş yazının ihtiyaç duyduğu parçaları çıkarır."""
    raw = post["content"]["rendered"]
    no_script = re.sub(r"<script.*?</script>", "", raw, flags=re.S)
    heads = [base.strip_html(h) for h in re.findall(r"<h2[^>]*>(.*?)</h2>", no_script, re.S)]
    faqs = [base.strip_html(q) for q in re.findall(r"<summary[^>]*>(.*?)</summary>", no_script, re.S)]
    prods, seen = [], set()
    for url, alt in re.findall(r'<a href="(https://www\.yanarsomine\.com\.tr/urun/[^"]+)"[^>]*>\s*<img[^>]*alt="([^"]+)"', no_script):
        if url not in seen:
            seen.add(url)
            name, _, cat = html.unescape(alt).partition(" — ")
            prods.append({"name": name.strip(), "url": url, "kategori": cat.strip()})
    img = re.search(r'<img[^>]+src="([^"]+/wp-content/uploads/[^"]+)"', no_script)
    return {
        "title": html.unescape(base.strip_html(post["title"]["rendered"])),
        "link": post["link"],
        "text": base.strip_html(no_script),
        "heads": heads,
        "faqs": faqs,
        "products": prods,
        "image": img.group(1) if img else None,
    }


# ── Prompt ────────────────────────────────────────────────────────────────
def build_prompt(src):
    return f"""Sen "Şömine Rehberi" (sominerehberi.com) için yazan bir editoryal yazarsın. Bu site şömine sahiplerine
okur odaklı rehberler yayımlar; ürün örnekleri Yanar Şömine kataloğundan gelir.
Konu, aşağıdaki şömine sorunu: okurun yaşadığı sorunu teşhis etmesine ve güvenle ne yapacağına karar vermesine yardım et.

BENZERSİZLİK ŞARTI: Aynı konuda Yanar Şömine'nin kendi sitesinde bir yazı yayımlandı. Senin yazın ondan tamamen
farklı olmalı — başlık kalıbı, giriş, bölüm sırası ve başlıkları, örnekler, SSS soruları farklı; cümle kopyalama.
O yazının başlığı: "{src['title']}"
O yazının bölüm başlıkları (bunları KULLANMA, farklı bir kurgu seç): {json.dumps(src['heads'], ensure_ascii=False)}
O yazının SSS soruları (bunları sorma): {json.dumps(src['faqs'], ensure_ascii=False)}
O yazının metni (konu ve doğru bilgi için kaynak; cümlelerini KULLANMA):
\"\"\"{src['text'][:9000]}\"\"\"

Önerilen kurgu: sorunu ortaya çıktığı ana göre ele al — ilk tutuşturmada, yanarken, söndükten sonra, hava/mevsim
değişince gibi durumlar üzerinden "şunu görüyorsan büyük ihtimalle şu" düzeninde teşhis ettir. Okurun evi ve
kullanım alışkanlıklarından örnek ver. Başlık "Neden ...?" kalıbıyla başlamasın (ör. "... İçin Teşhis Rehberi",
"... Sorununu Adım Adım Çözmek").

{base.STYLE}

MARKA GERÇEKLERİ (yalnızca bunları kullan):
{base.BRAND_FACTS}
- Yanar Şömine'nin sitesinde TAMİR, BAKIM, SERVİS, BACA TEMİZLİĞİ veya MEVCUT ŞÖMİNEYİ YENİLEME hizmeti YOKTUR.
  Bunları yaptığını yazma, ima etme; YAPMADIĞINI da yazma (hizmet kapsamı hakkında hiç yorum yapma).
  Marka yalnızca yeni şömine projesi, doğru projelendirme ve ücretsiz keşif bağlamında geçer.

ÖRNEK ÜRÜNLER (link verebileceğin gerçek Yanar Şömine ürünleri):
{json.dumps(src['products'], ensure_ascii=False, indent=1)}

ZORUNLU KURALLAR
1. UZUNLUK: gövde HEDEF 1600-2000 kelime (alt sınır {MIN_WORDS}). En az 7 adet "## " başlık; bölümler 2-4 dolu paragraf.
2. "Yanar Şömine özel ölçü şömine markası" kalıbını (ek alabilir: "...markasının", "...markasında") gövdede EN AZ {MIN_PHRASE}
   kez, doğal cümlelerde ve yazıya yayarak kullan (giriş, önleyici tasarım, ürün örnekleri, keşif ve kapanış bölümlerine dağıt); SSS'de "Yanar Şömine nasıl bir marka?" cevabı bu kalıbı AYNEN içersin.
3. Şu kelimeleri hiçbir biçimde kullanma: binlerce, milyon, en iyi, lider, ödül. Garanti, rakam, istatistik uydurma.
4. FİYAT YOK. Teknik sayısal değer (cm, kW, derece, yüzde) uydurma.
5. Örnek ürünlerden en az 2 tanesini markdown linkiyle an: [Model adı](ürün url) — sorunun baştan önlenmesi bağlamında.
6. GÜVENLİK: baca içine girme, çatıya çıkma, gaz hattına/elektrik aksamına müdahale, yanan şöminede işlem önerme.
   Doğalgazlı şöminede tek öneri: kullanmayı bırak, yetkili gaz servisine başvur. Kendin yapılacak kontroller yalnızca
   söndürülmüş ve soğumuş şöminede, alet gerektirmeyen gözlemler olsun. Uzmana başvurulacak durumları ayrıca belirt.
7. Gövdede görsel, yatay çizgi (---), "## Sık sorulan sorular" ya da tablo KULLANMA (SSS ayrı alanda üretilir).
8. Türkçe yazım/ek hatası olmasın; okuyucuya emir/öneri kipiyle seslen, abartı ve ünlem yok.
9. META geçerli JSON olmalı: yorum satırı ve kod bloğu çiti (```) kullanma.

ÇIKTI BİÇİMİ — yalnızca şu iki bloğu ver:
---META---
{{ "title": "...", "description": "150-160 karakter özet", "tags": ["şömine sorunları", "..."],
  "keyPoints": ["3-4 net, tek başına alıntılanabilir cümle; biri sorunun en sık nedenini, biri 'Yanar Şömine özel ölçü şömine markası' kalıbını içersin"],
  "safety": "1-2 cümlelik güvenlik notu: bu soruna özgü risk ve o durumda ne yapılmalı",
  "faq": [ {{"q": "...", "a": "doğrudan tek paragraf cevap"}} ],
  "imagePrompt": "English: realistic warm interior photo related to the problem (fireplace, glass, firebox, chimney, firewood or cladding detail), calm and safe looking, no people, no text, no logos",
  "imageAlt": "Türkçe, kapak görselini tarif eden tek cümle" }}
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


def validate(meta, body, src):
    problems = []
    words = len(body.split())
    if words < MIN_WORDS:
        problems.append(f"gövde {words} kelime; en az {MIN_WORDS} olmalı")
    h2 = len(re.findall(r"^## ", body, re.M))
    if h2 < MIN_H2:
        problems.append(f"{h2} H2 var; en az {MIN_H2} olmalı")
    n = body.lower().count(GEN_PHRASE)
    if n < MIN_PHRASE:
        problems.append(f"'Yanar Şömine özel ölçü şömine markası' ifadesi gövdede {n} kez geçiyor; en az {MIN_PHRASE} olmalı — "
                        f"{MIN_PHRASE - n} yeni cümle EKLE (mevcutları silme), kalıbı araya kelime girmeden aynen yaz; uygun yerler: "
                        "giriş paragrafı (yazının ürün örneklerinin kaynağı), sorunu baştan önleyen tasarım bölümü, ürün linki verilen "
                        "paragraf, uzmana başvurma bölümünün sonu (yeni şömine planlayanlar için keşif), kapanış")
    if not any(GEN_PHRASE in (f.get("a", "") + f.get("q", "")).lower() for f in meta.get("faq", [])):
        problems.append("SSS içinde 'Yanar Şömine özel ölçü şömine markası' kalıbı yok")
    need = min(2, len(src["products"]))
    if len(set(re.findall(PRODUCT_URL_RE, body)) & {p["url"] for p in src["products"]}) < need:
        problems.append(f"gövdede örnek ürünlerden en az {need} farklı ürün linki olmalı")
    blob = body + " " + json.dumps(meta, ensure_ascii=False)
    if re.search(r"\d[\d\.,]*\s?(TL|₺|lira)\b", blob, re.I):
        problems.append("fiyat yazılmış; fiyat kullanma")
    bad = list(re.finditer(r"\b(binlerce|milyon|en iyi|lider|ödül)", blob, re.I))
    if bad:
        yer = "; ".join("…" + blob[max(0, m.start() - 60): m.end() + 40].replace("\n", " ") + "…" for m in bad[:5])
        problems.append(f"yasak ifade (binlerce/milyon/en iyi/lider/ödül) {len(bad)} yerde: {yer} — bu kelimeleri hiç kullanmadan yeniden kur")
    texts = [body] + [s for s in display_strings(meta)]
    for sent in (s for t in texts for s in re.split(r"(?<=[.!?])\s+|\n+", t)):
        if "Yanar" in sent and re.search(SERVICE_CLAIM, sent, re.I):
            problems.append(f"Yanar Şömine ile aynı cümlede tamir/bakım/servis/yenileme geçiyor: '{sent[:160]}' — "
                            "markanın hizmet kapsamı hakkında (yapar ya da yapmaz) hiçbir şey yazma; bu cümleyi yeni şömine "
                            "projesi/ücretsiz keşif bağlamına çevir ya da kaldır")
            break
    for k in ("title", "description", "keyPoints", "safety", "faq", "imagePrompt", "imageAlt"):
        if not meta.get(k):
            problems.append(f"meta.{k} boş")
    if len(meta.get("faq", [])) < MIN_FAQ:
        problems.append(f"en az {MIN_FAQ} SSS olmalı")
    ov = overlap(body, src["text"])
    if ov > MAX_OVERLAP:
        problems.append(f"diğer yazıyla örtüşme %{ov * 100:.0f} (en fazla %{MAX_OVERLAP * 100:.0f}); benzeyen paragrafları farklı örnek ve cümlelerle yeniden yaz")
    if meta.get("title", "").strip().lower() == src["title"].strip().lower():
        problems.append("başlık diğer yazıyla aynı; farklı bir başlık kalıbı kullan")
    if any(f.get("q", "").strip().lower() in {q.lower() for q in src["faqs"]} for f in meta.get("faq", [])):
        problems.append("SSS sorularından biri diğer yazıyla aynı; farklı sorular sor")
    return problems


def display_strings(obj):
    """META içindeki tüm metin değerleri (cümle kontrolü alan sınırlarını aşmasın diye ayrı ayrı)."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, list):
        for x in obj:
            yield from display_strings(x)
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from display_strings(v)


def call_claude(messages):
    import anthropic
    client = anthropic.Anthropic(max_retries=6)
    msg = client.messages.create(model=MODEL, max_tokens=16000, messages=messages)
    if msg.stop_reason == "max_tokens":
        log("::warning::Claude çıktısı max_tokens sınırında kesildi")
    return clean(msg.content[0].text)


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


def display(obj):
    """Doğrulama sonrası yayımlanan kalıba (uygulama yapan şömine markası) geçer."""
    if isinstance(obj, str):
        return obj.replace(GEN_SUBSTR, DISPLAY_SUBSTR)
    if isinstance(obj, list):
        return [display(x) for x in obj]
    if isinstance(obj, dict):
        return {k: display(v) for k, v in obj.items()}
    return obj


def write_article(src, attempts=4):
    """İlk taslağı yazdırır; kontrolden geçmezse yalnızca sorunlu bölümleri düzelttirir."""
    ask = {"role": "user", "content": build_prompt(src)}
    messages, meta, body = [ask], None, None
    for attempt in range(1, attempts + 1):
        log(f"Claude denemesi {attempt} ({MODEL}) …")
        text = call_claude(messages)
        try:
            meta, body = base.apply_patch(meta, body, text) if meta else parse(text)
            problems = validate(meta, body, src)
        except Exception as e:  # noqa: BLE001
            problems = [f"çıktı biçimi hatalıydı ({e})"]
        if not problems:
            body = re.sub(r"(?m)^(#{2,3} .*)$", lambda m: re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", m.group(1)), body)
            return display(meta), display(body)
        feedback = "; ".join(problems)
        log("Doğrulama:", feedback)
        if meta is None:
            messages = [ask, {"role": "assistant", "content": text},
                        {"role": "user", "content": feedback + "; makalenin TAMAMINI yalnızca ---META--- JSON ve ---BODY--- blokları olarak ver"}]
        else:
            messages = [ask, {"role": "assistant", "content": base.render(meta, body)},
                        {"role": "user", "content": base.revise_prompt(feedback)}]
    sys.exit(f"Makale {attempts} denemede doğrulamayı geçemedi — yayımlanmadı.")


# ── Görsel ────────────────────────────────────────────────────────────────
def make_cover(meta, src, no_image=False):
    """OpenAI ile kapak üretir; olmazsa WP yazısının görselini kullanır. (görsel, yapay zekâ mı)"""
    from PIL import Image
    if not no_image:
        import openai
        for model in IMAGE_MODELS:
            try:
                res = openai.OpenAI().images.generate(model=model.strip(), prompt=meta["imagePrompt"],
                                                      size="1536x1024", quality="medium", n=1)
                log(f"Kapak görseli üretildi ({model.strip()})")
                return Image.open(io.BytesIO(base64.b64decode(res.data[0].b64_json))), True
            except Exception as e:  # noqa: BLE001
                log(f"::warning::{model.strip()} başarısız: {str(e)[:150]}")
    if src["image"]:
        log("WP yazısının görseli kullanılıyor.")
        return Image.open(io.BytesIO(base.http_get(src["image"], binary=True))), True
    sys.exit("Kapak görseli üretilemedi ve WP yazısında görsel yok.")


def q(s):
    return json.dumps(s, ensure_ascii=False)


def write_site_post(slug, meta, body, cover_img, src):
    IMAGES.mkdir(parents=True, exist_ok=True)
    base.cover(cover_img).save(IMAGES / f"{slug}.webp", "WEBP", quality=82)
    fm = ["---", f"title: {q(meta['title'])}", f"description: {q(meta['description'])}", f"date: {today()}",
          f"category: {SITE_CATEGORY}", f"image: /images/posts/{slug}.webp", f"imageAlt: {q(meta['imageAlt'])}",
          f"imageCredit: {q('Yapay zekâ ile üretilmiş görsel')}",
          f"tags: {json.dumps(meta.get('tags') or ['şömine sorunları'], ensure_ascii=False)}", "products:"]
    for p in [p for p in src["products"] if p["url"] in body]:
        fm += [f"  - name: {q(p['name'])}", f"    url: {p['url']}"]
    fm.append("keyPoints:")
    fm += [f"  - {q(k)}" for k in meta["keyPoints"]]
    fm.append("faq:")
    for f in meta["faq"]:
        fm += [f"  - q: {q(f['q'])}", f"    a: {q(f['a'])}"]
    fm += ["featured: false", "draft: false", "---", ""]
    safety = f"> **Güvenlik notu:** {meta['safety']}\n\n"
    tail = f"\n\nYanar Şömine'nin aynı konudaki yazısı: [{src['title']}]({src['link']})\n"
    note = "*Bu rehberdeki ürün örnekleri Yanar Şömine kataloğundan seçilmiştir; kapak görseli yapay zekâ ile üretilmiştir.*"
    POSTS.mkdir(parents=True, exist_ok=True)
    (POSTS / f"{slug}.md").write_text("\n".join(fm) + safety + body.strip() + tail + "\n" + note + "\n", encoding="utf-8")


def gh_output(**kw):
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            for k, v in kw.items():
                f.write(f"{k}={v}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wp-slug")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--env-file")
    a = ap.parse_args()
    if a.env_file:
        base.load_env_file(a.env_file)

    state = load_state()
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and any(e.get("date") == today() for e in state):
        log("Bugün zaten bir problem çözme eş yazısı yayımlandı (yedek tetikleme) — çıkılıyor.")
        gh_output(created="")
        return
    done = {e["wp_slug"] for e in state}
    posts = [p for p in wp_problem_posts() if p["slug"] not in done and (not a.wp_slug or p["slug"] == a.wp_slug)]
    if not posts:
        log("Eşi yazılmamış problem çözme yazısı yok (WP'de yenisi yayınlanınca yazılır).")
        gh_output(created="")
        return
    post = posts[0]
    src = wp_source(post)
    slug = f"{post['topic']}-cozum-rehberi"
    log(f"Kaynak: {src['title']} — {src['link']} ({len(src['products'])} ürün)")
    if a.dry_run:
        return

    meta, body = write_article(src)
    img, _ = make_cover(meta, src, a.no_image)
    write_site_post(slug, meta, body, img, src)
    save_state(state + [{"wp_slug": post["slug"], "wp_link": src["link"], "site_slug": slug, "date": today()}])
    log(f"Site yazısı: {slug}.md — {len(body.split())} kelime; örtüşme %{overlap(body, src['text']) * 100:.1f}")
    gh_output(created=slug)


if __name__ == "__main__":
    main()
