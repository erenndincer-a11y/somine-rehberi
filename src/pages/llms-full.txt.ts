import type { APIRoute } from 'astro';
import { getPosts, postToMarkdown } from '../lib';
import { SITE, categoryPath } from '../site.config';

// Tüm yazıların tam metni tek dosyada — AI asistanlarının siteyi tek seferde okuyabilmesi için.
export const GET: APIRoute = async () => {
  const posts = await getPosts();
  const body = [
    `# ${SITE.name} — tüm yazılar`,
    '',
    `> ${SITE.description}`,
    '',
    // Başlıklar bir seviye aşağı: dosyanın tek H1'i site başlığı kalsın
    ...posts.map((p) => postToMarkdown(p, SITE.url, categoryPath(p.data.category)).replace(/^(#+) /gm, '#$1 ')),
  ].join('\n\n---\n\n');
  return new Response(body, { headers: { 'Content-Type': 'text/plain; charset=utf-8' } });
};
