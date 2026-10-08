import type { APIRoute } from 'astro';
import { getPosts, postToMarkdown, type Post } from '../../lib';
import { SITE, categoryPath } from '../../site.config';

export async function getStaticPaths() {
  const posts = await getPosts();
  return posts.map((post) => ({ params: { slug: post.id }, props: { post } }));
}

export const GET: APIRoute = ({ props }) => {
  const post = props.post as Post;
  return new Response(postToMarkdown(post, SITE.url, categoryPath(post.data.category)), {
    headers: { 'Content-Type': 'text/markdown; charset=utf-8' },
  });
};
