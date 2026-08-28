/**
 * Number / date display formatting.
 *
 * The admin-configurable `number_format` setting decides how numbers and dates
 * read:
 *  - Numbers/currency resolve to a representative BCP-47 locale whose
 *    thousands/decimal separators match the setting (`resolveDisplayLocale`).
 *  - Dates keep the user's app language for month/day *words* but take their
 *    field *order* (day-first vs month-first) from the setting
 *    (`resolveDateLocale`). So an English UI on the European format shows
 *    "4 Jun 2026" / "04/06/2026" — never the German "4. Juni".
 */

export type NumberFormat = 'auto' | 'comma_dot' | 'dot_comma' | 'space_comma'

/** Admin date-format setting. 'auto' derives the order from the number format. */
export type DateFormat = 'auto' | 'dmy' | 'mdy' | 'ymd'

export type DateOrder = 'mdy' | 'dmy' | 'ymd'

/** Explicit format → representative locale (separators only matter here). */
const FORMAT_LOCALE: Record<Exclude<NumberFormat, 'auto'>, string> = {
  comma_dot: 'en-US', // 1,000.00
  dot_comma: 'de-DE', // 1.000,00
  space_comma: 'fr-FR', // 1 000,00
}

/**
 * Currency code → locale used when the format is "auto". Picks the separator
 * convention native to each currency (e.g. EUR → 1.000,00, USD → 1,000.00).
 */
const CURRENCY_LOCALE: Record<string, string> = {
  BRL: 'pt-BR',
  USD: 'en-US',
  EUR: 'de-DE',
  GBP: 'en-GB',
  JPY: 'ja-JP',
  CAD: 'en-CA',
  AUD: 'en-AU',
  CHF: 'de-CH',
  CNY: 'zh-CN',
  ARS: 'es-AR',
  MXN: 'es-MX',
  CLP: 'es-CL',
  COP: 'es-CO',
  PEN: 'es-PE',
  UYU: 'es-UY',
  INR: 'en-IN',
  SEK: 'sv-SE',
  DKK: 'da-DK',
  NOK: 'nb-NO',
  PLN: 'pl-PL',
  CZK: 'cs-CZ',
  HUF: 'hu-HU',
  RON: 'ro-RO',
  CRC: 'es-CR',
  IDR: 'id-ID',
  DOP: 'es-DO',
  RUB: 'ru-RU',
  GTQ: 'es-GT',
  PHP: 'en-PH',
}

/**
 * Resolve the locale used for number/date display.
 *
 * @param numberFormat  Admin setting ('auto' or an explicit format).
 * @param currency      The user's display currency — drives "auto".
 * @param fallback      Locale to use when nothing else matches (UI language).
 */
export function resolveDisplayLocale(
  numberFormat: NumberFormat | undefined,
  currency: string | undefined,
  fallback = 'en-US',
): string {
  if (numberFormat && numberFormat !== 'auto') {
    return FORMAT_LOCALE[numberFormat]
  }
  if (currency && CURRENCY_LOCALE[currency]) {
    return CURRENCY_LOCALE[currency]
  }
  return fallback
}

/**
 * Currencies that conventionally write dates month-first (MM/DD). Everything
 * else defaults to day-first, which covers the rest of our supported set.
 */
const MONTH_FIRST_CURRENCIES = new Set(['USD'])

/**
 * Resolve the date field order. An explicit `dateFormat` wins; otherwise
 * ('auto') it derives from the number format, then the currency.
 */
export function resolveDateOrder(
  dateFormat: DateFormat | undefined,
  numberFormat: NumberFormat | undefined,
  currency: string | undefined,
): DateOrder {
  if (dateFormat === 'dmy' || dateFormat === 'mdy' || dateFormat === 'ymd') return dateFormat
  // auto → follow the number format's convention, then the currency.
  if (numberFormat === 'comma_dot') return 'mdy'
  if (numberFormat === 'dot_comma' || numberFormat === 'space_comma') return 'dmy'
  return currency && MONTH_FIRST_CURRENCIES.has(currency) ? 'mdy' : 'dmy'
}

/** Representative English locale per order (numbers carry no language). */
const ORDER_EN_LOCALE: Record<DateOrder, string> = {
  dmy: 'en-GB', // 04/06/2026
  mdy: 'en-US', // 6/4/2026
  ymd: 'en-CA', // 2026-06-04
}

/**
 * Resolve the locale used for *dates*. Keeps the app language (so month names
 * stay translated) but picks a regional variant whose date order matches the
 * chosen format. English exposes all three orders (en-US/en-GB/en-CA); pt-BR
 * and es are day-first natively. When a non-English UI is paired with a
 * non-native order we fall back to an English proxy so the order is still
 * honored (a rare combination).
 *
 * @param dateFormat    Admin date-format setting ('auto' or explicit order).
 * @param numberFormat  Number-format setting — feeds the 'auto' date order.
 * @param currency      Display currency — feeds 'auto' when number is 'auto'.
 * @param language      The resolved UI language ('en', 'pt-BR', 'es', …).
 */
export function resolveDateLocale(
  dateFormat: DateFormat | undefined,
  numberFormat: NumberFormat | undefined,
  currency: string | undefined,
  language: string,
): string {
  const order = resolveDateOrder(dateFormat, numberFormat, currency)
  if (language.startsWith('en')) return ORDER_EN_LOCALE[order]
  if (order === 'dmy') return language
  return ORDER_EN_LOCALE[order]
}

/**
 * Format a numeric value as currency using Intl.NumberFormat.
 */
export function formatCurrency(
  value: number | null | undefined,
  currency = 'USD',
  locale = 'en-US',
): string {
  if (value == null) return '—'
  return new Intl.NumberFormat(locale, {
    style: 'currency',
    currency,
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(value)
}

/**
 * Derive the decimal and thousands separators used by a given locale. Falls
 * back to "1,234.56" (en-US) when Intl is not available in the environment.
 */
export function getNumberSeparators(
  locale = 'en-US',
): { decimal: string; thousands: string } {
  try {
    const parts = new Intl.NumberFormat(locale).formatToParts(1234.5)
    let decimal = '.'
    let thousands = ','
    for (const p of parts) {
      if (p.type === 'decimal') decimal = p.value
      else if (p.type === 'group') thousands = p.value
    }
    return { decimal, thousands }
  } catch {
    return { decimal: '.', thousands: ',' }
  }
}

/**
 * Currency input helpers used by every editable monetary field.
 *
 * The internal value stored in React state is always a *plain* numeric string
 * with a dot decimal separator (e.g. `"5966.04"`) — same format the fields use
 * today, so `parseFloat()` at submit keeps working unchanged.
 *
 * Typing follows the common "centavos" mask: each new digit shifts the value
 * left, so the last two digits are the cents. Typing `596604` → displayed
 * `5.966,04`, stored `5966.04`. Because the mask is derived from the *digits*
 * of the stored value, editing anywhere stays consistent and the cursor stays
 * at the end (acceptable for number entry).
 */

/**
 * Build the raw (dot-decimal) value from a user keystroke. Digits are kept,
 * the last two become cents. Returns `""` when nothing numeric remains.
 *
 * The controlled input's `onChange` feeds back the *formatted* (masked) text,
 * which for values < 1 includes the displayed leading zero (e.g. `0,34`). Normally
 * that leading zero would re-enter the digit stream on every keystroke and
 * accumulate (`0,034` → `00.34`). We therefore strip leading zeros from the
 * integer part so typing `3400` yields `34.00`, never `00.34`/`0034.00`.
 */
export function digitsToRawAmount(value: string): string {
  const digits = (value ?? '').replace(/\D/g, '')
  if (!digits) return ''
  const int = (digits.slice(0, -2) || '0').replace(/^0+/, '') || '0'
  const dec = digits.slice(-2).padStart(2, '0')
  return `${int}.${dec}`
}

/**
 * Format a raw (dot-decimal) amount for display with the locale's separators
 * and exactly two decimals. `"5966.04"` + pt-BR → `"5.966,04"`.
 */
export function formatRawAmount(raw: string, locale = 'en-US'): string {
  if (!raw) return ''
  const { decimal, thousands } = getNumberSeparators(locale)
  const [intPart, decPart] = raw.split('.')
  const int = (intPart || '0').replace(/\B(?=(\d{3})+(?!\d))/g, thousands)
  const dec = (decPart || '').padEnd(2, '0').slice(0, 2)
  return `${int}${decimal}${dec}`
}

/**
 * Convenience: build the raw amount string from the displayed, locale-masked
 * string (e.g. parsing a pre-filled seed that was formatted).
 */
export function rawAmountFromMasked(masked: string): string {
  const digits = (masked ?? '').replace(/\D/g, '')
  return digitsToRawAmount(digits)
}
