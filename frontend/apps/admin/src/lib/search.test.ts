import { oneOf, str } from './search'

test('a numeric search value (the router JSON-parses ?cursor=25) is kept as its string', () => {
  expect(str(25)).toBe('25')
  expect(str('acme')).toBe('acme')
  expect(str('')).toBeUndefined()
  expect(str(null)).toBeUndefined()
})

test('oneOf keeps only listed values', () => {
  const risk = oneOf(['low', 'high'] as const)
  expect(risk('low')).toBe('low')
  expect(risk('nope')).toBeUndefined()
})
