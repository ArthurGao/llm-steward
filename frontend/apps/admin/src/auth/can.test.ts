import { ACTIONS, can } from './can'

test('a viewer can do nothing that writes', () => {
  for (const action of ACTIONS) expect(can('viewer', action)).toBe(false)
})

test('a reviewer can review and edit knowledge, nothing else (design §9)', () => {
  const allowed = ACTIONS.filter((a) => can('reviewer', a))
  expect(allowed).toEqual(['review', 'editKb'])
})

test('an owner can do everything', () => {
  for (const action of ACTIONS) expect(can('owner', action)).toBe(true)
})
