import WorkflowTable, { ActionDef } from '@/components/WorkflowTable'

const ENDPOINT = '/api/abtest'
const columns = [
  '方案编号',
  '实验名称',
  '对照组配置',
  '实验组配置',
  '流量分配比例',
  '核心指标',
  '最小样本量',
  '置信水平',
]
// 动作只能从标注的前驱状态发起；状态顺序：草稿 → 待审批 → 运行中 → 已完成/已终止（终态）。
const actions: ActionDef[] = [
  { label: '提交审批', from: '草稿' },
  { label: '启动实验', from: '待审批' },
  { label: '停止实验', from: '运行中' },
  { label: '归档实验', from: '运行中' },
]
const statuses = ['草稿', '待审批', '运行中', '已完成', '已终止']
const negativeStatuses = ['已终止']

export default function AbtestView() {
  return (
    <section className="page" data-module="abtest">
      <header className="page-head">
        <div>
          <h2>AB实验管理</h2>
          <p className="page-desc">
            维护AB实验方案的状态流转与动作留痕：状态只能单步向前，停止与正常归档各为终态，
            跳级、回退与终态再流转会被后端拒绝并在台账中记录原因。
          </p>
        </div>
        <div className="page-actions">
          <button
            className="btn primary"
            type="button"
            onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}
          >
            导出AB实验清单
          </button>
        </div>
      </header>

      <WorkflowTable
        endpoint={ENDPOINT}
        entityLabel="实验方案"
        columns={columns}
        actions={actions}
        statuses={statuses}
        negativeStatuses={negativeStatuses}
        filterFields={columns.slice(0, 3)}
        statCards={[
          { label: '实验方案总数', match: () => true },
          { label: '运行中实验', match: (row) => row.status === '运行中' },
          { label: '已完成实验', match: (row) => row.status === '已完成' },
          { label: '已终止实验', match: (row) => row.status === '已终止' },
        ]}
      />
    </section>
  )
}
