import type { Config } from "tailwindcss";
import defaultTheme from "tailwindcss/defaultTheme";
import tokens from "./design/tokens.json";

const config: Config = {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    colors: {
      transparent: "transparent",
      current: "currentColor",
      white: tokens.colors.text.primary,
      bg: {
        primary: tokens.colors.bg.primary,
        surface: tokens.colors.bg.surface,
        elevated: tokens.colors.bg.elevated,
        overlay: tokens.colors.bg.overlay
      },
      border: {
        default: tokens.colors.border.default,
        subtle: tokens.colors.border.subtle
      },
      text: {
        primary: tokens.colors.text.primary,
        secondary: tokens.colors.text.secondary,
        muted: tokens.colors.text.muted,
        disabled: tokens.colors.text.disabled,
        inverse: tokens.colors.text.inverse
      },
      semantic: {
        positive: tokens.colors.semantic.positive,
        negative: tokens.colors.semantic.negative,
        warning: tokens.colors.semantic.warning,
        info: tokens.colors.semantic.info,
        neutral: tokens.colors.semantic.neutral
      },
      state: {
        hover: tokens.colors.state.hover,
        active: tokens.colors.state.active,
        selected: tokens.colors.state.selected,
        focusRing: tokens.colors.state.focusRing
      }
    },
    spacing: {
      "0": "0px",
      "4": "4px",
      "8": "8px",
      "12": "12px",
      "16": "16px",
      "20": "20px",
      "24": "24px",
      "32": "32px",
      "40": "40px",
      "48": "48px",
      "64": "64px"
    },
    borderRadius: {
      DEFAULT: `${tokens.radii.sm}px`,
      sm: `${tokens.radii.sm}px`,
      md: `${tokens.radii.md}px`,
      lg: `${tokens.radii.lg}px`
    },
    boxShadow: {
      sm: tokens.shadows.sm,
      md: tokens.shadows.md
    },
    fontFamily: {
      sans: tokens.typography.fontFamily.sans,
      mono: tokens.typography.fontFamily.mono
    },
    fontSize: {
      ...defaultTheme.fontSize,
      "page-title": [
        `${tokens.typography.textStyles.pageTitle.fontSize}px`,
        {
          lineHeight: `${tokens.typography.textStyles.pageTitle.lineHeight}px`,
          fontWeight: `${tokens.typography.textStyles.pageTitle.fontWeight}`,
          letterSpacing: `${tokens.typography.textStyles.pageTitle.letterSpacing}px`
        }
      ],
      "section-title": [
        `${tokens.typography.textStyles.sectionTitle.fontSize}px`,
        {
          lineHeight: `${tokens.typography.textStyles.sectionTitle.lineHeight}px`,
          fontWeight: `${tokens.typography.textStyles.sectionTitle.fontWeight}`,
          letterSpacing: `${tokens.typography.textStyles.sectionTitle.letterSpacing}px`
        }
      ],
      body: [
        `${tokens.typography.textStyles.body.fontSize}px`,
        {
          lineHeight: `${tokens.typography.textStyles.body.lineHeight}px`,
          fontWeight: `${tokens.typography.textStyles.body.fontWeight}`,
          letterSpacing: `${tokens.typography.textStyles.body.letterSpacing}px`
        }
      ],
      secondary: [
        `${tokens.typography.textStyles.secondary.fontSize}px`,
        {
          lineHeight: `${tokens.typography.textStyles.secondary.lineHeight}px`,
          fontWeight: `${tokens.typography.textStyles.secondary.fontWeight}`,
          letterSpacing: `${tokens.typography.textStyles.secondary.letterSpacing}px`
        }
      ],
      "table-header": [
        `${tokens.typography.textStyles.tableHeader.fontSize}px`,
        {
          lineHeight: `${tokens.typography.textStyles.tableHeader.lineHeight}px`,
          fontWeight: `${tokens.typography.textStyles.tableHeader.fontWeight}`,
          letterSpacing: `${tokens.typography.textStyles.tableHeader.letterSpacing}px`
        }
      ]
    },
    zIndex: {
      base: `${tokens.zIndex.base}`,
      sticky: `${tokens.zIndex.sticky}`,
      dropdown: `${tokens.zIndex.dropdown}`,
      drawer: `${tokens.zIndex.drawer}`,
      modal: `${tokens.zIndex.modal}`,
      toast: `${tokens.zIndex.toast}`
    },
    extend: {
      maxWidth: {
        content: "1440px",
        contentWide: "1560px"
      }
    }
  },
  plugins: []
};

export default config;
