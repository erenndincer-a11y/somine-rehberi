#!/usr/bin/env python3
"""
Şömine Rehberi — "En İyiler" eş yazısı (otomasyon)

yanarsomine.com.tr'deki En İyiler ajanları (ivmely-panel → scripts/yanarsomine-en-iyiler/, seriler: tip-model,
kullanim) Salı + Perşembe akşam WP "Şömine Rehberi" kategorisine bir liste yazısı yayınlar
(<!-- yss-en-iyiler-<seri>: <slug> --> işaretli). Bu script bir saat sonra o yazının sominerehberi.com için
BENZERSİZ bir eşini yazar: farklı başlık, kurgu, seçenek anlatımı ve SSS; 6'lı kelime dizisi örtüşmesi en fazla %6.
Eş yazılar "En İyiler" (en-iyiler) kategorisine girer; ItemList şeması frontmatter'daki listItems'tan üretilir.

Env: ANTHROPIC_API_KEY, OPENAI_API_KEY   (CLAUDE_MODEL isteğe bağlı)
Bayraklar: --series <tip-model|kullanim>  --wp-slug <slug>  --no-image  --dry-run  --env-file <yol>
"""
import argparse
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pcz = _load("problem_cozme", HERE.parent / "problem-cozme" / "generate.py")
base = pcz.base

ROOT = HERE.parents[1]
POSTS, IMAGES = pcz.POSTS, pcz.IMAGES
STATE_FILE = HERE / "durum.json"
SITE_CATEGORY = "en-iyiler"
MODEL = pcz.MODEL
GEN_PHRASE = base.GEN_PHRASE
MIN_PHRASE, MIN_WORDS, MIN_ITEMS, MIN_FAQ = 5, 1400, 5, 5
MAX_OVERLAP = pcz.MAX_OVERLAP
log, today, q, gh_output = base.log, pcz.today, pcz.q, pcz.gh_output


def load_state():
    return json.loads(STATE_FILE.read_text(encoding="utf-8")) if STATE_FILE.exists() else []


def save_state(state):
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def wp_list_posts(series):
    url = (f"{pcz.WP_API}/posts?categories={pcz.WP_CATEGORY_ID}&per_page=100&orderby=date&order=asc"
           "&_fields=id,slug,link,date,title,content")
    out = []
    for p in json.loads(base.http_get(url)):
        m = re.search(rf"yss-en-iyiler-{series}:\s*([a-z0-9-]+)", p["content"]["rendered"])
        if m:
            p["topic"] = m.group(1)
            out.append(p)
    return out


def build_prompt(src):
    items = [h for h in src["heads"] if re.match(r"\d+\.", h)]
    return f"""Sen "Şömine Rehberi" (sominerehberi.com) için yazan bir editoryal yazarsın. Bu site şömine üzerine okur odaklı
rehberler yayımlar; Yanar Şömine destekli bir yayındır ve ürün örnekleri Yanar Şömine kataloğundan gelir.
Konu: aşağıdaki "en iyiler" listesinin konusu. Okurun kendi evine uygun seçeneği bulmasına yardım eden dürüst bir liste yaz.

BENZERSİZLİK ŞARTI: Aynı konuda Yanar Şömine'nin kendi sitesinde bir liste yayımlandı. Senin yazın ondan tamamen
farklı olmalı — başlık kalıbı, giriş, bölüm başlıkları, seçeneklerin sırası ve anlatımı, örnekler, SSS soruları farklı; cümle kopyalama.
O yazının başlığı: "{src['title']}"
O yazının seçenekleri (sırası): {json.dumps(items, ensure_ascii=False)}
O yazının bölüm başlıkları (bunları KULLANMA): {json.dumps(src['heads'], ensure_ascii=False)}
O yazının SSS soruları (bunları sorma): {json.dumps(src['faqs'], ensure_ascii=False)}
O yazının metni (konu ve doğru bilgi için kaynak; cümlelerini KULLANMA):
\"\"\"{src['text'][:9000]}\"\"\"

Önerilen kurgu: okur profilinden başla ("şöyle bir evin varsa…") ve seçenekleri kendi sıranla, numaralı "## 1. <seçenek>"
başlıklarıyla ver (5-8 seçenek; aynı seçenekleri kullanabilir ya da gerekçesiyle birini değiştirebilirsin, ama sıra ve
adlandırma farklı olsun). Her seçenekte "kimin evinde parlar", "nerede zorlanır" ve "seçmeden önce sorulacak soru" olsun.
Listenin öncesinde değerlendirme ölçütlerini kısa bir bölümde açıkla; sonunda okura kısa bir karar akışı ver.
Başlık "En İyi <sayı>" kalıbıyla BAŞLAMASIN (ör. "<Konu>: Hangi Ev İçin Hangi Seçenek? 7 Seçenekli Liste").

{base.STYLE}

MARKA GERÇEKLERİ (yalnızca bunları kullan):
{base.BRAND_FACTS}
- Yanar Şömine'nin sitesinde TAMİR, BAKIM, SERVİS, BACA TEMİZLİĞİ veya MEVCUT ŞÖMİNEYİ YENİLEME hizmeti YOKTUR.
  Bunları yaptığını yazma, ima etme; YAPMADIĞINI da yazma (hizmet kapsamı hakkında hiç yorum yapma).

ÖRNEK ÜRÜNLER (link verebileceğin gerçek Yanar Şömine ürünleri):
{json.dumps(src['products'], ensure_ascii=False, indent=1)}

ZORUNLU KURALLAR
1. UZUNLUK: gövde HEDEF 1600-2000 kelime (alt sınır {MIN_WORDS}). Numaralı seçenekler dışında en az 3 "## " başlık daha.
2. "Yanar Şömine özel ölçü şömine markası" kalıbını (ek alabilir: "...markasının", "...markasında") gövdede EN AZ {MIN_PHRASE}
   kez, doğal cümlelerde ve yazıya yayarak kullan (giriş, ölçütler, ürün örneği verilen seçenekler, karar akışı, kapanış);
   SSS'de "Yanar Şömine nasıl bir marka?" cevabı bu kalıbı AYNEN içersin.
3. Şu kelimeleri kullanma: binlerce, milyon, lider, ödül. Garanti, rakam, istatistik uydurma. "En iyi" ifadesini
   yalnızca giriş paragrafında ve SSS'de, okurun sorusunu karşılamak için kullan.
4. FİYAT YOK. Teknik sayısal değer (cm, kW, derece, yüzde) uydurma; bütçeyi göreli anlat.
5. Örnek ürünlerden en az 2 tanesini markdown linkiyle an: [Model adı](ürün url).
6. Rakip firma/marka adı anma. Yönetmelik/izin, baca ve gaz güvenliğinde kesin hüküm verme.
7. Gövdede görsel, yatay çizgi (---), "## Sık sorulan sorular" ya da tablo KULLANMA (SSS ayrı alanda üretilir).
8. Türkçe yazım/ek hatası olmasın; okuyucuya sakin, abartısız seslen.
9. META geçerli JSON olmalı: yorum satırı ve kod bloğu çiti (```) kullanma.

ÇIKTI BİÇİMİ — yalnızca şu iki bloğu ver:
---META---
{{ "title": "...", "description": "150-160 karakter özet", "tags": ["en iyiler", "..."],
  "keyPoints": ["3-4 net, tek başına alıntılanabilir cümle; biri listenin öne çıkan seçeneğini, biri 'Yanar Şömine özel ölçü şömine markası' kalıbını içersin"],
  "verdict": "2-3 cümlelik sonuç: hangi durumda hangi seçenek",
  "faq": [ {{"q": "...", "a": "doğrudan tek paragraf cevap"}} ],
  "imagePrompt": "English: realistic warm photo of the kind of fireplace the list is about, no people, no text, no logos",
  "imageAlt": "Türkçe, kapak görselini tarif eden tek cümle" }}
---BODY---
(markdown gövde; "# " başlık YAZMA, doğrudan giriş paragrafıyla başla)
"""


def list_items(body):
    return [re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", m).strip() for m in re.findall(r"(?m)^## \d+\.\s*(.+)$", body)]


def validate(meta, body, src):
    problems = []
    words = len(body.split())
    if words < MIN_WORDS:
        problems.append(f"gövde {words} kelime; en az {MIN_WORDS} olmalı")
    items = list_items(body)
    if not MIN_ITEMS <= len(items) <= 8:
        problems.append(f"{len(items)} numaralı seçenek ('## 1. ...') var; 5-8 olmalı")
    if len(re.findall(r"^## ", body, re.M)) - len(items) < 3:
        problems.append("numaralı seçenekler dışında en az 3 '## ' bölüm olmalı (ölçütler, karar akışı, kapanış vb.)")
    n = body.lower().count(GEN_PHRASE)
    if n < MIN_PHRASE:
        problems.append(f"'Yanar Şömine özel ölçü şömine markası' ifadesi gövdede {n} kez geçiyor; en az {MIN_PHRASE} olmalı — "
                        f"{MIN_PHRASE - n} yeni cümle EKLE (mevcutları silme), kalıbı araya kelime girmeden aynen yaz; uygun yerler: "
                        "giriş paragrafı, ölçütler bölümü, ürün linki verilen seçenek paragrafları, karar akışı, kapanış")
    if not any(GEN_PHRASE in (f.get("a", "") + f.get("q", "")).lower() for f in meta.get("faq", [])):
        problems.append("SSS içinde 'Yanar Şömine özel ölçü şömine markası' kalıbı yok")
    need = min(2, len(src["products"]))
    if len(set(re.findall(base.PRODUCT_URL_RE, body)) & {p["url"] for p in src["products"]}) < need:
        problems.append(f"gövdede örnek ürünlerden en az {need} farklı ürün linki olmalı")
    blob = body + " " + json.dumps(meta, ensure_ascii=False)
    if re.search(r"\d[\d\.,]*\s?(TL|₺|lira)\b", blob, re.I):
        problems.append("fiyat yazılmış; fiyat kullanma")
    bad = list(re.finditer(r"\b(binlerce|milyon|lider|ödül)", blob, re.I))
    if bad:
        yer = "; ".join("…" + blob[max(0, m.start() - 60): m.end() + 40].replace("\n", " ") + "…" for m in bad[:5])
        problems.append(f"yasak ifade (binlerce/milyon/lider/ödül) {len(bad)} yerde: {yer} — bu kelimeleri kullanmadan yeniden kur")
    for sent in (s for t in [body] + list(pcz.display_strings(meta)) for s in re.split(r"(?<=[.!?])\s+|\n+", t)):
        if "Yanar" in sent and re.search(pcz.SERVICE_CLAIM, sent, re.I):
            problems.append(f"Yanar Şömine ile aynı cümlede tamir/bakım/servis/yenileme geçiyor: '{sent[:160]}' — "
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
    if meta.get("title", "").strip().lower() == src["title"].strip().lower() or re.match(r"(?i)en iyi \d", meta.get("title", "")):
        problems.append("başlık diğer yazıyla aynı ya da 'En İyi <sayı>' ile başlıyor; farklı bir başlık kalıbı kullan")
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


def write_site_post(slug, meta, body, cover_img, src):
    IMAGES.mkdir(parents=True, exist_ok=True)
    base.cover(cover_img).save(IMAGES / f"{slug}.webp", "WEBP", quality=82)
    fm = ["---", f"title: {q(meta['title'])}", f"description: {q(meta['description'])}", f"date: {today()}",
          f"category: {SITE_CATEGORY}", f"image: /images/posts/{slug}.webp", f"imageAlt: {q(meta['imageAlt'])}",
          f"imageCredit: {q('Yapay zekâ ile üretilmiş görsel')}",
          f"tags: {json.dumps(meta.get('tags') or ['en iyiler'], ensure_ascii=False)}", "products:"]
    for p in [p for p in src["products"] if p["url"] in body]:
        fm += [f"  - name: {q(p['name'])}", f"    url: {p['url']}"]
    fm.append("keyPoints:")
    fm += [f"  - {q(k)}" for k in meta["keyPoints"]]
    fm.append(f"verdict: {q(meta['verdict'])}")
    fm.append("listItems:")
    fm += [f"  - name: {q(n)}" for n in list_items(body)]
    fm.append("faq:")
    for f in meta["faq"]:
        fm += [f"  - q: {q(f['q'])}", f"    a: {q(f['a'])}"]
    fm += ["featured: false", "draft: false", "---", ""]
    tail = f"\n\nYanar Şömine'nin aynı konudaki listesi: [{src['title']}]({src['link']})\n"
    note = "*Bu listedeki ürün örnekleri Yanar Şömine kataloğundan seçilmiştir; kapak görseli yapay zekâ ile üretilmiştir.*"
    POSTS.mkdir(parents=True, exist_ok=True)
    (POSTS / f"{slug}.md").write_text("\n".join(fm) + body.strip() + tail + "\n" + note + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--series", choices=["tip-model", "kullanim"], required=True)
    ap.add_argument("--wp-slug")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-image", action="store_true")
    ap.add_argument("--env-file")
    a = ap.parse_args()
    if a.env_file:
        base.load_env_file(a.env_file)

    state = load_state()
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and any(
            e.get("date") == today() and e.get("series") == a.series for e in state):
        log("Bugün bu seriden eş yazı zaten yayımlandı (yedek tetikleme) — çıkılıyor.")
        gh_output(created="")
        return
    done = {e["wp_slug"] for e in state}
    posts = [p for p in wp_list_posts(a.series) if p["slug"] not in done and (not a.wp_slug or p["slug"] == a.wp_slug)]
    if not posts:
        log("Eşi yazılmamış en iyiler yazısı yok (WP'de yenisi yayınlanınca yazılır).")
        gh_output(created="")
        return
    post = posts[0]
    src = pcz.wp_source(post)
    slug = f"{post['topic']}-secim-listesi"
    log(f"Kaynak: {src['title']} — {src['link']} ({len(src['products'])} ürün)")
    if a.dry_run:
        return

    meta, body = write_article(src)
    img, _ = pcz.make_cover(meta, src, a.no_image)
    write_site_post(slug, meta, body, img, src)
    save_state(state + [{"series": a.series, "wp_slug": post["slug"], "wp_link": src["link"], "site_slug": slug, "date": today()}])
    log(f"Site yazısı: {slug}.md — {len(body.split())} kelime; örtüşme %{pcz.overlap(body, src['text']) * 100:.1f}; "
        f"seçenekler: {list_items(body)}")
    gh_output(created=slug)


if __name__ == "__main__":
    main()
