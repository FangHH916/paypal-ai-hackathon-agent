// AG Studio in Star's night palette. Every colour is one of the app's own CSS tokens (web/styles.css), so the
// dashboard reads as part of the page, and a change to a token there reaches the widgets, grids and charts too.
import { studioTheme } from 'ag-studio';

const t = (name: string) => `var(--${name})`;
const line = (color: string) => ({ width: 1, color: t(color) });
// Charts draw on a canvas and can't resolve a CSS variable as a font, so the stack is spelled out: the same as --body, with
// 'Plain Figures' first so a 0 is never slashed (docs/DESIGN.md).
const FONT = "'Plain Figures', 'Atkinson Next', 'Segoe UI', 'Helvetica Neue', Arial, sans-serif";

// Series colours: lit cyan first (her money), then calm tones; red-orange is kept for stops, as everywhere in Star.
const SERIES = ['brand', 'ok', 'pend', 'warn', 'accent', 'neutral', 'brand', 'ok', 'pend', 'warn'];
const palette = Object.fromEntries(
  SERIES.flatMap((name, i) => [
    [`chartPaletteFills${i + 1}Color`, t(name)],
    [`chartPaletteStrokes${i + 1}Color`, t(name)],
  ]),
);

export const starTheme = studioTheme.withParams({
  fontFamily: FONT,
  fontSize: 15,
  textColor: t('ink'),
  foregroundColor: t('ink'),
  subtleTextColor: t('muted'),
  backgroundColor: t('surface'),
  borderColor: t('line'),
  accentColor: t('brand'),
  iconColor: t('muted'),
  rowHoverColor: t('surface-2'),

  studioWrapperBorder: false,
  studioWrapperBorderRadius: 0,
  studioWrapperSpacing: 0,
  studioWrapperBackgroundColor: 'transparent',
  studioCanvasBorder: false,
  studioCanvasBorderRadius: 0,
  studioCanvasShadow: false,
  studioCanvasBackgroundColor: 'transparent',
  studioCanvasPadding: 0,
  studioCanvasRowHeight: 18,
  studioCanvasFontFamily: FONT,

  // Widgets are glass panels with a hairline, like the rest of the app.
  studioWidgetBackgroundColor: t('surface'),
  studioWidgetBorder: line('line'),
  studioWidgetBorderRadius: 12,
  studioWidgetPadding: 14,
  studioWidgetTitleFontFamily: FONT,
  studioWidgetTitleFontSize: 15,
  studioWidgetTitleFontWeight: 600,
  studioWidgetTitleTextColor: t('ink'),
  studioWidgetSubtitleFontFamily: FONT,
  studioWidgetSubtitleFontSize: 14,
  studioWidgetSubtitleTextColor: t('muted'),
  studioWidgetCaptionFontFamily: FONT,
  studioWidgetCaptionFontSize: 14,
  studioWidgetCaptionTextColor: t('muted'),
  studioWidgetResizeBorderColor: t('brand'),

  studioPanelContainerBackgroundColor: t('surface'),
  studioPanelContainerBorder: line('line'),
  studioPanelContainerBorderRadius: 12,
  studioPanelDividerColor: t('line'),
  studioPanelDividerActiveColor: t('brand'),
  studioPanelSectionBorderColor: t('line'),
  studioPanelHeaderFontFamily: FONT,
  studioPanelHeaderFontWeight: 600,
  studioPanelChevronButtonHoverBackgroundColor: t('surface-2'),
  studioPanelChevronButtonHoverColor: t('brand'),
  studioToggleButtonActiveBackgroundColor: t('surface-2'),
  studioToggleButtonActiveBorderColor: t('brand'),
  studioToggleButtonActiveColor: t('brand'),

  gridFontFamily: FONT,
  gridFontSize: 15,
  gridCellTextColor: t('ink'),
  gridHeaderBackgroundColor: t('surface'),
  gridHeaderTextColor: t('muted'),
  gridHeaderFontWeight: 600,
  gridHeaderFontSize: 14,
  gridRowHeight: 44,
  gridRowBorder: line('line'),
  gridHeaderRowBorder: line('rule'),
  gridPinnedRowBorder: line('rule'),
  gridRowHoverColor: t('surface-2'),
  gridWrapperBorder: false,

  chartFontFamily: FONT,
  chartTextColor: t('ink'),
  chartSubtleTextColor: t('muted'),
  chartAxisLineColor: t('line'),
  chartGridLineColor: t('line'),
  ...palette,
});
