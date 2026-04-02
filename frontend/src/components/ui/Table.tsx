import { ReactNode } from 'react'

interface TableProps {
  headers: string[]
  data: ReactNode[][]
  className?: string
}

const Table = ({ headers, data, className = '' }: TableProps) => {
  return (
    <div className={`overflow-x-auto ${className}`}>
      <table className="w-full">
        <thead>
          <tr className="border-b border-gray-200">
            {headers.map((header, index) => (
              <th key={index} className="px-4 py-3 text-left text-sm font-medium text-gray-700">
                {header}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {data.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-gray-100 transition-colors hover:bg-gray-50">
              {row.map((cell, cellIndex) => (
                <td key={cellIndex} className="px-4 py-3 text-sm text-gray-900">
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default Table
