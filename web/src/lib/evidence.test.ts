import { describe, expect, it } from 'vitest'

import {
  codePointLength,
  codePointRangeToUtf16Range,
  codePointToUtf16Offset,
  sliceByCodePoints,
  utf16ToCodePointOffset,
} from './evidence'

const EMOJI_TEXT = '年费😀已免'
const NEWLINE_TEXT = '第一行\n第二行'

describe('evidence code point helpers (CT-08)', () => {
  it('counts astral characters as one code point but two UTF-16 units', () => {
    expect(codePointLength(EMOJI_TEXT)).toBe(5)
    expect(EMOJI_TEXT.length).toBe(6) // JS string.length 是 UTF-16 code unit
    expect(codePointLength(NEWLINE_TEXT)).toBe(7)
  })

  it('converts code point offsets to UTF-16 offsets around surrogate pairs', () => {
    expect([0, 1, 2, 3, 4, 5].map((offset) => codePointToUtf16Offset(EMOJI_TEXT, offset))).toEqual([
      0, 1, 2, 4, 5, 6,
    ])
  })

  it('converts UTF-16 offsets back to code point offsets', () => {
    expect([0, 1, 2, 4, 5, 6].map((offset) => utf16ToCodePointOffset(EMOJI_TEXT, offset))).toEqual([
      0, 1, 2, 3, 4, 5,
    ])
  })

  it('rejects out-of-range and mid-surrogate offsets instead of guessing a side', () => {
    expect(() => codePointToUtf16Offset(EMOJI_TEXT, -1)).toThrow(RangeError)
    expect(() => codePointToUtf16Offset(EMOJI_TEXT, 6)).toThrow(RangeError)
    expect(() => utf16ToCodePointOffset(EMOJI_TEXT, 7)).toThrow(RangeError)
    // UTF-16 偏移 3 落在 emoji 的代理对中间。
    expect(() => utf16ToCodePointOffset(EMOJI_TEXT, 3)).toThrow(RangeError)
  })

  it('slices evidence quotes by code points, including emoji and newlines', () => {
    expect(sliceByCodePoints(EMOJI_TEXT, 2, 3)).toBe('😀')
    expect(sliceByCodePoints(EMOJI_TEXT, 2, 5)).toBe('😀已免')
    expect(sliceByCodePoints(NEWLINE_TEXT, 2, 5)).toBe('行\n第')
    expect(() => codePointRangeToUtf16Range(EMOJI_TEXT, 7, 2)).toThrow(RangeError)
    expect(() => codePointRangeToUtf16Range(EMOJI_TEXT, 0, 6)).toThrow(RangeError)
  })

  it('maps a backend evidence range onto a DOM selection range', () => {
    // “免了😀\n不用” start=7 end=13（码点）→ DOM [7,14)（UTF-16）。
    expect(codePointRangeToUtf16Range(EMOJI_TEXT, 2, 5)).toEqual({ start: 2, end: 6 })
  })
})
