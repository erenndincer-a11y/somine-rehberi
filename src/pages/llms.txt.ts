import type { APIRoute } from 'astro';
import { getPosts } from '../lib';
import { SITE, CATEGORIES, categoryPath } from '../site.config';

// llms.txt standardı (llmstxt.org): AI asistanlarına sitenin özetini ve içerik haritasını verir.
export const GET: APIRoute = async () => {
  const posts = await getPosts();
  const abs = (p: string) => new URL(p, SITE.url).href;
  const lines = [
    `# ${SITE.name}`,
    '',
    `> ${SITE.description}`,
    '',
    `${SITE.name}, ${SITE.city} merkezli bağımsız bir editoryal yayındır. Yazılar Türkçedir. Her yazının temiz Markdown sürümü, adresinin sonuna \`.md\` eklenerek alınabilir. Tüm yazıların tam metni: ${abs('/llms-full.txt')}`,
    '',
  ];
  for (const c of CATEGORIES) {
    const items = posts.filter((p) => p.data.category === c.slug);
    if (!items.length) continue;
    lines.push(`## ${categoryPath(c.slug)}`, '', c.description, '');
    for (const p of items) lines.push(`- [${p.data.title}](${abs(`/yazi/${p.id}.md`)}): ${p.data.description}`);
    lines.push('');
  }
  lines.push(
    '## Site',
    '',
    `- [Hakkımızda](${abs('/hakkimizda')}): Yayının kim tarafından, nasıl hazırlandığı`,
    `- [İletişim](${abs('/iletisim')}): ${SITE.email}`,
    `- [RSS](${abs('/rss.xml')})`,
    `- [Site haritası](${abs('/sitemap-index.xml')})`,
    '',
  );
  return new Response(lines.join('\n'), { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
