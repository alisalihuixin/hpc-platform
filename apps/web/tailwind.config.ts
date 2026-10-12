import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        brand: {
          50: '#eef4ff',
          100: '#d9e5ff',
          500: '#3b6ef6',
          600: '#2f57d4',
          700: '#2645ab',
        },
      },
    },
  },
  plugins: [],
};

export default config;
