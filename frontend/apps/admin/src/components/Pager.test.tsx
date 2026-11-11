import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { Pager } from './Pager'

test('without a total (backend keyset cursors) it pages forward and back to the first page', async () => {
  const onChange = vi.fn()
  const { rerender } = render(<Pager cursor={null} next="0190-a" total={null} shown={25} onChange={onChange} />)
  expect(screen.getByText('25 shown')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: 'First page' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'Next' }))
  expect(onChange).toHaveBeenLastCalledWith('0190-a')

  rerender(<Pager cursor="0190-a" next={null} total={null} shown={3} onChange={onChange} />)
  expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled()
  await userEvent.click(screen.getByRole('button', { name: 'First page' }))
  expect(onChange).toHaveBeenLastCalledWith(null)
})

test('a single keyset page needs no pager', () => {
  const { container } = render(<Pager cursor={null} next={null} total={null} shown={4} onChange={() => {}} />)
  expect(container).toBeEmptyDOMElement()
})
