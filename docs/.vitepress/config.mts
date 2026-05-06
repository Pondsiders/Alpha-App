import { defineConfig } from 'vitepress'

// https://vitepress.dev/reference/site-config
export default defineConfig({
  title: "Alpha",
  description: "An Artificial Intelligence",
  themeConfig: {
    // https://vitepress.dev/reference/default-theme-config
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Wire Protocol', link: '/wire-protocol' },
    ],

    sidebar: [
      {
        text: 'Specification',
        items: [
          { text: 'Wire Protocol', link: '/wire-protocol' },
        ],
      },
    ],

    socialLinks: [
      { icon: 'github', link: 'https://github.com/Pondsiders/Alpha-App' },
    ],
  },
})
