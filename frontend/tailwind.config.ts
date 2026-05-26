import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
        serif: [
          "Charter",
          "Georgia",
          "Iowan Old Style",
          "Source Serif Pro",
          "Cambria",
          "Times New Roman",
          "serif",
        ],
      },
      colors: {
        ink: "#e7ecf3",
        "ink-2": "#c1c9d4",
        "ink-dim": "#8a94a4",
        panel: "#11141b",
        "panel-2": "#161a23",
        rule: "#232938",
        "rule-soft": "#1b2030",
        accent: "#d6b25c",
        "accent-2": "#f0d28a",
        crit: "#ff4d61",
        high: "#ff9c4a",
        note: "#4dd1ff",
      },
    },
  },
  plugins: [],
};

export default config;
