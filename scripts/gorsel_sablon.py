# kopya — kaynak: gorsel-sablon/gorsel_sablon.py (değiştirme; kanonik dosyayı güncelleyip buraya kopyala)
"""Blog görseli şablon motoru — ChatGPT'siz kapak/iç görsel.

Sıra: 1) vitrin  : ürün fotoğrafının düz zemini silinir, marka stüdyo zemini + gölge (1–3 ürün)
      2) kart    : zemin silinemezse (manken/sahne fotoğrafı) fotoğraf çerçeveli kart olarak, bulanık kendi zemini üstünde
      3) havuz   : ürün yoksa (hizmet/bilgi yazısı) markanın görsel havuzundan seçilen görsel + başlık bandı
      4) yedek   : yalnız yukarıdakiler kalite kontrolünden geçemezse çağıranın verdiği GPT fonksiyonu

Hepsi yerelde, ücretsiz (Pillow + numpy + scipy). Kullanım:
    from gorsel_sablon import kapak
    img, yontem = kapak([foto1, foto2], tema="vivaq")              # PIL.Image listesi
    img, yontem = kapak([], tema="eosgb", havuz=[...yollar], baslik="...")
"""
from __future__ import annotations

import hashlib
import os
import random
from dataclasses import dataclass

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy import ndimage

W, H = 1440, 960
FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")


@dataclass
class Tema:
    duvar_ust: str       # stüdyo duvarı üst rengi
    duvar_alt: str       # duvar alt (ufka yakın) rengi
    zemin: str           # zemin rengi
    vurgu: str           # başlık bandı / ince çizgi
    yazi: str = "#ffffff"
    font: str = "Sora-Bold.ttf"
    ufuk: float = 0.70   # zemin çizgisi (yükseklik oranı)


TEMALAR = {
    "purevits":  Tema("#F3F5F0", "#E6EBE2", "#DCE3D6", "#3F6B4E"),
    "nisantasi": Tema("#F5F1EC", "#EAE3DA", "#DED5CA", "#2B2B2B"),
    "yanar":     Tema("#F2EFEB", "#E4DED7", "#D6CEC5", "#7A3E1D"),
    "vivaq":     Tema("#F6F0EC", "#ECE2DC", "#E0D3CB", "#6E2639"),
    "rhen":      Tema("#F4F2F0", "#E7E3DF", "#DAD4CE", "#1F1F1F"),
    "eosgb":     Tema("#EEF2F7", "#DFE6EF", "#D0DAE6", "#0F3D6E"),
}


def _hex(c):
    c = c.lstrip("#")
    return np.array([int(c[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def _grain(a, amount=2.2, seed=0):
    rng = np.random.default_rng(seed)
    return np.clip(a + rng.normal(0, amount, a.shape[:2])[..., None], 0, 255)


# ── 0) dolgu şeritlerini kırp (fotoğrafın içine gömülü düz renkli kenarlar) ──
def kirp(img: Image.Image, tol: float = 14.0):
    a = np.asarray(img.convert("RGB")).astype(np.float32)
    h, w = a.shape[:2]
    def duz(sat):
        ref = np.median(sat, axis=0)
        return (np.linalg.norm(sat - ref, axis=1) < tol).mean() > 0.985
    u, al, so, sa = 0, h, 0, w
    while u < h * 0.4 and duz(a[u]): u += 1
    while al > h * 0.6 and duz(a[al - 1]): al -= 1
    while so < w * 0.4 and duz(a[:, so]): so += 1
    while sa > w * 0.6 and duz(a[:, sa - 1]): sa -= 1
    if (u, al, so, sa) == (0, h, 0, w):
        return img
    # şeridi yalnız kenar rengi içerikten belirgin farklıysa kırp (düz stüdyo zeminini yeme)
    # şerit rengi, kırpılan alanın hemen içindeki kenarla aynıysa bu şerit değil düz stüdyo zemini: dokunma
    ic = a[u:al, so:sa]
    b = 4
    halka = np.concatenate([ic[:b].reshape(-1, 3), ic[-b:].reshape(-1, 3), ic[:, :b].reshape(-1, 3), ic[:, -b:].reshape(-1, 3)])
    serit = np.concatenate([a[:u].reshape(-1, 3), a[al:].reshape(-1, 3), a[:, :so].reshape(-1, 3), a[:, sa:].reshape(-1, 3)])
    if np.linalg.norm(np.median(serit, 0) - np.median(halka, 0)) < 30:
        return img
    return img.crop((so, u, sa, al))


# ── 1) zemin silme ────────────────────────────────────────────────────────
def kesit(img: Image.Image, tol: float = 28.0):
    """Düz, açık renkli zeminli ürün fotoğrafından RGBA kesit. Zemin düz değilse None + sebep."""
    img = img.convert("RGB")
    if max(img.size) > 1600:
        img.thumbnail((1600, 1600), Image.LANCZOS)
    a = np.asarray(img).astype(np.float32)
    h, w = a.shape[:2]
    b = 6
    kenar = np.concatenate([a[:b].reshape(-1, 3), a[-b:].reshape(-1, 3), a[:, :b].reshape(-1, 3), a[:, -b:].reshape(-1, 3)])
    bg = np.median(kenar, axis=0)
    yakin = (np.linalg.norm(kenar - bg, axis=1) < tol).mean()
    if yakin < 0.85 or (bg.mean() < 185 and yakin < 0.97):
        return None, f"zemin düz değil (açıklık {bg.mean():.0f}, kenar uyumu %{yakin * 100:.0f})"
    d = np.linalg.norm(a - bg, axis=2)
    aday = d < tol
    # ürün altındaki yumuşak gri gölge/yansıma: renksiz, açık ve yalnız görselin alt bölümünde
    renk = a.max(axis=2) - a.min(axis=2)
    on0 = ~aday
    satirlar = np.where(on0.mean(axis=1) > 0.01)[0]
    if len(satirlar):
        ust, alt_ = satirlar.min(), satirlar.max()
        sinir_y = int(ust + (alt_ - ust) * 0.80)
        golge = (renk < 16) & (a.mean(axis=2) > bg.mean() - 75) & (d < 95)
        golge[:sinir_y] = False
        aday |= golge
    lab, n = ndimage.label(aday)
    sinir = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    zemin = np.isin(lab, list(sinir))
    # kapalı boşluklar (çanta sapı içi vb.): zemine çok yakın renkte ve yeterince büyükse zemin say
    sayac = ndimage.sum(np.ones_like(d), lab, index=np.arange(1, n + 1))
    ort = ndimage.mean(d, lab, index=np.arange(1, n + 1))
    for i in range(n):
        if (i + 1) not in sinir and sayac[i] > h * w * 0.0015 and ort[i] < tol * 0.75:
            zemin |= lab == (i + 1)
    on = ~zemin
    on = ndimage.binary_opening(on, iterations=1)
    lab2, n2 = ndimage.label(on)
    if n2 == 0:
        return None, "ürün bulunamadı"
    boy = ndimage.sum(on, lab2, index=np.arange(1, n2 + 1))
    on = np.isin(lab2, np.where(boy > boy.max() * 0.02)[0] + 1)   # küçük kırıntıları at
    kapsama = on.mean()
    if not 0.04 < kapsama < 0.92:
        return None, f"kesit oranı şüpheli (%{kapsama * 100:.0f})"
    alfa = ndimage.binary_erosion(on, iterations=1).astype(np.float32)
    alfa = ndimage.gaussian_filter(alfa, 1.0)
    rgba = np.dstack([a, np.clip(alfa * 255, 0, 255)]).astype(np.uint8)
    out = Image.fromarray(rgba, "RGBA")
    return out.crop(out.getbbox()), "ok"


# ── zemin + gölge ─────────────────────────────────────────────────────────
def _studyo(t: Tema, seed=0):
    y = np.linspace(0, 1, H)[:, None, None]
    uy = int(H * t.ufuk)
    duvar = _hex(t.duvar_ust) * (1 - y) + _hex(t.duvar_alt) * y
    a = np.broadcast_to(duvar, (H, W, 3)).copy()
    zy = np.linspace(0, 1, H - uy)[:, None, None]
    a[uy:] = _hex(t.zemin) * (1 - zy * 0.35) + _hex(t.duvar_alt) * (zy * 0.35)
    # ufuk geçişini yumuşat
    gec = 18
    for i in range(-gec, gec):
        k = (i + gec) / (2 * gec)
        r = uy + i
        a[r] = a[uy - gec] * (1 - k) + a[uy + gec] * k
    # merkezden ışık + kenar kararması
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W * 0.55)) ** 2 + ((yy - H * 0.45) / (H * 0.6)) ** 2)
    a *= (1.04 - 0.10 * np.clip(r, 0, 1.4))[..., None]
    return Image.fromarray(_grain(a, seed=seed).astype(np.uint8), "RGB")


def _golge(canvas: Image.Image, urun: Image.Image, x, y):
    alfa = np.asarray(urun.split()[-1]).astype(np.float32) / 255
    w, h = urun.size
    # temas gölgesi: ürün tabanının genişliğinde basık elips
    alt = alfa[int(h * 0.85):].max(axis=0)
    xs = np.where(alt > 0.5)[0]
    x0, x1 = (xs.min(), xs.max()) if len(xs) else (0, w)
    katman = Image.new("L", canvas.size, 0)
    d = ImageDraw.Draw(katman)
    cx, gw = x + (x0 + x1) / 2, (x1 - x0) * 0.55
    d.ellipse([cx - gw, y + h - h * 0.025, cx + gw, y + h + h * 0.025], fill=150)
    katman = katman.filter(ImageFilter.GaussianBlur(h * 0.02))
    genis = Image.new("L", canvas.size, 0)
    ImageDraw.Draw(genis).ellipse([cx - gw * 1.35, y + h - h * 0.05, cx + gw * 1.35, y + h + h * 0.06], fill=70)
    genis = genis.filter(ImageFilter.GaussianBlur(h * 0.06))
    for k in (genis, katman):
        canvas.paste(Image.new("RGB", canvas.size, (40, 32, 28)), (0, 0), k.point(lambda v: int(v * 0.55)))


def vitrin(kesitler, t: Tema, seed=0):
    c = _studyo(t, seed)
    n = len(kesitler)
    uy = int(H * t.ufuk)
    hedef_h = {1: 0.66, 2: 0.58, 3: 0.50}[min(n, 3)] * H
    alan_w = W * (0.62 if n == 1 else 0.84)
    olcek = []
    for k in kesitler:
        s = hedef_h / k.height
        olcek.append(k.resize((max(1, int(k.width * s)), int(hedef_h)), Image.LANCZOS))
    toplam = sum(k.width for k in olcek) + (n - 1) * W * 0.05
    if toplam > alan_w:  # sığmazsa küçült
        s = alan_w / toplam
        olcek = [k.resize((int(k.width * s), int(k.height * s)), Image.LANCZOS) for k in olcek]
        toplam = alan_w
    x = (W - toplam) / 2
    taban = uy + H * 0.12
    for k in olcek:
        yy = int(taban - k.height)
        _golge(c, k, int(x), yy)
        c.paste(k, (int(x), yy), k)
        x += k.width + W * 0.05
    return c


# ── 2) kart ───────────────────────────────────────────────────────────────
def _kapla(img, w, h):
    s = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    l, u = (img.width - w) // 2, (img.height - h) // 2
    return img.crop((l, u, l + w, u + h))


def kart(fotolar, t: Tema):
    fotolar = [f.convert("RGB") for f in fotolar][:3]
    arka = _kapla(fotolar[0], W, H).filter(ImageFilter.GaussianBlur(45))
    a = np.asarray(arka).astype(np.float32)
    a = a * 0.55 + _hex(t.duvar_alt) * 0.45          # marka tonuyla yumuşat
    c = Image.fromarray(_grain(a).astype(np.uint8))
    n = len(fotolar)
    kh = int(H * (0.82 if n == 1 else 0.74))
    kartlar = []
    for f in fotolar:
        s = kh / f.height
        k = f.resize((int(f.width * s), kh), Image.LANCZOS)
        if k.width > W * (0.8 / n):
            k = _kapla(f, int(W * 0.8 / n), kh)
        kartlar.append(k)
    bosluk = W * 0.035
    toplam = sum(k.width for k in kartlar) + bosluk * (n - 1)
    x = (W - toplam) / 2
    for k in kartlar:
        y = (H - k.height) // 2
        maske = Image.new("L", k.size, 0)
        ImageDraw.Draw(maske).rounded_rectangle([0, 0, k.width - 1, k.height - 1], radius=22, fill=255)
        golge = Image.new("L", c.size, 0)
        golge.paste(maske, (int(x) + 10, y + 22))
        golge = golge.filter(ImageFilter.GaussianBlur(26)).point(lambda v: int(v * 0.45))
        c.paste(Image.new("RGB", c.size, (20, 16, 14)), (0, 0), golge)
        c.paste(k, (int(x), y), maske)
        x += k.width + bosluk
    return c


def _ates_odak(img: Image.Image):
    """Şömine fotoğrafında alevin merkezi (oran olarak) ve genişliği; alev yoksa None."""
    k = img.convert("RGB").copy()
    k.thumbnail((800, 800))
    a = np.asarray(k).astype(np.int32)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    alev = (r > 200) & (g < 170) & (b < 90) & (r - g > 60)        # turuncu-kırmızı, doygun (spot ışık/mermer değil)
    lab, n = ndimage.label(ndimage.binary_dilation(alev, iterations=6))
    if n == 0 or alev.sum() < k.width * k.height * 0.0004:
        return None
    boy = ndimage.sum(alev, lab, index=np.arange(1, n + 1))
    ys, xs = np.where(lab == int(np.argmax(boy)) + 1)
    return xs.mean() / k.width, ys.mean() / k.height, (xs.max() - xs.min()) / k.width


def _odak_kirp(img: Image.Image, w, h, yakin=1.7):
    """Alev varsa ona yakınlaş (alev genişliği × yakin), yoksa ortadan kapla."""
    o = _ates_odak(img)
    if not o:
        return _kapla(img, w, h)
    cx, cy, aw = o
    cw = min(img.width, max(img.width * aw * yakin, img.width * 0.32))
    ch = cw * h / w
    if ch > img.height:
        ch, cw = img.height, img.height * w / h
    x0 = min(max(cx * img.width - cw / 2, 0), img.width - cw)
    y0 = min(max(cy * img.height - ch * 0.62, 0), img.height - ch)   # alev kadrajın alt-ortasında
    return img.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch))).resize((w, h), Image.LANCZOS)


def sade(fotolar, t: Tema, odak=False):
    """Düz marka zemini + yuvarlak köşeli fotoğraf(lar). odak=True: alevi bul, ona yakınlaş."""
    fotolar = [f.convert("RGB") for f in fotolar][:3]
    y = np.linspace(0, 1, H)[:, None, None]
    a = np.broadcast_to(_hex(t.duvar_ust) * (1 - y) + _hex(t.duvar_alt) * y, (H, W, 3)).copy()
    c = Image.fromarray(_grain(a, 1.2).astype(np.uint8))
    n = len(fotolar)
    kh = int(H * (0.80 if n == 1 else (0.62 if odak else 0.74)))
    bosluk = int(W * 0.035)
    kartlar = []
    for f in fotolar:
        if odak:
            kw = int(W * 0.62) if n == 1 else int((W * 0.86 - bosluk * (n - 1)) / n)
            k = _odak_kirp(f, kw, kh)
        else:
            s = kh / f.height
            k = f.resize((int(f.width * s), kh), Image.LANCZOS)
            if k.width > W * (0.8 / n):
                k = _kapla(f, int(W * 0.8 / n), kh)
        kartlar.append(k)
    toplam = sum(k.width for k in kartlar) + bosluk * (n - 1)
    x = (W - toplam) / 2
    for k in kartlar:
        yy = (H - k.height) // 2
        maske = Image.new("L", k.size, 0)
        ImageDraw.Draw(maske).rounded_rectangle([0, 0, k.width - 1, k.height - 1], radius=18, fill=255)
        golge = Image.new("L", c.size, 0)
        golge.paste(maske, (int(x), yy + 14))
        golge = golge.filter(ImageFilter.GaussianBlur(24)).point(lambda v: int(v * 0.22))
        c.paste(Image.new("RGB", c.size, (30, 24, 20)), (0, 0), golge)
        c.paste(k, (int(x), yy), maske)
        x += k.width + bosluk
    return c


def _yatay_odak_kapla(img, w, h):
    """Yakınlaştırmadan kapla; alev varsa kadrajı yatayda aleve ortala."""
    s = max(w / img.width, h / img.height)
    im = img.resize((int(img.width * s) + 1, int(img.height * s) + 1), Image.LANCZOS)
    o = _ates_odak(img)
    cx = o[0] * im.width if o else im.width / 2
    l = int(min(max(cx - w / 2, 0), im.width - w))
    u = (im.height - h) // 2
    return im.crop((l, u, l + w, u + h))


def tam(fotolar, t: Tema):
    """Tam kadraj: fotoğraf(lar) yakınlaştırmasız, kenarları hafif kararmış. 2+ fotoğrafta yan yana şeritler."""
    fotolar = [f.convert("RGB") for f in fotolar][:3]
    n = len(fotolar)
    ara = 10
    c = Image.new("RGB", (W, H), t.duvar_ust)
    pw = (W - ara * (n - 1)) // n
    for i, f in enumerate(fotolar):
        c.paste(_yatay_odak_kapla(f, pw, H), (i * (pw + ara), 0))
    a = np.asarray(c).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    r = np.sqrt(((xx - W / 2) / (W * 0.6)) ** 2 + ((yy - H * 0.55) / (H * 0.65)) ** 2)
    a *= (1.03 - 0.18 * np.clip(r, 0, 1.3))[..., None]
    return Image.fromarray(np.clip(a, 0, 255).astype(np.uint8))


def _taban_skoru(k: Image.Image):
    """Yan profil tercihi: ürünün tabanı zemine ne kadar geniş basıyor (alt %5 satırların doluluk/genişlik)."""
    al = np.asarray(k.split()[-1]) > 128
    h = al.shape[0]
    alt = al[int(h * 0.95):].any(axis=0).mean()
    parca = ndimage.label(al)[1]
    return alt - 0.15 * (parca - 1)


# ── 3) havuz ──────────────────────────────────────────────────────────────
def _font(t, boy):
    return ImageFont.truetype(os.path.join(FONT_DIR, t.font), boy)


def _sar(d, metin, font, genislik):
    satirlar, s = [], ""
    for k in metin.split():
        dene = (s + " " + k).strip()
        if d.textlength(dene, font=font) <= genislik:
            s = dene
        else:
            satirlar.append(s)
            s = k
    return satirlar + [s] if s else satirlar


def havuz(gorsel: Image.Image, t: Tema, baslik: str | None = None, etiket: str | None = None):
    c = _kapla(gorsel.convert("RGB"), W, H)
    if not baslik:
        return c
    a = np.asarray(c).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    g = np.clip((yy / H - 0.35) / 0.65, 0, 1) ** 1.3 * 0.82          # alttan koyulaşan bant
    a = a * (1 - g[..., None]) + _hex(t.vurgu) * 0.35 * g[..., None]
    c = Image.fromarray(a.astype(np.uint8))
    d = ImageDraw.Draw(c)
    f = _font(t, 64)
    satir = _sar(d, baslik, f, W * 0.78)[:3]
    y = H - 90 - len(satir) * 78
    if etiket:
        fe = _font(t, 26)
        d.rectangle([80, y - 58, 80 + d.textlength(etiket.upper(), font=fe) + 28, y - 18], fill=t.vurgu)
        d.text((94, y - 54), etiket.upper(), font=fe, fill="#ffffff")
    for s in satir:
        d.text((80, y), s, font=f, fill=t.yazi)
        y += 78
    return c


# ── seçici ────────────────────────────────────────────────────────────────
SADE = {"rhen": dict(odak=False)}
TAM = {"yanar"}   # Eren 2026-11-19: Yanar tek şömine = tüm duvar, yakınlaştırmasız tam kadraj (C)   # sahne/manken fotoğraflı markalar: bulanık kart yerine düz zemin


def kapak(fotolar, tema: str, havuz_yollari=None, baslik=None, etiket=None, anahtar="", yedek=None, log=print):
    """(Image, yöntem) döner. yöntem: vitrin | kart | havuz | yedek-gpt.
    fotolar: ürün başına bir Image ya da o ürünün galeri listesi [Image, ...] (kesilebilen ilk fotoğraf seçilir)."""
    t = TEMALAR[tema]
    seed = int(hashlib.md5((anahtar or tema).encode()).hexdigest()[:8], 16)
    urunler = []
    for f in fotolar or []:
        galeri = [kirp(x) for x in (f if isinstance(f, (list, tuple)) else [f]) if x is not None]
        galeri = [x for x in galeri if min(x.size) >= 450]
        if galeri:
            urunler.append(galeri)
    if urunler:
        secim, sebep = [], []
        for galeri in urunler:
            adaylar, neden = [], ""
            for x in galeri:
                k, neden = kesit(x)
                if k:
                    adaylar.append(k)
            if adaylar:   # galeride birden çok kesilebilen varsa yan profil / tabanı yere basanı seç
                secim.append(max(adaylar, key=_taban_skoru) if len(adaylar) > 1 else adaylar[0])
            else:
                sebep.append(neden)
        if len(secim) == len(urunler):
            return vitrin(secim, t, seed), "vitrin"
        if tema in TAM:
            log("  vitrin olmadı → tam kadraj")
            return tam([g[0] for g in urunler], t), "tam"
        if tema in SADE:
            log("  vitrin olmadı → sade")
            return sade([g[0] for g in urunler], t, **SADE[tema]), "sade"
        log("  vitrin olmadı: " + "; ".join(sebep) + " → kart")
        return kart([g[0] for g in urunler], t), "kart"
    if havuz_yollari:
        yol = random.Random(seed).choice(sorted(havuz_yollari))
        return havuz(Image.open(yol), t, baslik, etiket), "havuz"
    if yedek:
        log("  ürün fotoğrafı ve havuz yok → GPT yedeği")
        return yedek(), "yedek-gpt"
    raise ValueError("Görsel kaynağı yok (ürün fotoğrafı, havuz ya da yedek)")
