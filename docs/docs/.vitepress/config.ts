import { defineConfig } from 'vitepress'
import llmstxt from 'vitepress-plugin-llms'

export default defineConfig({
  title: 'Alpha',
  description: 'The duck in the machine.',
  base: '/Alpha-App/',

  themeConfig: {
    nav: [
      { text: 'Home', link: '/' },
      { text: 'Architecture', link: '/architecture' },
    ],
    sidebar: [
      {
        items: [
          { text: 'What is Alpha?', link: '/what-is-alpha' },
          { text: 'Architecture', link: '/architecture' },
          { text: 'Chat', link: '/chat' },
          { text: 'Claude', link: '/claude' },
        ],
      },
    ],
    socialLinks: [
      { icon: 'github', link: 'https://github.com/Pondsiders/Alpha-App' },
    ],
  },

  vite: {
    plugins: [llmstxt()],
  },
})
