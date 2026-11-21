import { theme, type ThemeConfig } from "antd";

export const sentinelTheme: ThemeConfig = {
  algorithm: theme.darkAlgorithm,
  token: {
    colorPrimary: "#38BDF8",
    colorInfo: "#38BDF8",
    colorSuccess: "#22C55E",
    colorWarning: "#F59E0B",
    colorError: "#EF4444",
    colorBgBase: "#0B1020",
    colorBgContainer: "#111A2E",
    colorBgElevated: "#16213A",
    colorBorder: "#22304A",
    colorBorderSecondary: "#1A2740",
    colorText: "#E7ECF4",
    colorTextSecondary: "#A8B3C7",
    colorTextTertiary: "#7F8AA3",
    colorTextDisabled: "#5D667C",
    borderRadius: 4,
    borderRadiusLG: 6,
    controlHeight: 40,
    controlHeightSM: 32,
    fontFamily:
      "Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    fontSize: 14,
    boxShadow: "none",
    boxShadowSecondary: "0 4px 12px rgba(0,0,0,0.28)"
  },
  components: {
    Button: {
      primaryShadow: "none",
      defaultShadow: "none",
      dangerShadow: "none",
      fontWeight: 600
    },
    Input: {
      activeShadow: "0 0 0 2px rgba(56,189,248,0.18)"
    },
    Select: {
      activeOutlineColor: "rgba(56,189,248,0.18)",
      optionSelectedBg: "rgba(56,189,248,0.12)"
    },
    Table: {
      headerBg: "#0F1729",
      headerColor: "#7F8AA3",
      rowHoverBg: "rgba(231,236,244,0.04)",
      borderColor: "#1A2740",
      cellPaddingBlock: 10,
      cellPaddingInline: 12,
      fontSize: 13
    },
    Tabs: {
      itemColor: "#7F8AA3",
      itemSelectedColor: "#E7ECF4",
      itemHoverColor: "#E7ECF4",
      inkBarColor: "#38BDF8",
      horizontalItemGutter: 24
    },
    Segmented: {
      itemSelectedBg: "#16213A",
      itemSelectedColor: "#E7ECF4",
      trackBg: "#0B1020"
    }
  }
};
