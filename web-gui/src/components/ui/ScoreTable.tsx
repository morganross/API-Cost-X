import React from 'react'

export interface ScoreColumn<T> {
  header: string
  align?: 'left' | 'center' | 'right'
  headerClass?: string
  cellClass?: string
  cell: (row: T, idx: number) => React.ReactNode
}

interface ScoreTableProps<T> {
  columns: ScoreColumn<T>[]
  rows: T[]
  rowKey: (row: T, idx: number) => string | number
}

export function ScoreTable<T>({ columns, rows, rowKey }: ScoreTableProps<T>) {
  return (
    <div className="rounded-lg border border-gray-700 bg-gray-800 overflow-x-auto">
      <table className="w-full text-sm text-white">
        <thead className="border-b border-gray-700 bg-gray-700/60">
          <tr>
            {columns.map((col, i) => (
              <th
                key={i}
                className={[
                  'px-4 py-3 font-semibold text-white',
                  col.align === 'center' ? 'text-center' : col.align === 'right' ? 'text-right' : 'text-left',
                  col.headerClass ?? '',
                ].join(' ')}
              >
                {col.header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, idx) => (
            <tr key={rowKey(row, idx)} className={idx % 2 === 0 ? 'bg-gray-800' : 'bg-gray-700/30'}>
              {columns.map((col, i) => (
                <td
                  key={i}
                  className={[
                    'px-4 py-2 font-mono text-white',
                    col.align === 'center' ? 'text-center' : col.align === 'right' ? 'text-right' : '',
                    col.cellClass ?? '',
                  ].join(' ')}
                >
                  {col.cell(row, idx)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
