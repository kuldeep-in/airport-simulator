/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        base: '#0a0e1a',
        panel: '#111827',
        card: '#1c2535',
        edge: '#2d3748',
      },
    },
  },
  plugins: [],
};
