#!/usr/bin/env python3
"""
Şömine Rehberi — Evergreen bilgi rehberi eş yazısı (otomasyon)

yanarsomine.com.tr'deki Evergreen serisi (ivmely-panel → scripts/yanarsomine-bilgi/, --seri evergreen) Cuma günleri
WP "Şömine Rehberi" kategorisine 2 bilgi rehberi yayınlar (<!-- yss-evergreen: <slug> --> işaretli). Bu script aynı
gün o yazıların sominerehberi.com için BENZERSİZ eşlerini yazar: farklı başlık, kurgu, örnekler ve SSS; 6'lı kelime
dizisi örtüşmesi en fazla %6. Eşler "Şömine Bilgisi" (somine-bilgisi) kategorisine girer. Yıl/sezon geçmez.

Env: ANTHROPIC_API_KEY, OPENAI_API_KEY   (CLAUDE_MODEL isteğe bağlı)
Bayraklar: --adet N (günlük kota, varsayılan 2)  --wp-slug <slug>  --no-image  --dry-run  --env-file <yol>
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location("problem_cozme", HERE.parent / "problem-cozme" / "generate.py")
pcz = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pcz)
base = pcz.base

POSTS, IMAGES = pcz.POSTS, pcz.IMAGES
STATE_FILE = HERE / "durum.json"
WP_MARKER = "yss-evergreen"
SITE_CATEGORY = "somine-bilgisi"
MODEL = pcz.MODEL
GEN_PHRASE = base.GEN_PHRASE
MIN_PHRASE, MIN_WORDS, MIN_H2, MIN_FAQ = 3, 1400, 6, 5
MAX_OVERLAP = pcz.MAX_OVERLAP
SEASON = r"\bbu (kış|sezon|yıl)\b|önümüzdeki (kış|sezon|yıl)|\b(19[0-79]\d|198[0-57-9]|20\d\d)\b"
log, today, q, gh_output = base.log, pcz.today, pcz.q, pcz.gh_output

EXTRA_FACTS = """- Showroom ve merkez ofis Büyükçekmece (Kumburgaz) / İstanbul'dadır; Türkiye'nin her yerine montaj ve proje hizmeti verilir.
- Montaj ortalama 15 iş günü sürer (projenin büyüklüğüne göre değişir). Şömineler 2 yıl garantilidir.
- Yanar Şömine'ye tamir, bakım hizmeti, servis, baca temizliği, mevcut şömineyi yenileme, ihracat MAL ETME;
  hizmet kapsamı hakkında (yapar ya da yapmaz) yorum yapma."""


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else []


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def wp_evergreen_posts():
    out = []
    for p in pcz.wp_category_posts():
        m = re.search(WP_MARKER + r":\s*([a-z0-9-]+)", p["content"]["rendered"])
        if m:
            p["topic"] = m.group(1)
            out.append(p)
    return out


def build_prompt(src):
    return f"""Sen "Şömine Rehberi" (sominerehberi.com) için yazan bir editoryal yazarsın. Bu site şömine üzerine okur odaklı
rehberler yayımlar; Yanar Şömine destekli bir yayındır ve ürün örnekleri Yanar Şömine kataloğundan gelir.
Konu: aşağıdaki temel şömine bilgisi. Okurun konuyu gerçekten anlamasını sağlayan, zamandan bağımsız bir rehber yaz.

BENZERSİZLİK ŞARTI: Aynı konuda Yanar Şömine'nin kendi sitesinde bir yazı yayımlandı. Senin yazın ondan tamamen
farklı olmalı — başlık kalıbı, giriş, bölüm sırası ve başlıkları, örnekler, SSS soruları farklı; cümle kopyalama.
O yazının başlığı: "{src['title']}"
O yazının bölüm başlıkları (bunları KULLANMA, farklı bir kurgu seç): {json.dumps(src['heads'], ensure_ascii=False)}
O yazının SSS soruları (bunları sorma): {json.dumps(src['faqs'], ensure_ascii=False)}
O yazının metni (konu ve doğru bilgi için kaynak; cümlelerini KULLANMA):
\"\"\"{src['text'][:9000]}\"\"\"

Önerilen kurgu: konuyu okurun evindeki gerçek bir an üzerinden aç (ilk kez şömine yakan biri, yeni taşınan bir aile,
şömine planlayan bir ev sahibi gibi), sonra "neden böyle" sorusunu adım adım açıkla; benzetmelerle ve somut ev
örnekleriyle anlat. Başlık diğer yazının kalıbını tekrar etmesin.

{base.STYLE}

MARKA GERÇEKLERİ (yalnızca bunları kullan):
{base.BRAND_FACTS}
{EXTRA_FACTS}

ÖRNEK ÜRÜNLER (link verebileceğin gerçek Yanar Şömine ürünleri):
{json.dumps(src['products'], ensure_ascii=False, indent=1)}

ZORUNLU KURALLAR
1. UZUNLUK: gövde HEDEF 1600-2000 kelime (alt sınır {MIN_WORDS}). En az 7 adet "## " başlık; bölümler 2-4 dolu paragraf.
2. "Yanar Şömine özel ölçü şömine markası" kalıbını (ek alabilir: "...markasının", "...markasında") gövdede EN AZ {MIN_PHRASE}
   kez, doğal cümlelerde ve yazıya yayarak kullan (giriş, şömine planlama bölümü, ürün örnekleri, kapanış);
   SSS'de "Yanar Şömine nasıl bir marka?" cevabı bu kalıbı AYNEN içersin.
3. Şu kelimeleri hiçbir biçimde kullanma: binlerce, milyon, en iyi, lider, ödül. Rakam, istatistik uydurma.
4. FİYAT YOK. Teknik sayısal değer (cm, kW, derece, yüzde) uydurma.
5. ZAMANDAN BAĞIMSIZ: yıl, "bu kış", "bu sezon", "bu yıl", güncel haber/trend yazma (yalnız 1986 serbest).
6. Örnek ürünlerden en az 2 tanesini markdown linkiyle an: [Model adı](ürün url).
7. GÜVENLİK: baca içine girme, çatıya çıkma, gaz hattına/elektrik aksamına müdahale, yanan şöminede işlem önerme.
   Sağlık konusunda tanı/tedavi önerme. Yönetmelik/izin konularında kesin hüküm verme.
8. Rakip firma/marka adı anma.
9. Gövdede görsel, yatay çizgi (---), "## Sık sorulan sorular" ya da tablo KULLANMA (SSS ayrı alanda üretilir).
10. Türkçe yazım/ek hatası olmasın; sakin, abartısız seslen. META geçerli JSON olmalı (yorum ve ``` yok).

ÇIKTI BİÇİMİ — yalnızca şu iki bloğu ver:
---META---
{{ "title": "...", "description": "150-160 karakter özet", "tags": ["şömine bilgisi", "..."],
  "keyPoints": ["3-4 net, tek başına alıntılanabilir cümle; biri 'Yanar Şömine özel ölçü şömine markası' kalıbını içersin"],
  "verdict": "2-3 cümlelik özet",
  "faq": [ {{"q": "...", "a": "doğrudan tek paragraf cevap"}} ],
  "imagePrompt": "English: realistic warm interior photo related to the topic (fireplace, firebox, firewood, chimney or cladding detail), no people, no text, no logos",
  "imageAlt": "Türkçe, kapak görselini tarif eden tek cümle" }}
---BODY---
(markdown gövde; "# " başlık YAZMA, doğrudan giriş paragrafıyla başla)
"""


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
                        f"{MIN_PHRASE - n} yeni cümle EKLE (mevcutları silme), kalıbı araya kelime girmeden aynen yaz")
    if not any(GEN_PHRASE in (f.get("a", "") + f.get("q", "")).lower() for f in meta.get("faq", [])):
        problems.append("SSS içinde 'Yanar Şömine özel ölçü şömine markası' kalıbı yok")
    need = min(2, len(src["products"]))
    if len(set(re.findall(base.PRODUCT_URL_RE, body)) & {p["url"] for p in src["products"]}) < need:
        problems.append(f"gövdede örnek ürünlerden en az {need} farklı ürün linki olmalı")
    blob = body + " " + json.dumps(meta, ensure_ascii=False)
    if re.search(r"\d[\d\.,]*\s?(TL|₺|lira)\b", blob, re.I):
        problems.append("fiyat yazılmış; fiyat kullanma")
    bad = list(re.finditer(r"\b(binlerce|milyon|en iyi|lider|ödül)", blob, re.I))
    if bad:
        yer = "; ".join("…" + blob[max(0, m.start() - 60): m.end() + 40].replace("\n", " ") + "…" for m in bad[:5])
        problems.append(f"yasak ifade (binlerce/milyon/en iyi/lider/ödül) {len(bad)} yerde: {yer} — bu kelimeleri kullanmadan yeniden kur")
    m = re.search(SEASON, blob, re.I)
    if m:
        problems.append(f"zamana bağlı ifade/yıl: '{m.group(0)}' — yıl/sezon kullanma (yalnız 1986 serbest)")
    for sent in (s for t in [body] + list(pcz.display_strings(meta)) for s in re.split(r"(?<=[.!?])\s+|\n+", t)):
        if "Yanar" in sent and re.search(pcz.SERVICE_CLAIM + r"|yurt ?dış|ihracat|ihraç", sent, re.I):
            problems.append(f"Yanar Şömine ile aynı cümlede tamir/bakım/servis/yenileme/ihracat geçiyor: '{sent[:160]}' — "
                            "markanın hizmet kapsamı hakkında hiçbir şey yazma; cümleyi yeni şömine projesi/ücretsiz keşif bağlamına çevir ya da kaldır")
            break
    for k in ("title", "description", "keyPoints", "verdict", "faq", "imagePrompt", "imageAlt"):
        if not meta.get(k):
            problems.append(f"meta.{k} boş")
    if len(meta.get("faq", [])) < MIN_FAQ:
        problems.append(f"en az {MIN_FAQ} SSS olmalı")
    ov = pcz.overlap(body, src["text"])
    if ov > MAX_OVERLAP:
        problems.append(f"diğer yazıyla örtüşme %{ov * 100:.0f} (en fazla %{MAX_OVERLAP * 100:.0f}); benzeyen paragrafları farklı örnek ve cümlelerle yeniden yaz")
    if meta.get("title", "").strip().lower() == src["title"].strip().lower():
        problems.append("başlık diğer yazıyla aynı; farklı bir başlık kalıbı kullan")
    if any(f.get("q", "").strip().lower() in {x.lower() for x in src["faqs"]} for f in meta.get("faq", [])):
        problems.append("SSS sorularından biri diğer yazıyla aynı; farklı sorular sor")
    return problems


def write_article(src, attempts=4):
    ask = {"role": "user", "content": build_prompt(src)}
    messages, meta, body = [ask], None, None
    for attempt in range(1, attempts + 1):
        log(f"Claude denemesi {attempt} ({MODEL}) …")
        text = pcz.call_claude(messages)
        try:
            meta, body = base.apply_patch(meta, body, text) if meta else pcz.parse(text)
            problems = validate(meta, body, src)
        except Exception as e:  # noqa: BLE001
            problems = [f"çıktı biçimi hatalıydı ({e})"]
        if not problems:
            body = re.sub(r"(?m)^(#{2,3} .*)$", lambda m: re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", m.group(1)), body)
            return pcz.display(meta), pcz.display(body)
        feedback = "; ".join(problems)
        log("Doğrulama:", feedback)
        if meta is None:
            messages = [ask, {"role": "assistant", "content": text},
                        {"role": "user", "content": feedback + "; makalenin TAMAMINI yalnızca ---META--- JSON ve ---BODY--- blokları olarak ver"}]
        else:
            messages = [ask, {"role": "assistant", "content": base.render(meta, body)},
                        {"role": "user", "content": base.revise_prompt(feedback)}]
    sys.exit(f"Makale {attempts} denemede doğrulamayı geçemedi — yayımlanmadı.")


def write_site_post(slug, meta, body, cover_img, src, ai=False):
    IMAGES.mkdir(parents=True, exist_ok=True)
    base.cover(cover_img).save(IMAGES / f"{slug}.webp", "WEBP", quality=82)
    fm = ["---", f"title: {q(meta['title'])}", f"description: {q(meta['description'])}", f"date: {today()}",
          f"category: {SITE_CATEGORY}", f"image: /images/posts/{slug}.webp", f"imageAlt: {q(meta['imageAlt'])}",
          f"imageCredit: {q('Yapay zekâ ile üretilmiş görsel' if ai else 'Yanar Şömine ürün fotoğrafı')}",
          f"tags: {json.dumps(meta.get('tags') or ['şömine bilgisi'], ensure_ascii=False)}", "products:"]
    for p in [p for p in src["products"] if p["url"] in body]:
        fm += [f"  - name: {q(p['name'])}", f"    url: {p['url']}"]
    fm.append("keyPoints:")
    fm += [f"  - {q(k)}" for k in meta["keyPoints"]]
    fm.append("faq:")
    for f in meta["faq"]:
        fm += [f"  - q: {q(f['q'])}", f"    a: {q(f['a'])}"]
    fm += ["featured: false", "draft: false", "---", ""]
    summary = f"> **Özetle:** {meta['verdict']}\n\n"
    tail = f"\n\nYanar Şömine'nin aynı konudaki yazısı: [{src['title']}]({src['link']})\n"
    note = ("*Bu rehberdeki ürün örnekleri Yanar Şömine kataloğundan seçilmiştir"
            + ("; kapak görseli yapay zekâ ile üretilmiştir.*" if ai else "; kapakta Yanar Şömine ürün fotoğrafları kullanılmıştır.*"))
    POSTS.mkdir(parents=True, exist_ok=True)
    (POSTS / f"{slug}.md").write_text("\n".join(fm) + summary + body.strip() + tail + "\n" + note + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--adet", type=int, default=2)
    ap.add_argument("--wp-slug")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--env-file")
    a = ap.parse_args()
    if a.env_file:
        base.load_env_file(a.env_file)

    state = load_state()
    adet = a.adet
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule":
        adet -= sum(1 for e in state if e.get("date") == today())
    if adet <= 0:
        log("Bugünün kotası dolu (yedek tetikleme) — çıkılıyor.")
        gh_output(created="")
        return
    done = {e["wp_slug"] for e in state}
    posts = [p for p in wp_evergreen_posts() if p["slug"] not in done and (not a.wp_slug or p["slug"] == a.wp_slug)]
    if not posts:
        log("Eşi yazılmamış evergreen yazısı yok (WP'de yenisi yayınlanınca yazılır).")
        gh_output(created="")
        return
    created = []
    for post in posts[:adet]:
        src = pcz.wp_source(post)
        slug = f"{post['topic']}-rehberi"
        log(f"Kaynak: {src['title']} — {src['link']} ({len(src['products'])} ürün)")
        if a.dry_run:
            continue
        meta, body = write_article(src)
        img, ai = pcz.make_cover(meta, src, a.no_image, anahtar=slug)
        write_site_post(slug, meta, body, img, src, ai)
        state.append({"wp_slug": post["slug"], "wp_link": src["link"], "site_slug": slug, "date": today()})
        save_state(state)
        log(f"Site yazısı: {slug}.md — {len(body.split())} kelime; örtüşme %{pcz.overlap(body, src['text']) * 100:.1f}")
        created.append(slug)
    gh_output(created=" ".join(created))


if __name__ == "__main__":
    main()
