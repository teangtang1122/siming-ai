import { parseApiDateTime } from '../../utils/dateTime'
import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  Alert,
  Badge,
  Button,
  Empty,
  Input,
  Modal,
  Skeleton,
  Space,
  Switch,
  Tag,
  Tooltip,
  Typography,
  message,
} from 'antd'
import {
  ApiOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  DeleteOutlined,
  DisconnectOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
  LockOutlined,
  MobileOutlined,
  QrcodeOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SaveOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { QRCodeSVG } from 'qrcode.react'
import {
  approveGatewayPairing,
  disableProjectSync,
  enableProjectSync,
  getGatewayCapabilities,
  getGatewayPairingStatus,
  getGatewayStatus,
  listGatewayDevices,
  listSyncConflicts,
  listSyncProjects,
  loginGatewayAdmin,
  resolveSyncConflict,
  revokeGatewayDevice,
  startGatewayPairing,
} from './api'
import type {
  GatewayDevice,
  GatewayRuntimeCapabilities,
  GatewayStatus,
  LauncherGatewaySettings,
  PairingStart,
  PairingStatus,
  SyncConflict,
  SyncProject,
} from './types'
import './GatewaySettingsPanel.css'

const { Paragraph, Text, Title } = Typography

interface GatewaySettingsPanelProps {
  settings: LauncherGatewaySettings | null
  launcherLoading: boolean
  onSave: (values: Partial<LauncherGatewaySettings>) => Promise<void>
}

const PROJECT_STATUS_LABELS: Record<SyncProject['status'], string> = {
  not_enabled: '仅在本机',
  migrating: '正在建档',
  enabled: '已同步',
  disabled: '已暂停',
  error: '需要处理',
}

const ENTITY_LABELS: Record<string, string> = {
  project: '作品资料',
  chapter: '章节',
  chapter_version: '章节版本',
  outline: '大纲',
  character: '角色',
  character_alias: '角色别名',
  character_relation: '角色关系',
  world: '世界观',
  world_relation: '设定关系',
  summary: '摘要',
  timeline: '时间线',
  foreshadowing: '伏笔',
  governance: '叙事治理',
}

const formatDateTime = (value?: string | null) => {
  if (!value) return '尚未记录'
  const parsed = parseApiDateTime(value)
  if (!parsed) return '时间未记录'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  }).format(parsed)
}

const compactHash = (value?: string | null) => (
  value ? `${value.slice(0, 10)}…${value.slice(-8)}` : '—'
)

const responseStatus = (error: unknown) => (
  (error as { response?: { status?: number } } | null)?.response?.status
)

function GatewaySettingsPanel({ settings, launcherLoading, onSave }: GatewaySettingsPanelProps) {
  const [enabled, setEnabled] = useState(false)
  const [advertisedUrl, setAdvertisedUrl] = useState('')
  const [allowedHosts, setAllowedHosts] = useState('')
  const [saving, setSaving] = useState(false)
  const [loading, setLoading] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [adminRequired, setAdminRequired] = useState(false)
  const [bootstrapKey, setBootstrapKey] = useState('')
  const [adminBusy, setAdminBusy] = useState(false)
  const [capabilities, setCapabilities] = useState<GatewayRuntimeCapabilities | null>(null)
  const [status, setStatus] = useState<GatewayStatus | null>(null)
  const [projects, setProjects] = useState<SyncProject[]>([])
  const [devices, setDevices] = useState<GatewayDevice[]>([])
  const [conflicts, setConflicts] = useState<SyncConflict[]>([])
  const [workingProject, setWorkingProject] = useState<string>()
  const [workingConflict, setWorkingConflict] = useState<string>()
  const [pairing, setPairing] = useState<PairingStart | null>(null)
  const [pairingStatus, setPairingStatus] = useState<PairingStatus | null>(null)
  const [pairingBusy, setPairingBusy] = useState(false)
  const [now, setNow] = useState(Date.now())

  const runtimeActive = Boolean(settings?.gateway_runtime_active)
  const headlessGateway = Boolean(settings?.gateway_headless)
  const hasUnsavedSettings = !headlessGateway && Boolean(settings) && (
    enabled !== settings?.gateway_enabled
    || advertisedUrl.trim() !== (settings?.gateway_advertised_url || '')
    || allowedHosts.trim() !== (settings?.gateway_allowed_hosts || '')
  )

  useEffect(() => {
    if (!settings) return
    setEnabled(settings.gateway_enabled)
    setAdvertisedUrl(settings.gateway_advertised_url || '')
    setAllowedHosts(settings.gateway_allowed_hosts || '')
  }, [settings])

  const refreshGateway = useCallback(async () => {
    if (!runtimeActive) return
    setLoading(true)
    setLoadError('')
    try {
      const [nextCapabilities, nextStatus, nextProjects, nextDevices, nextConflicts] = await Promise.all([
        getGatewayCapabilities(),
        getGatewayStatus(),
        listSyncProjects(),
        listGatewayDevices(),
        listSyncConflicts(),
      ])
      setCapabilities(nextCapabilities)
      setStatus(nextStatus)
      setProjects(nextProjects)
      setDevices(nextDevices)
      setConflicts(nextConflicts)
      setAdminRequired(false)
    } catch (error) {
      if (responseStatus(error) === 401) {
        setAdminRequired(true)
        setLoadError('')
      } else {
        setLoadError(error instanceof Error ? error.message : '无法读取 Gateway 状态')
      }
    } finally {
      setLoading(false)
    }
  }, [runtimeActive])

  useEffect(() => {
    if (runtimeActive) void refreshGateway()
  }, [refreshGateway, runtimeActive])

  useEffect(() => {
    if (!pairing) return undefined
    const tick = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(tick)
  }, [pairing])

  useEffect(() => {
    if (!pairing || pairingStatus?.status === 'consumed' || pairingStatus?.status === 'expired') {
      return undefined
    }
    let cancelled = false
    const poll = async () => {
      try {
        const next = await getGatewayPairingStatus(pairing.pairing_id)
        if (!cancelled) {
          setPairingStatus(next)
          if (next.status === 'consumed') void refreshGateway()
        }
      } catch {
        // A transient status poll must not discard the one-time QR secret.
      }
    }
    void poll()
    const interval = window.setInterval(poll, 2500)
    return () => {
      cancelled = true
      window.clearInterval(interval)
    }
  }, [pairing, pairingStatus?.status, refreshGateway])

  const expiresIn = useMemo(() => {
    if (!pairing) return 0
    return Math.max(0, Math.ceil((new Date(pairing.expires_at).getTime() - now) / 1000))
  }, [now, pairing])

  const saveSettings = async () => {
    setSaving(true)
    try {
      await onSave({
        gateway_enabled: enabled,
        gateway_advertised_url: advertisedUrl.trim(),
        gateway_allowed_hosts: allowedHosts.trim(),
      })
    } finally {
      setSaving(false)
    }
  }

  const unlockAdmin = async () => {
    const key = bootstrapKey.trim()
    if (key.length < 12) return
    setAdminBusy(true)
    try {
      await loginGatewayAdmin(key)
      setBootstrapKey('')
      setAdminRequired(false)
      await refreshGateway()
      message.success('Gateway 管理页面已解锁，本次会话 12 小时内有效')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '管理口令验证失败')
    } finally {
      setAdminBusy(false)
    }
  }

  const createPairing = async () => {
    setPairingBusy(true)
    try {
      const next = await startGatewayPairing()
      setPairing(next)
      setPairingStatus({
        pairing_id: next.pairing_id,
        status: 'created',
        expires_at: next.expires_at,
      })
      setNow(Date.now())
    } catch (error) {
      message.error(error instanceof Error ? error.message : '生成配对二维码失败')
    } finally {
      setPairingBusy(false)
    }
  }

  const approvePairing = async () => {
    if (!pairing) return
    setPairingBusy(true)
    try {
      await approveGatewayPairing(pairing.pairing_id)
      const next = await getGatewayPairingStatus(pairing.pairing_id)
      setPairingStatus(next)
      message.success('设备已批准，正在等待手机完成安全连接')
    } catch (error) {
      message.error(error instanceof Error ? error.message : '批准设备失败')
    } finally {
      setPairingBusy(false)
    }
  }

  const confirmEnableProject = (project: SyncProject) => {
    Modal.confirm({
      title: `让《${project.title}》可在手机使用？`,
      content: '司命会先备份数据库，再逐项建档并校验数量与摘要哈希。只迁移这一部作品，不会上传整个作品库。',
      okText: '备份、校验并启用',
      cancelText: '取消',
      onOk: async () => {
        setWorkingProject(project.project_id)
        try {
          await enableProjectSync(project.project_id)
          message.success(`《${project.title}》已完成同步建档`)
          await refreshGateway()
        } catch (error) {
          message.error(error instanceof Error ? error.message : '作品同步建档失败')
          throw error
        } finally {
          setWorkingProject(undefined)
        }
      },
    })
  }

  const confirmDisableProject = (project: SyncProject) => {
    Modal.confirm({
      title: `暂停《${project.title}》的跨设备同步？`,
      content: '电脑上的作品不会删除；手机离线副本也会保留，但在重新启用前不再交换新修改。',
      okText: '暂停同步',
      okButtonProps: { danger: true },
      cancelText: '继续同步',
      onOk: async () => {
        setWorkingProject(project.project_id)
        try {
          await disableProjectSync(project.project_id)
          message.success('已暂停同步，本机数据保持不变')
          await refreshGateway()
        } finally {
          setWorkingProject(undefined)
        }
      },
    })
  }

  const confirmRevokeDevice = (device: GatewayDevice) => {
    Modal.confirm({
      title: `撤销“${device.name}”的访问权限？`,
      content: '该设备现有访问与刷新凭据会立即失效。设备内的离线副本不会被远程删除。',
      okText: '撤销权限',
      okButtonProps: { danger: true },
      cancelText: '取消',
      onOk: async () => {
        await revokeGatewayDevice(device.id)
        message.success('设备权限已撤销')
        await refreshGateway()
      },
    })
  }

  const confirmResolveConflict = (conflict: SyncConflict, choice: 'server' | 'client') => {
    const source = choice === 'server' ? '电脑当前版本' : '手机提交版本'
    Modal.confirm({
      title: `采用${source}？`,
      content: '采用后会生成一个新的同步修订；双方原始快照仍永久保留，便于复核。',
      okText: `采用${source}`,
      cancelText: '再比较一下',
      onOk: async () => {
        setWorkingConflict(conflict.id)
        try {
          await resolveSyncConflict(conflict.id, choice)
          message.success('冲突已处理，原始双方版本仍保留')
          await refreshGateway()
        } finally {
          setWorkingConflict(undefined)
        }
      },
    })
  }

  const activeDevices = devices.filter((device) => device.status === 'approved')
  const pairingState = pairingStatus?.status || 'created'

  return (
    <section className="gateway-panel" aria-labelledby="gateway-title">
      <div className="gateway-hero">
        <div className="gateway-hero-copy">
          <span className="gateway-eyebrow">USER-OWNED SYNC</span>
          <Title id="gateway-title" level={4}>自己的设备，自己的中转站</Title>
          <Paragraph>
            Gateway 是运行在你设备上的同步中枢。司命官方不托管小说正文；电脑、家中常开设备与手机只连接你指定的 Gateway。
          </Paragraph>
          <Space wrap size={8}>
            <Tag icon={<SafetyCertificateOutlined />} color="success">令牌只存哈希</Tag>
            <Tag icon={<SyncOutlined />} color="processing">离线可写</Tag>
            <Tag icon={<CloudServerOutlined />}>LAN · Tailscale · HTTPS</Tag>
          </Space>
        </div>
        <div className={`gateway-orbit ${runtimeActive ? 'gateway-orbit-active' : ''}`} aria-hidden="true">
          <span className="gateway-orbit-center">司命</span>
          <span className="gateway-orbit-node gateway-orbit-desktop"><CloudServerOutlined /></span>
          <span className="gateway-orbit-node gateway-orbit-mobile"><MobileOutlined /></span>
          <span className="gateway-orbit-line" />
        </div>
      </div>

      <div className="gateway-config-grid">
        <div className="gateway-config-main">
          <div className="gateway-config-heading">
            <div>
              <Text strong>{headlessGateway ? 'Docker Gateway 运行配置' : '在这台电脑启用 Gateway'}</Text>
              <Text type="secondary">
                {headlessGateway
                  ? ' 容器模式由 Compose 环境变量管理；此页面不会改写部署配置。'
                  : ' 下次启动后监听局域网；单机创作能力保持不变。'}
              </Text>
            </div>
            <Switch
              checked={enabled}
              checkedChildren="启用"
              unCheckedChildren="关闭"
              aria-label="启用 Gateway"
              disabled={headlessGateway}
              onChange={setEnabled}
            />
          </div>
          <label className="gateway-field">
            <span>公布地址 <Text type="secondary">（可选）</Text></span>
            <Input
              value={advertisedUrl}
              prefix={<LinkOutlined />}
              placeholder="例如 https://siming.example.ts.net"
              aria-label="Gateway 公布地址"
              disabled={headlessGateway}
              onChange={(event) => setAdvertisedUrl(event.target.value)}
            />
            <small>配对时会自动选取合适的局域网地址；Tailscale 或反向代理时再填写。不得包含账号、路径或查询参数。</small>
          </label>
          <label className="gateway-field">
            <span>额外允许的主机名 <Text type="secondary">（高级）</Text></span>
            <Input
              value={allowedHosts}
              placeholder="例如 siming.example.ts.net,192.168.1.20"
              aria-label="Gateway 允许主机"
              disabled={headlessGateway}
              onChange={(event) => setAllowedHosts(event.target.value)}
            />
          </label>
          <Space wrap>
            {!headlessGateway && (
              <Button
                type="primary"
                icon={<SaveOutlined />}
                loading={saving || launcherLoading}
                disabled={!hasUnsavedSettings}
                onClick={saveSettings}
              >
                保存 Gateway 设置
              </Button>
            )}
            <Badge
              status={runtimeActive ? 'success' : 'default'}
              text={runtimeActive ? 'Gateway 正在运行' : '当前仍为单机模式'}
            />
          </Space>
        </div>
        <div className="gateway-boundary-note">
          <ApiOutlined />
          <div>
            <Text strong>没有官方数据服务器</Text>
            <Text>手机找不到 Gateway 时仍可查看和编辑已下载作品，联网后再按修订号同步。</Text>
          </div>
        </div>
      </div>

      {settings && settings.gateway_enabled !== settings.gateway_runtime_active && (
        <Alert
          showIcon
          type="warning"
          message={settings.gateway_enabled ? '设置已保存，需要重启司命才能开放 Gateway' : '设置已保存，重启后会恢复为仅本机访问'}
          description="重启前当前监听范围不会改变，避免在没有明确提示时突然暴露或断开网络服务。"
        />
      )}

      {!runtimeActive && (
        <div className="gateway-disabled-state">
          <div className="gateway-step-rail" aria-label="启用 Gateway 的三个步骤">
            <span><b>01</b> 启用并重启</span>
            <i />
            <span><b>02</b> 逐部作品建档</span>
            <i />
            <span><b>03</b> 手机扫码配对</span>
          </div>
          <Empty
            image={Empty.PRESENTED_IMAGE_SIMPLE}
            description="Gateway 尚未运行；当前作品仍只保存在这台电脑。"
          />
        </div>
      )}

      {runtimeActive && (
        <>
          <div className="gateway-section-heading">
            <div>
              <span className="gateway-section-index">01</span>
              <div>
                <Title level={5}>运行脉搏</Title>
                <Text type="secondary">来自 Gateway 本身的实时状态，不依赖官方云端。</Text>
              </div>
            </div>
            <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void refreshGateway()}>
              刷新状态
            </Button>
          </div>

          {adminRequired ? (
            <section className="gateway-admin-unlock" aria-labelledby="gateway-admin-title">
              <div className="gateway-admin-seal" aria-hidden="true"><LockOutlined /></div>
              <div className="gateway-admin-copy">
                <span className="gateway-eyebrow">GATEWAY ADMIN</span>
                <Title id="gateway-admin-title" level={5}>解锁这台 Gateway 的管理页面</Title>
                <Text type="secondary">
                  输入部署时设置的管理口令。口令只发送给当前 Gateway，不会保存在浏览器；验证后改用 HttpOnly 安全会话。
                </Text>
                <div className="gateway-admin-form">
                  <Input.Password
                    autoComplete="off"
                    value={bootstrapKey}
                    prefix={<SafetyCertificateOutlined />}
                    placeholder="SIMING_GATEWAY_BOOTSTRAP_KEY"
                    aria-label="Gateway 管理口令"
                    onChange={(event) => setBootstrapKey(event.target.value)}
                    onPressEnter={() => void unlockAdmin()}
                  />
                  <Button
                    type="primary"
                    icon={<LockOutlined />}
                    loading={adminBusy}
                    disabled={bootstrapKey.trim().length < 12}
                    onClick={() => void unlockAdmin()}
                  >
                    验证并进入
                  </Button>
                </div>
                <Text className="gateway-admin-hint" type="secondary">
                  忘记口令时，请在运行 Gateway 的设备上更新环境变量后重启；司命官方无法找回你的自托管口令。
                </Text>
              </div>
            </section>
          ) : (
            <>
          {loadError && (
            <Alert
              showIcon
              type="error"
              message="Gateway 状态读取失败"
              description={loadError}
              action={<Button onClick={() => void refreshGateway()}>重试</Button>}
            />
          )}

          {loading && !status ? <Skeleton active paragraph={{ rows: 4 }} /> : (
            <div className="gateway-metrics" aria-label="Gateway 运行指标">
              <div><span>同步游标</span><strong>{status?.cursor ?? '—'}</strong><small>全局有序修订</small></div>
              <div><span>已启用作品</span><strong>{status?.enabled_projects ?? '—'}</strong><small>逐部显式加入</small></div>
              <div><span>在线授权设备</span><strong>{status?.active_devices ?? '—'}</strong><small>可随时撤销</small></div>
              <div className={status?.open_conflicts ? 'gateway-metric-attention' : ''}>
                <span>待处理冲突</span><strong>{status?.open_conflicts ?? '—'}</strong><small>双方版本均保留</small>
              </div>
            </div>
          )}
          <div className="gateway-capability-line">
            <span><CheckCircleOutlined /> Gateway 权威数据源</span>
            <span><CheckCircleOutlined /> 协议 v{capabilities?.sync_protocol_version || status?.protocol_version || 1}</span>
            <span><CheckCircleOutlined /> 删除标记保留 {status?.tombstone_retention_days || 90} 天</span>
            <span><DisconnectOutlined /> 官方服务器不参与</span>
          </div>

          <div className="gateway-section-heading">
            <div>
              <span className="gateway-section-index">02</span>
              <div>
                <Title level={5}>选择可在手机使用的作品</Title>
                <Text type="secondary">每部作品都要单独确认；建档前自动备份，完成后校验数量与摘要哈希。</Text>
              </div>
            </div>
          </div>
          <div className="gateway-project-list">
            {projects.length === 0 && !loading ? (
              <Empty description="作品库还没有可加入同步的作品" />
            ) : projects.map((project) => (
              <article className={`gateway-project gateway-project-${project.status}`} key={project.project_id}>
                <div className="gateway-project-mark" aria-hidden="true">{project.title.slice(0, 1)}</div>
                <div className="gateway-project-body">
                  <div className="gateway-project-title-row">
                    <div>
                      <Text strong>{project.title}</Text>
                      <Tag color={project.status === 'enabled' ? 'success' : project.status === 'error' ? 'error' : 'default'}>
                        {PROJECT_STATUS_LABELS[project.status]}
                      </Tag>
                    </div>
                    {project.status === 'enabled' ? (
                      <Button
                        danger
                        type="text"
                        loading={workingProject === project.project_id}
                        onClick={() => confirmDisableProject(project)}
                      >暂停</Button>
                    ) : (
                      <Button
                        type="primary"
                        loading={workingProject === project.project_id}
                        onClick={() => confirmEnableProject(project)}
                      >
                        {project.status === 'disabled' ? '重新启用' : '加入同步'}
                      </Button>
                    )}
                  </div>
                  {project.status === 'enabled' ? (
                    <div className="gateway-project-proof">
                      <span><b>{project.entity_count}</b> 项资料</span>
                      <span>校验 {formatDateTime(project.verified_at)}</span>
                      <Tooltip title={project.aggregate_hash || '无摘要哈希'}>
                        <span>摘要 {compactHash(project.aggregate_hash)}</span>
                      </Tooltip>
                    </div>
                  ) : (
                    <Text type="secondary">不会因启用 Gateway 而自动迁移；当前只保留在电脑。</Text>
                  )}
                  {project.last_error && <Alert type="error" showIcon message={project.last_error} />}
                </div>
              </article>
            ))}
          </div>

          <div className="gateway-two-column">
            <section className="gateway-device-section">
              <div className="gateway-section-heading gateway-section-heading-compact">
                <div>
                  <span className="gateway-section-index">03</span>
                  <div><Title level={5}>配对手机</Title><Text type="secondary">二维码 10 分钟、一次有效。</Text></div>
                </div>
              </div>
              {!pairing ? (
                <div className="gateway-pairing-empty">
                  <QrcodeOutlined />
                  <Text strong>用司命手机版扫描一次性二维码</Text>
                  <Text type="secondary">手机提交设备信息后，仍需你在这台电脑批准。</Text>
                  <Button type="primary" icon={<QrcodeOutlined />} loading={pairingBusy} onClick={createPairing}>
                    生成配对二维码
                  </Button>
                </div>
              ) : (
                <div className="gateway-pairing-card">
                  <div className="gateway-qr-frame">
                    <QRCodeSVG
                      value={JSON.stringify(pairing.qr_payload)}
                      size={210}
                      level="Q"
                      marginSize={1}
                      title="司命 Gateway 一次性配对二维码"
                      bgColor="#fffdf8"
                      fgColor="#20201f"
                    />
                  </div>
                  <div className="gateway-pairing-copy">
                    <Tag color={pairingState === 'consumed' ? 'success' : pairingState === 'expired' ? 'error' : 'processing'}>
                      {pairingState === 'created' && '等待手机扫描'}
                      {pairingState === 'pending_approval' && '等待电脑批准'}
                      {pairingState === 'approved' && '等待手机取回授权'}
                      {pairingState === 'consumed' && '配对完成'}
                      {pairingState === 'expired' && '二维码已过期'}
                    </Tag>
                    <Title level={5}>{pairing.gateway_name}</Title>
                    <Text code copyable>{pairing.gateway_url}</Text>
                    <Text type="secondary">指纹 {compactHash(pairing.gateway_fingerprint)}</Text>
                    {pairingState === 'created' && <Text>请在 {Math.floor(expiresIn / 60)}:{String(expiresIn % 60).padStart(2, '0')} 内扫描。</Text>}
                    {pairingState === 'pending_approval' && (
                      <Alert
                        showIcon
                        type="warning"
                        message={`确认设备：${pairingStatus?.device_name || '未命名手机'}`}
                        description={`平台：${pairingStatus?.device_platform || 'Android'}。只批准你手中的设备。`}
                      />
                    )}
                    {pairingState === 'approved' && <Text>已批准。请保持手机页面打开，授权会自动完成。</Text>}
                    {pairingState === 'consumed' && <Text>令牌已安全交给手机，二维码不能再次使用。</Text>}
                    <Space wrap>
                      {pairingState === 'pending_approval' && (
                        <Button type="primary" loading={pairingBusy} onClick={approvePairing}>批准这台设备</Button>
                      )}
                      {(pairingState === 'expired' || pairingState === 'consumed') && (
                        <Button onClick={() => { setPairing(null); setPairingStatus(null) }}>
                          {pairingState === 'expired' ? '重新生成' : '关闭二维码'}
                        </Button>
                      )}
                      {!['expired', 'consumed'].includes(pairingState) && (
                        <Button type="text" onClick={() => { setPairing(null); setPairingStatus(null) }}>取消</Button>
                      )}
                    </Space>
                  </div>
                </div>
              )}
            </section>

            <section className="gateway-device-section">
              <div className="gateway-section-heading gateway-section-heading-compact">
                <div>
                  <span className="gateway-section-index">04</span>
                  <div><Title level={5}>已授权设备</Title><Text type="secondary">最近活动与撤销入口。</Text></div>
                </div>
              </div>
              <div className="gateway-device-list">
                {activeDevices.length === 0 ? <Empty description="还没有完成配对的设备" /> : activeDevices.map((device) => (
                  <div className="gateway-device" key={device.id}>
                    <span className="gateway-device-icon">{device.platform === 'android' ? <MobileOutlined /> : <CloudServerOutlined />}</span>
                    <div>
                      <Text strong>{device.name}</Text>
                      <Text type="secondary">{device.platform} · {device.role === 'owner' ? '所有者' : device.role === 'compute' ? '计算节点' : '移动设备'}</Text>
                      <small>最近活动 {formatDateTime(device.last_seen_at || device.approved_at)}</small>
                    </div>
                    <Tooltip title="撤销设备权限">
                      <Button
                        type="text"
                        danger
                        aria-label={`撤销 ${device.name}`}
                        icon={<DeleteOutlined />}
                        onClick={() => confirmRevokeDevice(device)}
                      />
                    </Tooltip>
                  </div>
                ))}
              </div>
            </section>
          </div>

          <div className="gateway-section-heading">
            <div>
              <span className="gateway-section-index">05</span>
              <div>
                <Title level={5}>版本分岔</Title>
                <Text type="secondary">同一资料离线两边都改动时不覆盖：先保留双方，再由你决定。</Text>
              </div>
            </div>
          </div>
          {conflicts.length === 0 ? (
            <div className="gateway-all-clear"><CheckCircleOutlined /><span><b>没有待处理冲突</b><small>所有设备都沿同一条修订线继续。</small></span></div>
          ) : (
            <div className="gateway-conflict-list">
              {conflicts.map((conflict) => (
                <article className="gateway-conflict" key={conflict.id}>
                  <ExclamationCircleOutlined />
                  <div className="gateway-conflict-copy">
                    <div><Text strong>{conflict.project_title}</Text><Tag color="warning">{ENTITY_LABELS[conflict.entity_type] || conflict.entity_type}</Tag></div>
                    <Text>来自 {conflict.device_name || '移动设备'} 的离线修改基于修订 {conflict.client_base_revision}，电脑已到修订 {conflict.server_revision}。</Text>
                    <details>
                      <summary>比较保留的双方快照</summary>
                      <div className="gateway-conflict-snapshots">
                        <pre>{JSON.stringify(conflict.server_payload, null, 2) || '电脑版本为删除记录'}</pre>
                        <pre>{JSON.stringify(conflict.client_payload, null, 2) || '手机版本为删除记录'}</pre>
                      </div>
                    </details>
                    <Space wrap>
                      <Button loading={workingConflict === conflict.id} onClick={() => confirmResolveConflict(conflict, 'server')}>采用电脑版本</Button>
                      <Button type="primary" loading={workingConflict === conflict.id} onClick={() => confirmResolveConflict(conflict, 'client')}>采用手机版本</Button>
                    </Space>
                  </div>
                </article>
              ))}
            </div>
          )}
            </>
          )}
        </>
      )}
    </section>
  )
}

export default GatewaySettingsPanel
