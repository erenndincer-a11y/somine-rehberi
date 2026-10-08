import rss from '@astrojs/rss';
import { getPosts } from '../lib';
import { SITE } from '../site.config';

export async function GET(context) {
  const posts = await getPosts();
  return rss({
    title: SITE.name,
    description: SITE.description,
    site: context.site,
    customData: '<language>tr</language>',
    items: posts.map((p) => ({
      title: p.data.title,
      description: p.data.description,
      pubDate: p.data.date,
      link: `/yazi/${p.id}`,
    })),
  });
}
