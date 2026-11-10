import { render, screen } from '@testing-library/react'
import { StatusBadge } from './StatusBadge'

test('a candidate awaiting review reads "Awaiting review" in the awaiting tone', () => {
  render(<StatusBadge status="awaiting_review" />)
  const badge = screen.getByText('Awaiting review')
  expect(badge).toHaveAttribute('data-tone', 'awaiting')
})

test('verified knowledge uses the verified tone', () => {
  render(<StatusBadge status="verified" />)
  expect(screen.getByText('Verified')).toHaveAttribute('data-tone', 'verified')
})
