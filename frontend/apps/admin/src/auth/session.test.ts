import { backendUrl, connect, disconnect, getConnection } from './session'

afterEach(() => disconnect())

test('a backend URL is an http(s) origin plus an optional path, without credentials', () => {
  expect(backendUrl(' https://steward.example.com/ ')).toBe('https://steward.example.com')
  expect(backendUrl('http://127.0.0.1:8600/steward/')).toBe('http://127.0.0.1:8600/steward')
  for (const bad of ['javascript:alert(1)', 'file:///etc/passwd', 'ftp://x', 'https://user:pw@steward.example.com', 'not a url', 'https://h/?q=1', 'https://h/#x']) {
    expect(() => backendUrl(bad), bad).toThrow()
  }
})

test('connect refuses a bad URL and keeps nothing', () => {
  expect(() => connect('javascript:alert(1)', 'token')).toThrow()
  expect(getConnection()).toBeNull()
  connect('https://steward.example.com/', 'token')
  expect(getConnection()).toEqual({ mode: 'live', baseUrl: 'https://steward.example.com', token: 'token' })
})
