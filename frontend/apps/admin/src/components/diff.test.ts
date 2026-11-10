import { wordDiff } from './diff'

test('a changed amount shows as one deletion and one insertion between equal runs', () => {
  expect(wordDiff('over $1,000 require', 'over $500 require')).toEqual([
    { op: 'eq', text: 'over ' },
    { op: 'del', text: '$1,000' },
    { op: 'ins', text: '$500' },
    { op: 'eq', text: ' require' },
  ])
})

test('identical text is one equal run', () => {
  expect(wordDiff('same words', 'same words')).toEqual([{ op: 'eq', text: 'same words' }])
})

test('appended words are an insertion', () => {
  expect(wordDiff('a b', 'a b c')).toEqual([
    { op: 'eq', text: 'a b' },
    { op: 'ins', text: ' c' },
  ])
})
