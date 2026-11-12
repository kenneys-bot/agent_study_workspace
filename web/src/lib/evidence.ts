/**
 * 契约 §4 的证据坐标换算（CT-08）。
 *
 * 后端证据的 `start`/`end` 是不可变原文的 **Unicode 码点**偏移（start 含、
 * end 不含）；JavaScript 字符串的下标是 **UTF-16 code unit**。中文通常一对一，
 * 但 emoji 等星平面字符占两个 code unit，直接把后端偏移当 JS 下标会切错位置。
 *
 * 本模块是前端唯一的换算入口；`Text` 节点、DOM `Range` 与 `slice` 都必须先
 * 经过这里。落在代理对中间的偏移直接抛错，不静默对齐到某一侧。
 */

export interface CodePointRange {
  start: number
  end: number
}

function assertInteger(value: number, name: string): void {
  if (!Number.isInteger(value)) {
    throw new RangeError(`${name} 必须是整数`)
  }
}

export function codePointLength(text: string): number {
  let count = 0
  for (const _ of text) {
    count += 1
  }
  return count
}

export function codePointToUtf16Offset(text: string, offset: number): number {
  assertInteger(offset, '码点偏移')
  if (offset < 0 || offset > codePointLength(text)) {
    throw new RangeError('码点偏移超出原文范围')
  }
  let units = 0
  let points = 0
  for (const char of text) {
    if (points === offset) {
      return units
    }
    units += char.length
    points += 1
  }
  return units
}

export function utf16ToCodePointOffset(text: string, offset: number): number {
  assertInteger(offset, 'UTF-16 偏移')
  if (offset < 0 || offset > text.length) {
    throw new RangeError('UTF-16 偏移超出原文范围')
  }
  let units = 0
  let points = 0
  for (const char of text) {
    if (units === offset) {
      return points
    }
    units += char.length
    if (units > offset) {
      throw new RangeError('UTF-16 偏移落在代理对中间')
    }
    points += 1
  }
  return points
}

export function codePointRangeToUtf16Range(
  text: string,
  start: number,
  end: number,
): CodePointRange {
  assertInteger(start, 'start')
  assertInteger(end, 'end')
  if (start < 0 || end <= start || end > codePointLength(text)) {
    throw new RangeError('码点区间非法')
  }
  return {
    start: codePointToUtf16Offset(text, start),
    end: codePointToUtf16Offset(text, end),
  }
}

/** 按后端码点区间截取原文；用于预览证据 quote 是否与原文一致。 */
export function sliceByCodePoints(text: string, start: number, end: number): string {
  const range = codePointRangeToUtf16Range(text, start, end)
  return text.slice(range.start, range.end)
}
