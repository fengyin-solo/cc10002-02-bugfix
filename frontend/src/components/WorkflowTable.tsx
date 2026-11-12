import { useCallback, useEffect, useMemo, useState } from 'react'

import { request } from '@/api/client'

/** 单条业务记录（字段为中文业务列）。 */
type Row = Record<string, string | number | boolean | null>

/** 动作定义：动作名 + 该动作允许发起的前驱状态（用于在页面上标注“合法下一步”）。 */
export interface ActionDef {
  label: string
  from: string
}

/** 操作台账里的一条留痕。 */
interface LedgerRecord {
  id: number
  time: string
  action: string
  from_status: string
  to_status: string
  accepted: boolean
  idempotent: boolean
  reason: string
  source: string
}

interface PageResult {
  items: Row[]
  total: number
}

interface ActionResult {
  ok: boolean
  message: string
  entry: Row | null
  idempotent: boolean
}

interface WorkflowTableProps {
  /** 模块接口前缀，例如 /api/abtest */
  endpoint: string
  /** 模块中文称谓，用于提示文案 */
  entityLabel: string
  /** 列表展示列 */
  columns: string[]
  /** 全部动作及各自的合法前驱状态 */
  actions: ActionDef[]
  /** 状态顺序（用于徽标配色与统计） */
  statuses: string[]
  /** 负向终态（已终止 / 已失败），用于徽标配色 */
  negativeStatuses: string[]
  /** 顶部统计卡片定义，value 由真实列表数据计算 */
  statCards: { label: string; match: (row: Row) => boolean }[]
  /** 参与关键字检索的列 */
  filterFields: string[]
}

/**
 * 动作执行链通用页面：列表、状态徽标、状态感知动作、操作台账抽屉。
 * 动作请求继续发送页面既有的顶层 `{ action }` 协议；后端同时兼容嵌套协议。
 * 关键：HTTP 200 但 ok=false 属于业务拒绝，必须把 message 当作失败原因展示，不能当成成功。
 */
export default function WorkflowTable({
  endpoint,
  entityLabel,
  columns,
  actions,
  statuses,
  negativeStatuses,
  statCards,
  filterFields,
}: WorkflowTableProps) {
  const [rows, setRows] = useState<Row[]>([])
  const [allRows, setAllRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const [statusFilter, setStatusFilter] = useState('')
  const [notice, setNotice] = useState<{ kind: 'ok' | 'error'; text: string } | null>(null)
  const [busyId, setBusyId] = useState<number | null>(null)
  const [ledgerFor, setLedgerFor] = useState<{ id: number; code: string; items: LedgerRecord[] } | null>(null)

  const buildQuery = useCallback(
    (extraSize?: number) => {
      const params = new URLSearchParams()
      // 后端列表只认 keyword（按业务编号检索）与 status：把第一个筛选框映射到 keyword，
      // 避免发后端不识别的列名参数，保证搜索真正生效。
      const keywordValue = filters[filterFields[0]]
      if (keywordValue) params.set('keyword', keywordValue)
      if (statusFilter) params.set('status', statusFilter)
      if (extraSize) params.set('size', String(extraSize))
      const query = params.toString()
      return query ? `?${query}` : ''
    },
    [filters, statusFilter, filterFields],
  )

  const reload = useCallback(async () => {
    try {
      const [pageResp, statResp] = await Promise.all([
        request(`${endpoint}${buildQuery()}`),
        request(`${endpoint}${buildQuery(200)}`),
      ])
      if (!pageResp.ok || !statResp.ok) throw new Error(`${entityLabel}列表读取失败`)
      const page: PageResult = await pageResp.json()
      const stat: PageResult = await statResp.json()
      setRows(page.items ?? [])
      setAllRows(stat.items ?? [])
      setTotal(page.total ?? 0)
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : `${entityLabel}列表读取失败` })
    }
  }, [endpoint, entityLabel, buildQuery])

  useEffect(() => {
    void reload()
  }, [reload])

  const legalActionsByStatus = useMemo(() => {
    const map: Record<string, string[]> = {}
    for (const action of actions) {
      (map[action.from] ??= []).push(action.label)
    }
    return map
  }, [actions])

  const stats = useMemo(
    () => statCards.map((card) => ({ label: card.label, value: allRows.filter(card.match).length })),
    [allRows, statCards],
  )

  async function runAction(action: string, row: Row) {
    const id = Number(row.id)
    setBusyId(id)
    setNotice(null)
    try {
      // 保留页面既有协议：顶层 action。后端同时接受 values.action 嵌套协议。
      const response = await request(`${endpoint}/${id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      if (!response.ok) {
        throw new Error(`请求失败（HTTP ${response.status}），动作未送达`)
      }
      const result = (await response.json()) as ActionResult
      // HTTP 200 不代表业务成功：ok=false 必须按失败展示后端给出的原因。
      if (!result.ok) {
        setNotice({ kind: 'error', text: `已拒绝：${result.message}` })
      } else if (result.idempotent) {
        setNotice({ kind: 'ok', text: result.message })
      } else {
        setNotice({ kind: 'ok', text: result.message })
      }
      await reload()
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : `${entityLabel}操作失败` })
    } finally {
      setBusyId(null)
    }
  }

  async function openLedger(row: Row) {
    const id = Number(row.id)
    try {
      const response = await request(`${endpoint}/${id}/ledger`)
      if (!response.ok) throw new Error('操作台账读取失败')
      const payload = await response.json()
      setLedgerFor({ id, code: String(row[columns[0]] ?? id), items: payload.items ?? [] })
    } catch (error) {
      setNotice({ kind: 'error', text: error instanceof Error ? error.message : '操作台账读取失败' })
    }
  }

  return (
    <>
      <div className="stat-row">
        {stats.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        <label className="filter-item">
          <span>{filterFields[0]}</span>
          <input
            value={filters[filterFields[0]] ?? ''}
            placeholder={`按${filterFields[0]}检索`}
            onChange={(event) => setFilters((prev) => ({ ...prev, [filterFields[0]]: event.target.value }))}
          />
        </label>
        <label className="filter-item">
          <span>状态</span>
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value)}>
            <option value="">全部状态</option>
            {statuses.map((status) => <option key={status} value={status}>{status}</option>)}
          </select>
        </label>
        <button className="btn" type="submit">查询</button>
        <button
          className="btn ghost"
          type="button"
          onClick={() => { setFilters({}); setStatusFilter('') }}
        >
          重置条件
        </button>
      </form>

      {notice && (
        <div className={`notice ${notice.kind === 'ok' ? 'notice-ok' : 'notice-error'}`} role="alert">
          <span>{notice.text}</span>
          <button className="link" type="button" onClick={() => setNotice(null)}>关闭</button>
        </div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>当前状态</th>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
            <th>操作台账</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => {
            const current = String(row.status ?? '')
            const legal = new Set(legalActionsByStatus[current] ?? [])
            const terminal = !statuses.includes(current) || statuses.indexOf(current) >= 3
            return (
              <tr key={String(row.id)}>
                <td>
                  <span className={`status-badge ${negativeStatuses.includes(current) ? 'badge-negative' : terminal ? 'badge-terminal' : 'badge-active'}`}>
                    {current || '未知'}
                  </span>
                </td>
                {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
                <td className="row-actions">
                  {actions.map((action) => {
                    const isLegal = legal.has(action.label)
                    return (
                      <button
                        key={action.label}
                        className={`link ${isLegal ? 'action-legal' : 'action-disabled'}`}
                        type="button"
                        disabled={busyId === Number(row.id)}
                        title={isLegal ? `当前状态「${current}」可执行` : `当前状态「${current}」不能执行该动作，提交将被后端拒绝`}
                        onClick={() => void runAction(action.label, row)}
                      >
                        {action.label}
                      </button>
                    )
                  })}
                </td>
                <td>
                  <button className="link" type="button" onClick={() => void openLedger(row)}>查看台账</button>
                </td>
              </tr>
            )
          })}
          {rows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 3}>暂无{entityLabel}数据</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>共 {total} 条{entityLabel}记录（统计按当前过滤口径实时计算）</span>
      </footer>

      {ledgerFor && (
        <div className="modal-mask" onClick={() => setLedgerFor(null)}>
          <div className="modal-panel" onClick={(event) => event.stopPropagation()}>
            <div className="modal-head">
              <strong>{entityLabel} {ledgerFor.code} 的操作台账</strong>
              <button className="link" type="button" onClick={() => setLedgerFor(null)}>关闭</button>
            </div>
            {ledgerFor.items.length === 0 ? (
              <p className="empty-state">暂无动作记录（历史数据迁移不补造台账）。</p>
            ) : (
              <table className="data-table ledger-table">
                <thead>
                  <tr>
                    <th>序号</th><th>时间</th><th>动作</th><th>变更前</th><th>变更后</th>
                    <th>结论</th><th>原因 / 说明</th>
                  </tr>
                </thead>
                <tbody>
                  {ledgerFor.items.map((record) => (
                    <tr key={record.id} className={record.accepted ? '' : record.idempotent ? 'ledger-idempotent' : 'ledger-rejected'}>
                      <td>{record.id}</td>
                      <td>{record.time}</td>
                      <td>{record.action || '—'}</td>
                      <td>{record.from_status || '—'}</td>
                      <td>{record.to_status || '—'}</td>
                      <td>
                        {record.accepted
                          ? <span className="badge-terminal">已接受</span>
                          : record.idempotent
                            ? <span className="badge-active">幂等未变更</span>
                            : <span className="badge-negative">已拒绝</span>}
                      </td>
                      <td>{record.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      )}
    </>
  )
}
