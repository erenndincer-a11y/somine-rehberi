// Sitenin tüm marka/iletişim bilgileri tek yerde. Domain bağlanınca `url`'i güncelle.
export const SITE = {
  name: 'Şömine Rehberi',
  url: 'https://sominerehberi.com',
  title: 'Şömine Rehberi — Model seçimi, kurulum ve bakım rehberleri',
  description:
    'Şömine üzerine bağımsız bir editoryal yayın: doğru model seçimi, yakıt türleri, kurulum ve bakım, mekâna göre şömine rehberleri.',
  author: 'Şömine Rehberi editör ekibi',
  email: 'info@sominerehberi.com',
  city: 'İstanbul',
  socials: [
    { name: 'Pinterest', label: "Pinterest'te takip et", href: 'https://pinterest.com/' },
    { name: 'Instagram', label: "Instagram'da takip et", href: 'https://instagram.com/' },
    { name: 'TikTok', label: "TikTok'ta takip et", href: 'https://tiktok.com/' },
  ],
} as const;

// Kategori yapısı. `parent` olanlar alt kategoridir (adres: /kategori/<parent>/<slug>).
// Yazının `category` alanına en alt seviyedeki slug yazılır.
export type Category = {
  slug: string;
  name: string;
  description: string;
  parent?: string;
};

export const CATEGORIES: Category[] = [
  {
    slug: 'modern-somine',
    name: 'Modern Şömine',
    description: 'Sade çizgiler, geniş cam yüzeyler ve minimalist hatlar: çağdaş salonların vazgeçilmezi.',
  },
  {
    slug: 'l-tipi-modern-somine',
    name: 'L Tipi Modern Şömine',
    parent: 'modern-somine',
    description: 'İki cepheden alev gösteren L tipi modeller: köşe salonlarda mekânı ikiye bölmeden ısıtır.',
  },
  {
    slug: 'u-tipi-modern-somine',
    name: 'U Tipi Modern Şömine',
    parent: 'modern-somine',
    description: 'Üç yönden camlı U tipi modeller: alevi neredeyse her açıdan gösteren panoramik tasarım.',
  },
  {
    slug: 'cift-tarafli-somine',
    name: 'Çift Taraflı Şömine',
    description: 'İki mekânı aynı anda ısıtan, salon ile yemek odası arasına kurulan mekân bölücü modeller.',
  },
  {
    slug: 'domi-klasik-somine',
    name: 'Dömi Klasik Şömine',
    description: 'Klasik detayları sade çizgilerle buluşturan ara form: hem geleneksel hem güncel mekânlara uyar.',
  },
  {
    slug: 'klasik-somine',
    name: 'Klasik Şömine',
    description: 'Mermer ve taş işçilikli, oymalı detaylı geleneksel şömineler: klasik ve lüks iç mekânların simgesi.',
  },
  {
    slug: 'ahsap-somine',
    name: 'Ahşap Şömine',
    description: 'Ahşap kaplama ve detaylarla sıcak bir görünüm: doğal/rustik dekorasyonla uyumlu modeller.',
  },
  {
    slug: 'rustik-somine',
    name: 'Rustik Şömine',
    description: 'Doğal taş ve kaba dokulu yüzeylerle köy evi/yazlık havası veren, samimi görünümlü şömineler.',
  },
  {
    slug: 'dogalgazli-somine',
    name: 'Doğalgazlı Şömine',
    description: 'Odun derdi olmadan gerçek alev görüntüsü: doğalgaz hattına bağlanan, temiz yanan modeller.',
  },
  {
    slug: 'aski-somine',
    name: 'Askı Şömine',
    description: 'Yerden kesilip havada asılı duran, 360 derece alev gösteren heykelsi tasarım şömineler.',
  },
  {
    slug: 'elektrikli-somine',
    name: 'Elektrikli Şömine',
    description: 'Baca gerektirmeyen, kurulumu en kolay şömine türü: apartman dairelerinde de kullanılabilir.',
  },
  {
    slug: 'barbeku-somine',
    name: 'Barbekü Şömine',
    description: 'İç veya yarı açık mekânlara kurulan, ızgara yapımına uygun şömine-barbekü kombinasyonları.',
  },
  {
    slug: 'barbeku',
    name: 'Barbekü',
    description: 'Bahçe ve teras için bağımsız barbekü üniteleri: taş, tuğla ve metal gövdeli modeller.',
  },
];

export const getCategory = (slug: string) => CATEGORIES.find((c) => c.slug === slug);
export const categoryName = (slug: string) => getCategory(slug)?.name ?? slug;
export const topCategories = CATEGORIES.filter((c) => !c.parent);
export const subcategoriesOf = (slug: string) => CATEGORIES.filter((c) => c.parent === slug);
export const categoryUrl = (slug: string) => {
  const c = getCategory(slug);
  return c?.parent ? `/kategori/${c.parent}/${c.slug}` : `/kategori/${slug}`;
};
/** "Modern Şömine › L Tipi Modern Şömine" gibi tam kategori yolu */
export const categoryPath = (slug: string) => {
  const c = getCategory(slug);
  return c?.parent ? `${categoryName(c.parent)} › ${c.name}` : categoryName(slug);
};
/** Bir kategori sayfasında listelenecek yazı kategorileri (kendisi + alt kategorileri) */
export const categoryFamily = (slug: string) => [slug, ...subcategoriesOf(slug).map((c) => c.slug)];
