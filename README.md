# Şömine Rehberi

Astro 7 + Tailwind 4 ile statik editoryal blog. `kadinayakkabirehberi.com`'un yapısından uyarlandı. Vercel'de yayınlanır.

```bash
npm install
npm run dev      # http://localhost:4321
npm run build    # dist/ klasörüne statik site
```

## Yeni yazı eklemek

1. Görseli `public/images/posts/<slug>.webp` olarak koy (yatay, ~1440×960 önerilir).
2. `src/content/yazilar/<slug>.md` dosyası oluştur. Dosya adı = adres (`/yazi/<slug>`).

```md
---
title: "Yazının başlığı"
description: "1–2 cümlelik özet. Google, sosyal medya ve AI'lar bunu kullanır."
date: 2026-10-05
updated: 2026-10-10          # (isteğe bağlı) güncellediğinde ekle
category: modern-somine      # modern-somine | l-tipi-modern-somine | u-tipi-modern-somine | cift-tarafli-somine | domi-klasik-somine | klasik-somine | ahsap-somine | rustik-somine | dogalgazli-somine | aski-somine | elektrikli-somine | barbeku-somine | barbeku
image: /images/posts/<slug>.webp
imageAlt: "Görselde ne olduğunu anlatan cümle"
tags: [etiket1, etiket2]
products:                    # (isteğe bağlı) "Yazıdaki ürün" kutusu
  - name: "Ürün adı"
    url: https://yanarsomine.com.tr/urun/...
featured: false              # true → anasayfada büyük "öne çıkan" yazı
draft: false                 # true → yayınlanmaz
keyPoints:                   # "Kısaca" kutusu — AI'ların alıntılayacağı net cümleler
  - "Kısa, tek başına anlamlı bir cevap cümlesi."
faq:                         # Sık sorulan sorular — FAQPage şeması olarak da işaretlenir
  - q: "Kullanıcının soracağı soru?"
    a: "Doğrudan, tek paragraflık cevap."
---

Yazının gövdesi Markdown ile. `## Ara başlık`, listeler, **kalın**, [bağlantı](https://...) kullanılabilir.
```

Kategoriler (alt kategoriler `parent` ile), site adı, e-posta ve sosyal medya linkleri: `src/site.config.ts`.

## Yapay zekâ okunabilirliği (GEO / AEO)

Site ChatGPT, Claude, Gemini, Perplexity gibi asistanların kolayca okuyup alıntılayacağı şekilde kuruldu:

| Ne | Nerede |
|---|---|
| Tamamen statik HTML (JS gerekmeden içerik okunur) | tüm sayfalar |
| AI botlarına açık izin (GPTBot, ClaudeBot, Google-Extended, PerplexityBot…) | `public/robots.txt` |
| Sitenin AI özeti + tüm yazıların listesi | `/llms.txt` |
| Tüm yazıların tam metni tek dosyada | `/llms-full.txt` |
| Her yazının temiz Markdown sürümü | `/yazi/<slug>.md` |
| BlogPosting, FAQPage, BreadcrumbList, Organization, WebSite şemaları | her yazıda JSON-LD |
| "Kısaca" özet kutusu ve SSS bölümü | `keyPoints` / `faq` alanları |
| Yayın + güncelleme tarihi (`<time>` ve `dateModified`) | `date` / `updated` alanları |
| Site haritası ve RSS | `/sitemap-index.xml`, `/rss.xml` |

Yazı ipuçları: Başlığı kullanıcının soracağı soru gibi kur, ilk paragrafta cevabı doğrudan ver, `keyPoints`'e 3 net cümle yaz, her yazıya 2–4 SSS ekle, güncellediğinde `updated` tarihini değiştir.

## Domain

Domain farklıysa şu üç yerde güncelle: `astro.config.mjs` (`site`), `src/site.config.ts` (`url`, `email`), `public/robots.txt`.

## Marka dosyaları

`brand/logo.png` (şeffaf), `brand/logo-koyu-zemin.png`, `brand/logo-ikon.svg`. Renkler: bordo `#7a2e3c`, mürekkep `#1a1a1a`, zemin `#fafaf9`. Fontlar: Fraunces (başlık), Inter (metin).
