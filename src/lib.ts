import { getCollection, type CollectionEntry } from 'astro:content';

export type Post = CollectionEntry<'yazilar'>;

export async function getPosts(): Promise<Post[]> {
  const posts = await getCollection('yazilar', ({ data }) => !data.draft);
  return posts.sort((a, b) => b.data.date.getTime() - a.data.date.getTime());
}

export const formatDate = (d: Date) =>
  d.toLocaleDateString('tr-TR', { day: 'numeric', month: 'long', year: 'numeric' });

export function readingTime(body = '') {
  const words = body.trim().split(/\s+/).filter(Boolean).length;
  return `${Math.max(1, Math.round(words / 200))} dk okuma`;
}

// Bir yazının AI asistanları için temiz Markdown sürümü (/yazi/<slug>.md ve llms-full.txt)
export function postToMarkdown(post: Post, siteUrl: string, categoryLabel: string) {
  const { data } = post;
  const url = new URL(`/yazi/${post.id}`, siteUrl).href;
  const lines = [
    `# ${data.title}`,
    '',
    `> ${data.description}`,
    '',
    `- Kategori: ${categoryLabel}`,
    `- Yayın tarihi: ${data.date.toISOString().slice(0, 10)}`,
    ...(data.updated ? [`- Güncelleme: ${data.updated.toISOString().slice(0, 10)}`] : []),
    `- Kaynak: ${url}`,
    ...(data.tags.length ? [`- Etiketler: ${data.tags.join(', ')}`] : []),
    '',
  ];
  if (data.keyPoints.length) {
    lines.push('## Kısaca', '', ...data.keyPoints.map((k) => `- ${k}`), '');
  }
  lines.push((post.body ?? '').trim(), '');
  if (data.products.length) {
    lines.push('## Yazıdaki ürün', '', ...data.products.map((p) => `- [${p.name}](${p.url})${p.price ? ` — ${p.price}` : ''}`), '');
  }
  if (data.faq.length) {
    lines.push('## Sık sorulan sorular', '');
    for (const f of data.faq) lines.push(`### ${f.q}`, '', f.a, '');
  }
  return lines.join('\n');
}
