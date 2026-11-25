import { theme, type ThemeConfig } from "antd";

import tokens from "../design/tokens.json";

const { bg, border, text, semantic, state } = tokens.colors;
const { fontFamily, textStyles } = tokens.typography;

export const sentinelTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: semantic.info,
    colorInfo: semantic.info,
    colorSuccess: semantic.positive,
    colorWarning: semantic.warning,
    colorError: semantic.negative,
    colorLink: semantic.info,
    colorBgBase: bg.primary,
    colorBgLayout: bg.primary,
    colorBgContainer: bg.surface,
    colorBgElevated: bg.elevated,
    colorBorder: border.default,
    colorBorderSecondary: border.subtle,
    colorText: text.primary,
    colorTextSecondary: text.secondary,
    colorTextTertiary: text.muted,
    colorTextDisabled: text.disabled,
    borderRadius: tokens.radii.sm,
    borderRadiusSM: tokens.radii.sm,
    borderRadiusLG: tokens.radii.lg,
    controlHeight: 28,
    controlHeightSM: 24,
    controlHeightLG: 32,
    fontFamily: fontFamily.sans.join(", "),
    fontFamilyCode: fontFamily.mono.join(", "),
    fontSize: textStyles.body.fontSize,
    fontSizeSM: textStyles.secondary.fontSize,
    boxShadow: "none",
    boxShadowSecondary: "none",
    boxShadowTertiary: "none"
  },
  components: {
    Layout: {
      headerHeight: 48,
      headerPadding: "0 24px",
      headerBg: bg.surface,
      bodyBg: bg.primary
    },
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
      dangerShadow: "none",
      fontWeight: 600
    },
    Input: {
      activeShadow: `0 0 0 2px ${state.focusRing}`
    },
    Select: {
      activeOutlineColor: state.focusRing,
      optionSelectedBg: state.selected
    },
    Table: {
      headerBg: bg.surface,
      headerColor: text.muted,
      rowHoverBg: state.hover,
      borderColor: border.subtle,
      cellPaddingBlock: 6,
      cellPaddingInline: 12,
      fontSize: textStyles.body.fontSize
    },
    Tooltip: {
      colorBgSpotlight: bg.elevated,
      colorTextLightSolid: text.primary
    },
    Tabs: {
      itemColor: text.muted,
      itemSelectedColor: text.primary,
      itemHoverColor: text.primary,
      inkBarColor: semantic.info,
      horizontalItemGutter: 24
    },
    Segmented: {
      itemSelectedBg: bg.elevated,
      itemSelectedColor: text.primary,
      trackBg: bg.primary
    }
  }
};
