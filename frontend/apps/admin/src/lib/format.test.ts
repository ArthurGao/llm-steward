import { age, bytes, pct } from './format'

const now = Date.parse('2026-11-09T09:00:00.000Z')

test('ages read as the shortest unit', () => {
  expect(age('2026-11-09T08:59:30.000Z', now)).toBe('now')
  expect(age('2026-11-09T08:15:00.000Z', now)).toBe('45m')
  expect(age('2026-11-09T07:00:00.000Z', now)).toBe('2h')
  expect(age('2026-11-06T09:00:00.000Z', now)).toBe('3d')
  expect(age('2026-08-01T09:00:00.000Z', now)).toBe('2026-08-01')
})

test('a future time reads as "in …"', () => {
  expect(age('2026-11-11T09:00:00.000Z', now)).toBe('in 2d')
})

test('bytes and percentages', () => {
  expect(bytes(1_842_311)).toBe('1.8 MB')
  expect(bytes(18_204)).toBe('18 KB')
  expect(pct(0.021)).toBe('2.1%')
  expect(pct(0.91)).toBe('91%')
})
