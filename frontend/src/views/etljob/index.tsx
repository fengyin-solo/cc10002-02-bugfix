import WorkflowTable, { ActionDef } from '@/components/WorkflowTable'

const ENDPOINT = '/api/etljob'
const columns = [
  '任务编号',
  '输入源',
  '输出目标',
  '转换脚本',
  '依赖任务',
  '调度表达式',
  '数据分区',
  '失败重试次数',
]
// 动作只能从标注的前驱状态发起；状态顺序：草稿 → 待调度 → 运行中 → 已完成/已失败（终态）。
const actions: ActionDef[] = [
  { label: '提交调度', from: '草稿' },
  { label: '启动任务', from: '待调度' },
  { label: '终止任务', from: '运行中' },
  { label: '归档结果', from: '运行中' },
]
const statuses = ['草稿', '待调度', '运行中', '已完成', '已失败']
const negativeStatuses = ['已失败']

export default function EtljobView() {
  return (
    <section className="page" data-module="etljob">
      <header className="page-head">
        <div>
          <h2>ETL任务管理</h2>
          <p className="page-desc">
            维护ETL加工任务的状态流转与动作留痕：状态只能单步向前，终止与完成各为终态，
            跳级、回退、失败终态再次归档及并发竞争只会保留一条合法结论，并在台账中记录原因。
          </p>
        </div>
        <div className="page-actions">
          <button
            className="btn primary"
            type="button"
            onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}
          >
            导出ETL任务清单
          </button>
        </div>
      </header>

      <WorkflowTable
        endpoint={ENDPOINT}
        entityLabel="加工任务"
        columns={columns}
        actions={actions}
        statuses={statuses}
        negativeStatuses={negativeStatuses}
        filterFields={columns.slice(0, 3)}
        statCards={[
          { label: '加工任务总数', match: () => true },
          { label: '运行中任务', match: (row) => row.status === '运行中' },
          { label: '已完成任务', match: (row) => row.status === '已完成' },
          { label: '已失败任务', match: (row) => row.status === '已失败' },
        ]}
      />
    </section>
  )
}
