import { describe, it, expect } from 'vitest'
import {
  getNumberSeparators,
  digitsToRawAmount,
  formatRawAmount,
  rawAmountFromMasked,
} from './format'

describe('currency mask helpers', () => {
  it('derives pt-BR separators', () => {
    expect(getNumberSeparators('pt-BR')).toEqual({ decimal: ',', thousands: '.' })
    expect(getNumberSeparators('en-US')).toEqual({ decimal: '.', thousands: ',' })
    expect(getNumberSeparators('de-DE')).toEqual({ decimal: ',', thousands: '.' })
  })

  it('digitsToRawAmount: typing flow (centavos)', () => {
    expect(digitsToRawAmount('596604')).toBe('5966.04')
    expect(digitsToRawAmount('5966')).toBe('59.66')
    expect(digitsToRawAmount('500')).toBe('5.00')
    expect(digitsToRawAmount('5')).toBe('0.05')
    expect(digitsToRawAmount('')).toBe('')
    expect(digitsToRawAmount('abc')).toBe('')
    // ignores already-present separators/symbols
    expect(digitsToRawAmount('5.966,04')).toBe('5966.04')
  })

  it('digitsToRawAmount: strips accumulated leading zeros (centavos mask)', () => {
    // typing "3400" — the controlled input feeds back the masked text including
    // the displayed leading zero for values < 1 (0,03 -> 0,034 -> 0,34 ...)
    expect(digitsToRawAmount('0,034')).toBe('0.34')
    expect(digitsToRawAmount('003.40')).toBe('3.40')
    expect(digitsToRawAmount('0034.00')).toBe('34.00')
    expect(digitsToRawAmount('003400')).toBe('34.00')
    // single decimal digit of cents keeps a single leading zero
    expect(digitsToRawAmount('0,05')).toBe('0.05')
  })

  it('formatRawAmount: builds locale mask', () => {
    expect(formatRawAmount('5966.04', 'pt-BR')).toBe('5.966,04')
    expect(formatRawAmount('5966.04', 'en-US')).toBe('5,966.04')
    expect(formatRawAmount('1234.5', 'pt-BR')).toBe('1.234,50')
    expect(formatRawAmount('1000000', 'pt-BR')).toBe('1.000.000,00')
    expect(formatRawAmount('', 'pt-BR')).toBe('')
  })

  it('rawAmountFromMasked reads a pre-filled masked value', () => {
    expect(rawAmountFromMasked('5.966,04')).toBe('5966.04')
    expect(rawAmountFromMasked('5,966.04')).toBe('5966.04')
  })
})
