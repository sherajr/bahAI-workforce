import type { Config } from "tailwindcss";

export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  darkMode: "class",
  theme: {
    extend: {
      colors: {
        gold: {
          300: "#f5d78e",
          400: "#eec36a",
          500: "#d9a441",
        },
      },
      fontFamily: {
        display: ["Georgia", "Cambria", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "Consolas", "monospace"],
      },
    },
  },
  plugins: [],
} satisfies Config;
