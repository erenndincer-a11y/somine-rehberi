import { defineCollection } from 'astro:content';
import { glob } from 'astro/loaders';
import { z } from 'astro/zod';
import { CATEGORIES } from './site.config';

const yazilar = defineCollection({
  loader: glob({ pattern: '**/*.md', base: './src/content/yazilar' }),
  schema: z.object({
    title: z.string(),
    description: z.string(),
    date: z.coerce.date(),
    // Yazıyı güncellediğinde ekle — AI'lar ve Google tazeliği buradan okur
    updated: z.coerce.date().optional(),
    category: z.enum(CATEGORIES.map((c) => c.slug) as [string, ...string[]]),
    image: z.string(),
    imageAlt: z.string(),
    imageCredit: z.string().optional(),
    imageCreditUrl: z.string().optional(),
    tags: z.array(z.string()).default([]),
    author: z.string().optional(),
    // "Kısaca" kutusu: yazının en üstünde, AI'ların doğrudan alıntılayabileceği kısa cevaplar
    keyPoints: z.array(z.string()).default([]),
    // Sık sorulan sorular: sayfada gösterilir + FAQPage şeması olarak işaretlenir
    faq: z.array(z.object({ q: z.string(), a: z.string() })).default([]),
    // Yazıda geçen ürünler: "Yazıdaki ürün" kutusunda gösterilir
    products: z.array(z.object({ name: z.string(), url: z.string().url(), price: z.string().optional() })).default([]),
    // Karşılaştırma yazıları: yan yana ürün kartları, özellik tablosu ve karar kutusu
    comparison: z
      .array(z.object({ name: z.string(), short: z.string(), url: z.string().url(), image: z.string(), bestFor: z.string() }))
      .default([]),
    specs: z.array(z.object({ label: z.string(), values: z.array(z.string()) })).default([]),
    verdict: z.string().optional(),
    featured: z.boolean().default(false),
    draft: z.boolean().default(false),
  }),
});

export const collections = { yazilar };
