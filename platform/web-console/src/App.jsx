import { useState, useEffect } from 'react'
import { Routes, Route, Navigate, Link, useNavigate, useParams } from 'react-router-dom'
import { api } from './api'
import './styles.css'

// ─── Preload cached data on app startup ───
if (api.isLoggedIn()) {
  api.getAvailableChannels().catch(() => {})
  api.getLlmProviders().catch(() => {})
}

// ─── Auth Context ───
function useAuth() {
  const [, setTick] = useState(0)
  return {
    isLoggedIn: api.isLoggedIn(),
    logout: () => { api.setToken(null); api.setUser(null); setTick(t => t + 1) },
    refresh: () => setTick(t => t + 1),
  }
}

// ─── Navbar ───
function Navbar({ auth }) {
  return (
    <nav className="navbar">
      <Link to="/" className="navbar-brand">🦐 OpenClaw SaaS</Link>
      <div className="navbar-right">
        {auth.isLoggedIn && (
          <>
            {api.isPlatformAdmin() && <span className="badge badge-purple" style={{fontSize:'0.6875rem'}}>Platform Admin</span>}
            <Link to="/">Dashboard</Link>
            <Link to="/plans">Plans</Link>
            <button className="btn btn-sm" onClick={auth.logout}>Logout</button>
          </>
        )}
      </div>
    </nav>
  )
}

// ─── Login Page ───
function LoginPage({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    try {
      const res = await api.login(email, password)
      api.setToken(res.access_token)
      api.setUser(res.user)
      onLogin()
      navigate('/')
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="auth-page">
      <div className="auth-box">
        <h1>🦐 OpenClaw SaaS</h1>
        <p>Sign in to manage your agents</p>
        <div className="card">
          {error && <div className="alert alert-error">{error}</div>}
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Email</label>
              <input className="form-input" type="email" value={email} onChange={e => setEmail(e.target.value)} required />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input className="form-input" type="password" value={password} onChange={e => setPassword(e.target.value)} required />
            </div>
            <button className="btn btn-primary" style={{width:'100%'}}>Sign In</button>
          </form>
          <p style={{textAlign:'center', marginTop:'16px', fontSize:'0.875rem'}}>
            Don't have an account? <Link to="/signup">Sign Up</Link>
          </p>
        </div>
      </div>
    </div>
  )
}

// ─── Signup Page ───
function SignupPage({ onLogin }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const navigate = useNavigate()

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError('')
    try {
      await api.signup(email, password, name)
      const res = await api.login(email, password)
      api.setToken(res.access_token)
      api.setUser(res.user)
      onLogin()
      navigate('/')
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="auth-page">
      <div className="auth-box">
        <h1>🦐 Create Account</h1>
        <p>Your email must be pre-approved by a platform admin</p>
        <div className="card">
          {error && <div className="alert alert-error">{error}</div>}
          <form onSubmit={handleSubmit}>
            <div className="form-group">
              <label>Display Name</label>
              <input className="form-input" value={name} onChange={e => setName(e.target.value)} placeholder="Your name" />
            </div>
            <div className="form-group">
              <label>Email</label>
              <input className="form-input" type="email" value={email} onChange={e => setEmail(e.target.value)} required />
            </div>
            <div className="form-group">
              <label>Password</label>
              <input className="form-input" type="password" value={password} onChange={e => setPassword(e.target.value)} required minLength={8} />
            </div>
            <button className="btn btn-primary" style={{width:'100%'}}>Create Account</button>
          </form>
          <p style={{textAlign:'center', marginTop:'16px', fontSize:'0.875rem'}}>
            Already have an account? <Link to="/login">Sign In</Link>
          </p>
        </div>
      </div>
    </div>
  )
}

// ─── Dashboard Page ───
function DashboardPage() {
  const [tenants, setTenants] = useState([])
  const [newName, setNewName] = useState('')
  const [newPlan, setNewPlan] = useState('free')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [adminStats, setAdminStats] = useState(null)
  const navigate = useNavigate()
  const isAdmin = api.isPlatformAdmin()

  useState(() => {
    api.getDashboard().then(data => {
      setTenants(data.tenants)
      if (data.admin_stats) setAdminStats(data.admin_stats)
    }).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  const createTenant = async (e) => {
    e.preventDefault()
    if (!newName.trim()) return
    setError('')
    try {
      const t = await api.createTenant(newName.trim(), newPlan)
      setTenants([...tenants, t])
      setNewName('')
      setNewPlan('free')
    } catch (err) { setError(err.message) }
  }

  const deleteTenantFromList = async (name) => {
    if (!confirm(`Delete tenant "${name}"? This will remove ALL agents, members, and namespace. This cannot be undone.`)) return
    try {
      await api.deleteTenant(name)
      setTenants(tenants.filter(t => t.name !== name))
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="container">
      <div className="page-header">
        <h1>{isAdmin ? '🛡️ Platform Admin Dashboard' : 'Dashboard'}</h1>
        <p>{isAdmin ? 'Manage all tenants and monitor platform usage' : 'Manage your tenants and agents'}</p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Platform Admin Overview */}
      {isAdmin && adminStats && (
        <div className="card" style={{ marginBottom: '16px' }}>
          <div className="card-header">
            <span className="card-title">📊 Platform Overview (This Month)</span>
          </div>
          <div className="billing-stats">
            <div className="stat-box">
              <div className="stat-label">Tenants</div>
              <div className="stat-value">{adminStats.total_tenants}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Agents</div>
              <div className="stat-value">{adminStats.total_agents}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Users</div>
              <div className="stat-value">{adminStats.total_users}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Total Cost</div>
              <div className="stat-value">${adminStats.current_month?.estimated_cost?.toFixed(4) ?? '0'}</div>
            </div>
          </div>
          {adminStats.tenants?.length > 0 && (
            <div className="usage-table" style={{ marginTop: '12px' }}>
              <div className="usage-row usage-header">
                <span>Tenant</span><span>Plan</span><span>Agents</span><span>Tokens</span><span>Cost</span>
              </div>
              {adminStats.tenants.map(t => (
                <div key={t.name} className="usage-row">
                  <span><Link to={`/tenants/${t.name}`} style={{ color: 'var(--text-link)' }}>{t.name}</Link></span>
                  <span><span className="badge badge-blue">{t.plan}</span></span>
                  <span>{t.agent_count}</span>
                  <span>{t.tokens_used >= 1000 ? (t.tokens_used / 1000).toFixed(1) + 'K' : t.tokens_used}</span>
                  <span>${t.cost.toFixed(4)}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="card">
        <div className="card-header">
          <span className="card-title">Tenants</span>
        </div>
        {isAdmin && (
          <form onSubmit={createTenant} style={{display:'flex', gap:'8px', marginBottom:'16px'}}>
            <input className="form-input" value={newName} onChange={e => setNewName(e.target.value)}
              placeholder="tenant-name (lowercase, a-z, 0-9, hyphens)" style={{flex:1}} />
            <select className="form-input" value={newPlan} onChange={e => setNewPlan(e.target.value)} style={{width:'140px'}}>
              <option value="free">Free</option>
              <option value="pro">Pro</option>
              <option value="enterprise">Enterprise</option>
            </select>
            <button className="btn btn-primary" type="submit">Create</button>
          </form>
        )}
        {loading ? <p>Loading...</p> : tenants.length === 0 ? (
          <div className="empty"><div className="empty-icon">📦</div><p>No tenants yet. Create one above.</p></div>
        ) : tenants.map(t => (
          <div key={t.id} className="agent-item">
            <div className="agent-info">
              <Link to={`/tenants/${t.name}`} className="agent-name">{t.name}</Link>
              <span className="badge badge-blue">{t.plan}</span>
              {t.role && <span className={`badge ${t.role === 'owner' ? 'badge-purple' : 'badge-green'}`}>{t.role}</span>}
            </div>
            <div style={{display:'flex', gap:'8px', alignItems:'center'}}>
              <Link to={`/tenants/${t.name}/billing`} className="btn btn-sm">📊 Billing</Link>
              {isAdmin && <button className="btn btn-sm btn-danger" onClick={() => deleteTenantFromList(t.name)}>🗑</button>}
              <span style={{color:'var(--text-secondary)', fontSize:'0.75rem'}}>
                Created: {new Date(t.created_at).toLocaleDateString()}
              </span>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Tenant Detail Page ───
function TenantPage() {
  const [agents, setAgents] = useState([])
  const [members, setMembers] = useState([])
  const [myRole, setMyRole] = useState(null) // owner | admin | member
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')
  const [loading, setLoading] = useState(true)
  const [channelModal, setChannelModal] = useState(null)
  const [createModal, setCreateModal] = useState(false)
  const [logsModal, setLogsModal] = useState(null)
  const tenantName = window.location.pathname.split('/tenants/')[1]?.split('/')[0]
  const navigate = useNavigate()
  const isAdmin = api.isPlatformAdmin()

  const [allowedEmails, setAllowedEmails] = useState([])
  const [billingInfo, setBillingInfo] = useState(null)

  // Role helpers
  const isOwnerOrAdmin = isAdmin || myRole === 'owner' || myRole === 'admin'
  const isOwner = isAdmin || myRole === 'owner'

  // Single reload function — replaces separate loadAgents + loadMembers
  const reload = () => {
    api.getTenantDashboard(tenantName).then(data => {
      setAgents(data.agents)
      setMembers(data.members)
      setMyRole(data.role)
      setBillingInfo(data.billing)
      setAllowedEmails(data.allowed_emails || [])
    }).catch(e => setError(e.message))
  }
  useState(() => {
    // Single aggregated API call replaces 4-5 separate requests
    api.getTenantDashboard(tenantName).then(data => {
      setAgents(data.agents)
      setMembers(data.members)
      setMyRole(data.role)
      setBillingInfo(data.billing)
      setAllowedEmails(data.allowed_emails || [])
    }).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [])

  // Auto-refresh when any agent has gateway enabled but URL not yet ready
  useEffect(() => {
    const hasPending = agents.some(a => a.gateway_enabled && !a.gateway_url)
    if (!hasPending) return
    const interval = setInterval(reload, 10000)
    return () => clearInterval(interval)
  }, [agents])

  const deleteAgent = async (id) => {
    if (!confirm('Delete this agent? This will stop the pod.')) return
    try {
      await api.deleteAgent(tenantName, id)
      reload()
    } catch (err) { setError(err.message) }
  }

  const deleteTenant = async () => {
    if (!confirm(`Delete tenant "${tenantName}"? This will remove ALL agents, members, and namespace. This cannot be undone.`)) return
    try {
      await api.deleteTenant(tenantName)
      navigate('/')
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="container">
      <div className="page-header">
        <h1>Tenant: {tenantName} {myRole && <span className={`badge ${myRole === 'owner' ? 'badge-purple' : myRole === 'admin' ? 'badge-green' : 'badge-blue'}`} style={{fontSize:'0.75rem',verticalAlign:'middle'}}>{myRole}</span>}</h1>
        <p>
          <Link to="/">← Back to Dashboard</Link>
          {' · '}
          <Link to={`/tenants/${tenantName}/billing`}>📊 Billing & Usage</Link>
          {isOwner && <>
            {' · '}
            <button className="btn btn-sm btn-danger" onClick={deleteTenant} style={{verticalAlign:'middle'}}>🗑 Delete Tenant</button>
          </>}
        </p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}
      {success && <div className="alert alert-success">{success}</div>}

      <div className="card">
        <div className="card-header">
          <span className="card-title">Agents</span>
          {myRole && <button className="btn btn-primary btn-sm" onClick={() => setCreateModal(true)}>+ Create Agent</button>}
        </div>
        {loading ? <p>Loading...</p> : agents.length === 0 ? (
          <div className="empty"><div className="empty-icon">🤖</div><p>No agents yet. Click "Create Agent" above.</p></div>
        ) : agents.map(a => (
          <div key={a.id} className="agent-item">
            <div className="agent-info">
              <span className="agent-name">{a.name}</span>
              <span className={`badge ${a.status === 'running' ? 'badge-green' : 'badge-orange'}`}>{a.status}</span>
              <span className="badge badge-blue">{a.llm_provider || 'bedrock-irsa'}</span>
              {a.llm_model && <span style={{color:'var(--text-secondary)', fontSize:'0.6875rem'}}>{a.llm_model}</span>}
              <div className="agent-channels">
                {(a.channels || []).map(ch => <span key={ch} className="channel-chip">{ch}</span>)}
              </div>
            </div>
            <div style={{display:'flex', gap:'6px'}}>
              {a.gateway_enabled && (a.gateway_url
                ? <a href={a.gateway_url} target="_blank" rel="noopener noreferrer" className="btn btn-sm btn-primary">🔗 Gateway</a>
                : <button className="btn btn-sm" disabled style={{opacity:0.5, cursor:'not-allowed'}}>🔗 Gateway (provisioning...)</button>
              )}
              <button className="btn btn-sm" onClick={() => setLogsModal({agentId: a.id, agentName: a.name})}>📋 Logs</button>
              {myRole && <button className="btn btn-sm" onClick={() => setChannelModal({agentId: a.id, agentName: a.name})}>+ Channel</button>}
              {myRole && <button className="btn btn-sm btn-danger" onClick={() => deleteAgent(a.id)}>Delete</button>}
            </div>
          </div>
        ))}
      </div>

      {/* Allowed Emails — admin+ only */}
      {isOwnerOrAdmin && <AllowedEmailsCard tenantName={tenantName} initialEmails={allowedEmails} />}

      {/* Members */}
      <div className="card" style={{ marginTop: '16px' }}>
        <div className="card-header">
          <span className="card-title">Members</span>
        </div>
        {members.length === 0 ? (
          <p style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontSize: '0.8125rem' }}>No members yet.</p>
        ) : (
          <div className="usage-table">
            <div className="usage-row usage-header"><span>Email</span><span>Name</span><span>Role</span><span>Joined</span><span></span></div>
            {members.map(m => (
              <div key={m.user_id} className="usage-row">
                <span>{m.email}</span>
                <span>{m.display_name || '—'}</span>
                <span className={`badge ${m.role === 'owner' ? 'badge-blue' : m.role === 'admin' ? 'badge-green' : ''}`}>{m.role}</span>
                <span style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>{new Date(m.joined_at).toLocaleDateString()}</span>
                <span>{m.role !== 'owner' && isOwnerOrAdmin && <button className="btn btn-sm btn-danger" onClick={async () => {
                  if (!confirm(`Remove ${m.email} from this tenant?`)) return
                  try { await api.removeMember(tenantName, m.user_id); reload() } catch (err) { setError(err.message) }
                }}>Remove</button>}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {createModal && (
        <CreateAgentModal
          tenantName={tenantName}
          onClose={() => { setCreateModal(false); reload() }}
          onSuccess={(msg) => { setSuccess(msg); setCreateModal(false); reload() }}
          onError={setError}
        />
      )}

      {channelModal && (
        <ChannelModal
          tenantName={tenantName}
          agentId={channelModal.agentId}
          agentName={channelModal.agentName}
          onClose={() => { setChannelModal(null); reload() }}
        />
      )}

      {logsModal && (
        <LogsModal
          tenantName={tenantName}
          agentId={logsModal.agentId}
          agentName={logsModal.agentName}
          onClose={() => setLogsModal(null)}
        />
      )}
    </div>
  )
}

// ─── Allowed Emails Card ───
function AllowedEmailsCard({ tenantName, initialEmails }) {
  const [emails, setEmails] = useState(initialEmails || [])
  const [newEmail, setNewEmail] = useState('')
  const [newRole, setNewRole] = useState('member')
  const [loading, setLoading] = useState(!initialEmails)
  const [error, setError] = useState('')

  const load = () => {
    api.getAllowedEmails(tenantName).then(setEmails).catch(() => {}).finally(() => setLoading(false))
  }
  useState(() => { if (!initialEmails) load() }, [])

  const addEmail = async (e) => {
    e.preventDefault()
    if (!newEmail) return
    try {
      await api.addAllowedEmail(tenantName, newEmail, newRole)
      setNewEmail('')
      load()
    } catch (err) { setError(err.message) }
  }

  const removeEmail = async (id) => {
    try {
      await api.removeAllowedEmail(tenantName, id)
      load()
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="card" style={{ marginTop: '16px' }}>
      <div className="card-header">
        <span className="card-title">Allowed Signup Emails</span>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <form onSubmit={addEmail} style={{ display: 'flex', gap: '8px', padding: '12px 16px', borderBottom: '1px solid var(--border)' }}>
        <input className="form-input" value={newEmail} onChange={e => setNewEmail(e.target.value)} placeholder="user@example.com" type="email" style={{ flex: 1 }} />
        <select className="form-input" value={newRole} onChange={e => setNewRole(e.target.value)} style={{ width: '120px' }}>
          <option value="member">Member</option>
          <option value="admin">Admin</option>
        </select>
        <button className="btn btn-primary btn-sm" type="submit">Add</button>
      </form>
      {loading ? <p style={{ padding: '12px 16px' }}>Loading...</p> : emails.length === 0 ? (
        <p style={{ padding: '12px 16px', color: 'var(--text-secondary)', fontSize: '0.8125rem' }}>No emails added. Add emails to allow users to sign up for this tenant.</p>
      ) : (
        <div className="usage-table">
          <div className="usage-row usage-header"><span>Email</span><span>Role</span><span>Status</span><span></span></div>
          {emails.map(e => (
            <div key={e.id} className="usage-row">
              <span>{e.email}</span>
              <span className="badge">{e.role}</span>
              <span>{e.used ? <span className="badge badge-green">Registered</span> : <span className="badge badge-orange">Pending</span>}</span>
              <span>{!e.used && <button className="btn btn-sm btn-danger" onClick={() => removeEmail(e.id)}>Remove</button>}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ─── Create Agent Modal ───
function CreateAgentModal({ tenantName, onClose, onSuccess, onError }) {
  const [name, setName] = useState('')
  const [providers, setProviders] = useState(null)
  const [provider, setProvider] = useState('bedrock-irsa')
  const [model, setModel] = useState('')
  const [apiKeys, setApiKeys] = useState({})
  const [enableChromium, setEnableChromium] = useState(false)
  const [enableGateway, setEnableGateway] = useState(false)
  const [showAdvanced, setShowAdvanced] = useState(false)
  const [customImage, setCustomImage] = useState('')
  const [customImageTag, setCustomImageTag] = useState('')
  const [runtimeClassName, setRuntimeClassName] = useState('')
  const [nodeSelector, setNodeSelector] = useState('')
  const [tolerations, setTolerations] = useState('')
  const [error, setError] = useState('')
  const [submitting, setSubmitting] = useState(false)

  // Load providers on mount
  useState(() => {
    api.getLlmProviders().then(p => {
      setProviders(p)
      setModel(p['bedrock-irsa']?.default_model || '')
    }).catch(e => setError(e.message))
  }, [])

  const currentProvider = providers?.[provider]

  const handleProviderChange = (p) => {
    setProvider(p)
    setApiKeys({})
    if (providers?.[p]) setModel(providers[p].default_model)
  }

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(''); setSubmitting(true)
    try {
      const parsedNodeSelector = nodeSelector.trim() ? Object.fromEntries(
        nodeSelector.split(',').map(s => s.trim().split('=').map(v => v.trim())).filter(([k]) => k)
      ) : undefined
      const parsedTolerations = tolerations.trim() ? tolerations.split('\n').map(line => {
        const parts = Object.fromEntries(line.split(',').map(s => s.trim().split('=').map(v => v.trim())).filter(([k]) => k))
        return parts
      }).filter(t => Object.keys(t).length) : undefined
      await api.createAgent(tenantName, name.trim(), {
        llmProvider: provider,
        llmModel: model || undefined,
        llmApiKeys: Object.keys(apiKeys).length ? apiKeys : undefined,
        enableChromium,
        enableGateway,
        customImage: customImage.trim() || undefined,
        customImageTag: customImageTag.trim() || undefined,
        runtimeClassName: runtimeClassName.trim() || undefined,
        nodeSelector: parsedNodeSelector,
        tolerations: parsedTolerations,
      })
      onSuccess('Agent created! Pod is starting...')
    } catch (err) {
      setError(err.message)
    } finally { setSubmitting(false) }
  }

  return (
    <div className="modal-overlay">
      <div className="modal" onClick={e => e.stopPropagation()} style={{maxWidth:'520px', maxHeight:'85vh', overflowY:'auto'}}>
        <h2>Create Agent</h2>
        {error && <div className="alert alert-error">{error}</div>}
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Agent Name</label>
            <input className="form-input" value={name} onChange={e => setName(e.target.value)}
              placeholder="my-agent (lowercase, a-z, 0-9, hyphens)" required pattern="^[a-z0-9-]+$" minLength={3} />
          </div>

          <div className="form-group">
            <label>LLM Provider</label>
            <select className="form-input" value={provider} onChange={e => handleProviderChange(e.target.value)}>
              {providers ? Object.entries(providers).map(([key, p]) => (
                <option key={key} value={key}>{p.name}</option>
              )) : <option>Loading...</option>}
            </select>
          </div>

          {currentProvider && (
            <div className="form-group">
              <label>Model</label>
              <select className="form-input" value={model} onChange={e => setModel(e.target.value)}>
                {currentProvider.models.map(m => (
                  <option key={m.id} value={m.id}>{m.name} ({m.id})</option>
                ))}
              </select>
            </div>
          )}

          {currentProvider?.required_keys?.length > 0 && provider !== 'openai-compatible' && (
            <div style={{background:'var(--bg-secondary)', padding:'12px', borderRadius:'8px', marginBottom:'12px'}}>
              <p style={{fontSize:'0.8125rem', color:'var(--text-secondary)', marginBottom:'8px'}}>
                🔑 API Keys required for {currentProvider.name}
              </p>
              {currentProvider.required_keys.filter(k => k !== 'CUSTOM_BASE_URL' && k !== 'CUSTOM_MODEL_ID').map(key => (
                <div className="form-group" key={key} style={{marginBottom:'8px'}}>
                  <label style={{fontSize:'0.75rem'}}>{key}</label>
                  <input className="form-input" type="password" placeholder={key}
                    value={apiKeys[key] || ''} onChange={e => setApiKeys({...apiKeys, [key]: e.target.value})} required />
                </div>
              ))}
              {currentProvider?.optional_keys?.length > 0 && (
                <>
                  <p style={{fontSize:'0.75rem', color:'var(--text-secondary)', marginTop:'8px', marginBottom:'4px'}}>
                    Optional settings:
                  </p>
                  {currentProvider.optional_keys.map(key => (
                    <div className="form-group" key={key} style={{marginBottom:'8px'}}>
                      <label style={{fontSize:'0.75rem'}}>{key} <span style={{color:'var(--text-secondary)'}}>(optional)</span></label>
                      <input className="form-input" placeholder={key === 'AWS_DEFAULT_REGION' ? 'us-west-2' : key}
                        value={apiKeys[key] || ''} onChange={e => setApiKeys({...apiKeys, [key]: e.target.value})} />
                    </div>
                  ))}
                </>
              )}
            </div>
          )}

          {provider === 'openai-compatible' && (
            <div style={{background:'var(--bg-secondary)', padding:'12px', borderRadius:'8px', marginBottom:'12px'}}>
              <p style={{fontSize:'0.8125rem', color:'var(--text-secondary)', marginBottom:'8px'}}>
                🌐 OpenAI-Compatible Endpoint
              </p>
              <div className="form-group" style={{marginBottom:'8px'}}>
                <label style={{fontSize:'0.75rem'}}>API Base URL</label>
                <input className="form-input" placeholder="https://api.example.com/v1"
                  value={apiKeys['CUSTOM_BASE_URL'] || ''} onChange={e => setApiKeys({...apiKeys, CUSTOM_BASE_URL: e.target.value})} required />
              </div>
              <div className="form-group" style={{marginBottom:'8px'}}>
                <label style={{fontSize:'0.75rem'}}>API Key</label>
                <input className="form-input" type="password" placeholder="sk-..."
                  value={apiKeys['CUSTOM_API_KEY'] || ''} onChange={e => setApiKeys({...apiKeys, CUSTOM_API_KEY: e.target.value})} required />
              </div>
              <div className="form-group" style={{marginBottom:'8px'}}>
                <label style={{fontSize:'0.75rem'}}>Model ID</label>
                <input className="form-input" placeholder="gpt-4o, deepseek-chat, qwen-plus..."
                  value={apiKeys['CUSTOM_MODEL_ID'] || ''} onChange={e => setApiKeys({...apiKeys, CUSTOM_MODEL_ID: e.target.value})} required />
              </div>
            </div>
          )}

          <div style={{background:'var(--bg-secondary)', padding:'12px', borderRadius:'8px', marginBottom:'12px', display:'flex', flexDirection:'column', gap:'10px'}}>
            <label style={{display:'flex', alignItems:'center', gap:'8px', cursor:'pointer', fontSize:'0.8125rem'}}>
              <input type="checkbox" checked={enableChromium} onChange={e => setEnableChromium(e.target.checked)} />
              <span>🌐 <strong>Enable Browser</strong> — adds Chromium sidecar for web automation (+500m CPU, +1Gi mem)</span>
            </label>
            <label style={{display:'flex', alignItems:'center', gap:'8px', cursor:'pointer', fontSize:'0.8125rem'}}>
              <input type="checkbox" checked={enableGateway} onChange={e => setEnableGateway(e.target.checked)} />
              <span>🔗 <strong>Expose Gateway</strong> — create an internet-facing ALB to access the agent's web UI</span>
            </label>
          </div>

          <div style={{marginBottom:'12px'}}>
            <button type="button" className="btn btn-sm" onClick={() => setShowAdvanced(!showAdvanced)}
              style={{fontSize:'0.75rem', color:'var(--text-secondary)'}}>
              {showAdvanced ? '▼' : '▶'} Advanced Options
            </button>
            {showAdvanced && (
              <div style={{background:'var(--bg-secondary)', padding:'12px', borderRadius:'8px', marginTop:'8px'}}>
                <p style={{fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:'8px'}}>
                  🐳 <strong>Custom Container Image</strong> — use a custom-built OpenClaw image with pre-installed tools.
                  Leave empty to use the platform default.
                </p>
                <div className="form-group" style={{marginBottom:'8px'}}>
                  <label style={{fontSize:'0.75rem'}}>Image Repository</label>
                  <input className="form-input" value={customImage} onChange={e => setCustomImage(e.target.value)}
                    placeholder="e.g. public.ecr.aws/xxx/openclaw-custom" />
                </div>
                <div className="form-group" style={{marginBottom:'0'}}>
                  <label style={{fontSize:'0.75rem'}}>Image Tag</label>
                  <input className="form-input" value={customImageTag} onChange={e => setCustomImageTag(e.target.value)}
                    placeholder="e.g. 2026.3.21 (default: latest)" />
                </div>

                <hr style={{border:'none', borderTop:'1px solid var(--border-color)', margin:'12px 0'}} />
                <p style={{fontSize:'0.75rem', color:'var(--text-secondary)', marginBottom:'8px'}}>
                  <strong>Scheduling</strong> — control pod placement on specific nodes.
                </p>
                <div className="form-group" style={{marginBottom:'8px'}}>
                  <label style={{fontSize:'0.75rem'}}>RuntimeClassName</label>
                  <input className="form-input" value={runtimeClassName} onChange={e => setRuntimeClassName(e.target.value)}
                    placeholder="e.g. kata, gvisor" />
                </div>
                <div className="form-group" style={{marginBottom:'8px'}}>
                  <label style={{fontSize:'0.75rem'}}>Node Selector <span style={{color:'var(--text-secondary)'}}>(comma-separated key=value)</span></label>
                  <input className="form-input" value={nodeSelector} onChange={e => setNodeSelector(e.target.value)}
                    placeholder="e.g. kubernetes.io/arch=arm64, node.kubernetes.io/instance-type=m7g.xlarge" />
                </div>
                <div className="form-group" style={{marginBottom:'0'}}>
                  <label style={{fontSize:'0.75rem'}}>Tolerations <span style={{color:'var(--text-secondary)'}}>(one per line: key=mykey,operator=Equal,value=myval,effect=NoSchedule)</span></label>
                  <textarea className="form-input" value={tolerations} onChange={e => setTolerations(e.target.value)}
                    placeholder={"key=dedicated,operator=Equal,value=agents,effect=NoSchedule"}
                    rows={3} style={{resize:'vertical', fontFamily:'monospace', fontSize:'0.75rem'}} />
                </div>
              </div>
            )}
          </div>

          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary" disabled={submitting}>
              {submitting ? 'Creating...' : 'Create Agent'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Channel Binding Modal ───
function ChannelModal({ tenantName, agentId, agentName, onClose }) {
  const [channelType, setChannelType] = useState('')
  const [availableChannels, setAvailableChannels] = useState([])
  const [creds, setCreds] = useState({})
  const [error, setError] = useState('')
  const [success, setSuccess] = useState('')

  const CHANNEL_LABELS = {
    telegram: 'Telegram',
    feishu: 'Feishu (飞书)',
    discord: 'Discord',
    whatsapp: 'WhatsApp',
  }

  const FIELDS = {
    telegram: [{ key: 'bot_token', label: 'Bot Token', placeholder: '123456:ABC-DEF...' }],
    feishu: [
      { key: 'app_id', label: 'App ID', placeholder: 'cli_xxx' },
      { key: 'app_secret', label: 'App Secret', placeholder: 'xxx' },
    ],
    discord: [{ key: 'bot_token', label: 'Bot Token', placeholder: 'MTk...' }],
    whatsapp: [
      { key: 'phone_number_id', label: 'Phone Number ID', placeholder: '123456789' },
      { key: 'access_token', label: 'Access Token', placeholder: 'EAA...' },
      { key: 'verify_token', label: 'Verify Token', placeholder: 'my-verify-token' },
    ],
  }

  useEffect(() => {
    api.getAvailableChannels().then(res => {
      const channels = res.channels || []
      setAvailableChannels(channels)
      if (channels.length > 0) setChannelType(channels[0])
    }).catch(() => {
      const fallback = ['telegram', 'feishu', 'discord', 'whatsapp']
      setAvailableChannels(fallback)
      setChannelType(fallback[0])
    })
  }, [])

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError(''); setSuccess('')
    try {
      const res = await api.bindChannel(tenantName, agentId, channelType, creds)
      setSuccess(res.message || 'Channel bound!')
      setTimeout(onClose, 1500)
    } catch (err) { setError(err.message) }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={e => e.stopPropagation()}>
        <h2>Bind Channel to {agentName}</h2>
        {error && <div className="alert alert-error">{error}</div>}
        {success && <div className="alert alert-success">{success}</div>}
        <form onSubmit={handleSubmit}>
          <div className="form-group">
            <label>Channel Type</label>
            <select className="form-input" value={channelType} onChange={e => { setChannelType(e.target.value); setCreds({}) }}>
              {availableChannels.map(ch => (
                <option key={ch} value={ch}>{CHANNEL_LABELS[ch] || ch}</option>
              ))}
            </select>
          </div>
          {FIELDS[channelType]?.map(f => (
            <div className="form-group" key={f.key}>
              <label>{f.label}</label>
              <input className="form-input" type="password" placeholder={f.placeholder}
                value={creds[f.key] || ''} onChange={e => setCreds({...creds, [f.key]: e.target.value})} required />
            </div>
          ))}
          <div className="modal-actions">
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="submit" className="btn btn-primary">Bind Channel</button>
          </div>
        </form>
      </div>
    </div>
  )
}

// ─── Logs Modal ───
function LogsModal({ tenantName, agentId, agentName, onClose }) {
  const [logs, setLogs] = useState('')
  const [container, setContainer] = useState('openclaw')
  const [containers, setContainers] = useState(['openclaw', 'metrics-exporter', 'gateway-proxy'])
  const [tail, setTail] = useState(200)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [autoRefresh, setAutoRefresh] = useState(false)

  const fetchLogs = async () => {
    setLoading(true)
    setError('')
    try {
      const res = await api.getAgentLogs(tenantName, agentId, container, tail)
      setLogs(res.logs || '(no logs)')
      if (res.available_containers) setContainers(res.available_containers)
      if (res.error) setError(res.error)
    } catch (e) { setError(e.message) }
    finally { setLoading(false) }
  }

  useEffect(() => { fetchLogs() }, [container, tail])

  useEffect(() => {
    if (!autoRefresh) return
    const interval = setInterval(fetchLogs, 5000)
    return () => clearInterval(interval)
  }, [autoRefresh, container, tail])

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal logs-modal" onClick={e => e.stopPropagation()}>
        <div className="logs-header">
          <h2>📋 Logs — {agentName}</h2>
          <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
            <select className="form-input" style={{ width: 'auto' }} value={container}
              onChange={e => setContainer(e.target.value)}>
              {containers.map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select className="form-input" style={{ width: 'auto' }} value={tail}
              onChange={e => setTail(+e.target.value)}>
              <option value={50}>50 lines</option>
              <option value={200}>200 lines</option>
              <option value={500}>500 lines</option>
              <option value={1000}>1000 lines</option>
            </select>
            <label style={{ fontSize: '0.75rem', display: 'flex', alignItems: 'center', gap: '4px', cursor: 'pointer' }}>
              <input type="checkbox" checked={autoRefresh} onChange={e => setAutoRefresh(e.target.checked)} />
              Auto
            </label>
            <button className="btn btn-sm" onClick={fetchLogs} disabled={loading}>
              {loading ? '⏳' : '🔄'}
            </button>
          </div>
        </div>
        {error && <div className="alert alert-error" style={{ margin: '8px 0' }}>{error}</div>}
        <pre className="logs-content">{logs}</pre>
        <div className="modal-actions">
          <button className="btn" onClick={onClose}>Close</button>
        </div>
      </div>
    </div>
  )
}

// ─── Billing Page ───
function BillingPage() {
  const { name: tenantName } = useParams()
  const [billing, setBilling] = useState(null)
  const [usage, setUsage] = useState(null)
  const [agentUsage, setAgentUsage] = useState({}) // { agentName: data }
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [period, setPeriod] = useState(30)
  const [expandedAgent, setExpandedAgent] = useState(null)

  useEffect(() => {
    setLoading(true)
    api.getBillingFull(tenantName, period).then(data => {
      setBilling(data.billing)
      setUsage(data.usage)
    }).catch(e => setError(e.message)).finally(() => setLoading(false))
  }, [tenantName, period])

  const fmtTokens = (n) => {
    if (!n) return '0'
    if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M'
    if (n >= 1_000) return (n / 1_000).toFixed(1) + 'K'
    return n.toString()
  }

  const fmtCost = (c) => c != null ? '$' + c.toFixed(4) : '$0.00'

  const quotaColor = (pct) => {
    if (pct >= 90) return 'var(--accent-red)'
    if (pct >= 70) return 'var(--accent-orange)'
    return 'var(--accent-green)'
  }

  if (loading) return <div className="container"><p>Loading billing data...</p></div>

  return (
    <div className="container">
      <div className="page-header">
        <h1>📊 Billing — {tenantName}</h1>
        <p><Link to={`/tenants/${tenantName}`}>← Back to Tenant</Link></p>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {/* Quota Overview Card */}
      {billing && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Plan & Quota</span>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              <span className={`badge ${billing.current_plan === 'free' ? 'badge-orange' : 'badge-green'}`}>
                {billing.current_plan.toUpperCase()}
              </span>
              <Link to="/plans" style={{ fontSize: '0.75rem' }}>Compare Plans →</Link>
            </div>
          </div>
          <div className="billing-stats">
            <div className="stat-box">
              <div className="stat-label">Tokens Used</div>
              <div className="stat-value">{fmtTokens(billing.current_month_usage?.tokens_used)}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Token Limit</div>
              <div className="stat-value">{billing.current_month_usage?.tokens_limit ? fmtTokens(billing.current_month_usage.tokens_limit) : '∞'}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Days Until Reset</div>
              <div className="stat-value">{billing.current_month_usage?.days_until_reset ?? '—'}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Monthly Price</div>
              <div className="stat-value">${billing.limits?.price_monthly ?? 0}</div>
            </div>
          </div>

          {/* Quota Bar */}
          <div className="quota-bar-container">
            <div className="quota-bar-labels">
              <span>Usage: {(billing.current_month_usage?.percentage_used ?? 0).toFixed(1)}%</span>
              {billing.current_month_usage?.is_warning && <span className="badge badge-red">⚠ Warning</span>}
              {billing.current_month_usage?.is_over_quota && <span className="badge badge-red">🚫 Over Quota</span>}
            </div>
            <div className="quota-bar">
              <div className="quota-bar-fill"
                style={{
                  width: Math.min(billing.current_month_usage?.percentage_used ?? 0, 100) + '%',
                  background: quotaColor(billing.current_month_usage?.percentage_used ?? 0)
                }}
              />
            </div>
          </div>

          {/* Plan Limits */}
          <div className="plan-limits">
            <span>Max Agents: <strong>{billing.limits?.max_agents ?? '—'}</strong></span>
            <span>Memory/Agent: <strong>{billing.limits?.max_memory_per_agent ?? '—'}</strong></span>
            <span>CPU/Agent: <strong>{billing.limits?.max_cpu_per_agent ?? '—'}</strong></span>
          </div>
        </div>
      )}

      {/* Usage Breakdown */}
      {usage && (
        <div className="card">
          <div className="card-header">
            <span className="card-title">Token Usage Breakdown</span>
            <select className="form-input" style={{ width: 'auto' }} value={period} onChange={e => setPeriod(+e.target.value)}>
              <option value={7}>Last 7 days</option>
              <option value={30}>Last 30 days</option>
              <option value={90}>Last 90 days</option>
            </select>
          </div>

          {/* Summary */}
          <div className="billing-stats">
            <div className="stat-box">
              <div className="stat-label">Total Tokens</div>
              <div className="stat-value">{fmtTokens(usage.summary?.total_tokens)}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Input</div>
              <div className="stat-value">{fmtTokens(usage.summary?.input_tokens)}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Output</div>
              <div className="stat-value">{fmtTokens(usage.summary?.output_tokens)}</div>
            </div>
            <div className="stat-box">
              <div className="stat-label">Est. Cost</div>
              <div className="stat-value">{fmtCost(usage.summary?.estimated_cost)}</div>
            </div>
          </div>

          {/* By Agent */}
          {usage.by_agent?.length > 0 && (
            <>
              <h3 className="section-title">By Agent</h3>
              <div className="usage-table">
                <div className="usage-row usage-header">
                  <span>Agent</span><span>Tokens</span><span>Calls</span><span>Cost</span><span></span>
                </div>
                {usage.by_agent.map(a => (
                  <div key={a.agent_name}>
                    <div className="usage-row" onClick={() => setExpandedAgent(expandedAgent === a.agent_name ? null : a.agent_name)} style={{ cursor: 'pointer' }}>
                      <span className="agent-name-link">{a.agent_name}</span>
                      <span>{fmtTokens(a.total_tokens)}</span>
                      <span>{a.call_count}</span>
                      <span>{fmtCost(a.estimated_cost)}</span>
                      <span style={{ color: 'var(--text-secondary)' }}>{expandedAgent === a.agent_name ? '▼' : '▶'}</span>
                    </div>
                    {expandedAgent === a.agent_name && (
                      <div className="agent-detail" style={{ padding: '10px 16px', background: 'var(--bg-secondary)', borderRadius: '0 0 8px 8px', marginBottom: '4px' }}>
                        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '10px', fontSize: '0.8125rem' }}>
                          <div><span style={{ color: 'var(--text-secondary)' }}>Input: </span><strong>{fmtTokens(a.input_tokens)}</strong></div>
                          <div><span style={{ color: 'var(--text-secondary)' }}>Output: </span><strong>{fmtTokens(a.output_tokens)}</strong></div>
                          <div><span style={{ color: 'var(--text-secondary)' }}>Total: </span><strong>{fmtTokens(a.total_tokens)}</strong></div>
                          {(a.cache_read > 0 || a.cache_write > 0) && (
                            <>
                              <div><span style={{ color: 'var(--text-secondary)' }}>Cache Read: </span><strong>{fmtTokens(a.cache_read)}</strong></div>
                              <div><span style={{ color: 'var(--text-secondary)' }}>Cache Write: </span><strong>{fmtTokens(a.cache_write)}</strong></div>
                              <div></div>
                            </>
                          )}
                          <div><span style={{ color: 'var(--text-secondary)' }}>API Calls: </span><strong>{a.call_count}</strong></div>
                          <div><span style={{ color: 'var(--text-secondary)' }}>Est. Cost: </span><strong>{fmtCost(a.estimated_cost)}</strong></div>
                          {a.call_count > 0 && (
                            <div><span style={{ color: 'var(--text-secondary)' }}>Avg Tokens/Call: </span><strong>{Math.round(a.total_tokens / a.call_count)}</strong></div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}

          {/* By Model */}
          {usage.by_model?.length > 0 && (
            <>
              <h3 className="section-title">By Model</h3>
              <div className="usage-table">
                <div className="usage-row usage-header">
                  <span>Model</span><span>Tokens</span><span>Calls</span><span>Cost</span><span></span>
                </div>
                {usage.by_model.map(m => (
                  <div key={m.model} className="usage-row">
                    <span style={{ fontSize: '0.75rem' }}>{m.model}</span>
                    <span>{fmtTokens(m.total_tokens)}</span>
                    <span>{m.call_count}</span>
                    <span>{fmtCost(m.estimated_cost)}</span>
                    <span></span>
                  </div>
                ))}
              </div>
            </>
          )}

          {/* Daily Trend */}
          {usage.daily?.length > 0 && (
            <>
              <h3 className="section-title">Daily Trend</h3>
              <div className="daily-chart">
                {usage.daily.map((d, i) => {
                  const maxTokens = Math.max(...usage.daily.map(x => x.total_tokens || 1))
                  return (
                    <div key={i} className="daily-bar-wrapper" title={`${d.date}: ${fmtTokens(d.total_tokens)} tokens, ${fmtCost(d.estimated_cost)}`}>
                      <div className="daily-bar"
                        style={{ height: Math.max(2, (d.total_tokens / maxTokens) * 80) + 'px' }}
                      />
                      <span className="daily-label">{d.date?.slice(5, 10)}</span>
                    </div>
                  )
                })}
              </div>
            </>
          )}

          {/* Empty state */}
          {(!usage.by_agent?.length && !usage.by_model?.length && !usage.daily?.length) && (
            <div className="empty">
              <div className="empty-icon">📈</div>
              <p>No usage data yet. Start chatting with your agents to generate usage.</p>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ─── Plans Page ───
function PlansPage() {
  const navigate = useNavigate()

  const plans = [
    {
      name: 'Free',
      key: 'free',
      price: '$0',
      priceSub: 'forever',
      description: 'Perfect for trying out OpenClaw with a single agent.',
      color: 'var(--text-secondary)',
      badge: null,
      limits: {
        agents: '1',
        tokensPerMonth: '100K',
        tokenEnforcement: 'Count only (no hard limit)',
        cpuPerAgent: '2 vCPU',
        memoryPerAgent: '4 Gi',
        totalCpu: '4 vCPU',
        totalMemory: '8 Gi',
        storage: '50 Gi per agent',
        channels: 'All (Telegram, Discord, Feishu, WhatsApp)',
        support: 'Community',
      },
    },
    {
      name: 'Pro',
      key: 'pro',
      price: '$99',
      priceSub: '/month',
      description: 'For teams running multiple agents with higher capacity.',
      color: 'var(--accent-blue)',
      badge: 'Most Popular',
      limits: {
        agents: '10',
        tokensPerMonth: '10M',
        tokenEnforcement: 'Count only (no hard limit)',
        cpuPerAgent: '2 vCPU',
        memoryPerAgent: '4 Gi',
        totalCpu: '24 vCPU',
        totalMemory: '48 Gi',
        storage: '50 Gi per agent',
        channels: 'All (Telegram, Discord, Feishu, WhatsApp)',
        support: 'Email',
      },
    },
    {
      name: 'Enterprise',
      key: 'enterprise',
      price: 'Custom',
      priceSub: 'contact us',
      description: 'Dedicated resources and priority support for large deployments.',
      color: 'var(--accent-purple)',
      badge: null,
      limits: {
        agents: 'Unlimited',
        tokensPerMonth: 'Unlimited',
        tokenEnforcement: 'Count only (no hard limit)',
        cpuPerAgent: '2 vCPU',
        memoryPerAgent: '4 Gi',
        totalCpu: '120 vCPU',
        totalMemory: '240 Gi',
        storage: '50 Gi per agent',
        channels: 'All + Custom integrations',
        support: 'Priority + Slack',
      },
    },
  ]

  const limitLabels = {
    agents: '🤖 Max Agents',
    tokensPerMonth: '🔤 Tokens / Month',
    tokenEnforcement: '📊 Token Policy',
    cpuPerAgent: '⚡ CPU / Agent',
    memoryPerAgent: '💾 Memory / Agent',
    totalCpu: '🖥️ Total CPU Quota',
    totalMemory: '🗄️ Total Memory Quota',
    storage: '💿 Storage',
    channels: '📡 Channels',
    support: '🛟 Support',
  }

  return (
    <div className="container" style={{ maxWidth: 960 }}>
      <div className="page-header" style={{ textAlign: 'center' }}>
        <h1>Choose Your Plan</h1>
        <p>All plans include full access to OpenClaw features. Scale up as you grow.</p>
      </div>

      {/* Plan Cards Grid */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(3, 1fr)',
        gap: '16px',
        marginBottom: '32px',
      }}>
        {plans.map(plan => (
          <div key={plan.key} className="card" style={{
            borderColor: plan.badge ? plan.color : 'var(--border)',
            position: 'relative',
            display: 'flex',
            flexDirection: 'column',
          }}>
            {plan.badge && (
              <div style={{
                position: 'absolute', top: '-12px', left: '50%', transform: 'translateX(-50%)',
                background: plan.color, color: '#fff', padding: '2px 12px',
                borderRadius: '10px', fontSize: '0.6875rem', fontWeight: 600, whiteSpace: 'nowrap',
              }}>{plan.badge}</div>
            )}
            <div style={{ textAlign: 'center', paddingTop: plan.badge ? 8 : 0 }}>
              <div style={{ fontSize: '0.875rem', fontWeight: 600, color: plan.color, textTransform: 'uppercase', letterSpacing: 1 }}>
                {plan.name}
              </div>
              <div style={{ fontSize: '2.25rem', fontWeight: 700, margin: '8px 0 0' }}>
                {plan.price}
              </div>
              <div style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', marginBottom: 12 }}>
                {plan.priceSub}
              </div>
              <p style={{ fontSize: '0.8125rem', color: 'var(--text-secondary)', minHeight: 40 }}>
                {plan.description}
              </p>
            </div>
            <div style={{ borderTop: '1px solid var(--border)', paddingTop: 12, marginTop: 'auto' }}>
              <div style={{ fontSize: '0.8125rem' }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Agents</span>
                  <strong>{plan.limits.agents}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Tokens/mo</span>
                  <strong>{plan.limits.tokensPerMonth}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>CPU/Agent</span>
                  <strong>{plan.limits.cpuPerAgent}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Mem/Agent</span>
                  <strong>{plan.limits.memoryPerAgent}</strong>
                </div>
                <div style={{ display: 'flex', justifyContent: 'space-between', padding: '4px 0' }}>
                  <span style={{ color: 'var(--text-secondary)' }}>Support</span>
                  <strong>{plan.limits.support}</strong>
                </div>
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Detailed Comparison Table */}
      <div className="card">
        <div className="card-header">
          <span className="card-title">📋 Detailed Plan Comparison</span>
        </div>
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: '0.8125rem' }}>
            <thead>
              <tr style={{ borderBottom: '2px solid var(--border)' }}>
                <th style={{ textAlign: 'left', padding: '10px 12px', color: 'var(--text-secondary)', fontWeight: 600 }}>Feature</th>
                {plans.map(p => (
                  <th key={p.key} style={{ textAlign: 'center', padding: '10px 12px', color: p.color, fontWeight: 600 }}>
                    {p.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {Object.entries(limitLabels).map(([key, label]) => (
                <tr key={key} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 12px', color: 'var(--text-secondary)' }}>{label}</td>
                  {plans.map(p => (
                    <td key={p.key} style={{ textAlign: 'center', padding: '10px 12px', fontWeight: 500 }}>
                      {p.limits[key]}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>

      <div style={{ textAlign: 'center', marginTop: 16, color: 'var(--text-secondary)', fontSize: '0.8125rem' }}>
        <p>💡 Token counting is enabled on all plans but currently <strong>not enforced</strong> — you won't be cut off if you exceed the limit.</p>
        <p style={{ marginTop: 4, fontSize: '0.75rem' }}>
          Each agent runs as a dedicated pod with its own CPU, memory, and 50Gi persistent storage.
          Total CPU/Memory quota is the cluster-wide limit for all your agents combined.
        </p>
        <p style={{ marginTop: 8 }}>
          Need a custom plan? <a href="mailto:support@openclaw-saas.com">Contact us</a>
        </p>
      </div>

      <div style={{ textAlign: 'center', marginTop: 20, marginBottom: 40 }}>
        <button className="btn" onClick={() => navigate(-1)}>← Back</button>
      </div>
    </div>
  )
}

// ─── App Router ───
export default function App() {
  const auth = useAuth()

  return (
    <div className="app">
      <Navbar auth={auth} />
      <Routes>
        <Route path="/login" element={auth.isLoggedIn ? <Navigate to="/" /> : <LoginPage onLogin={auth.refresh} />} />
        <Route path="/signup" element={auth.isLoggedIn ? <Navigate to="/" /> : <SignupPage onLogin={auth.refresh} />} />
        <Route path="/" element={auth.isLoggedIn ? <DashboardPage /> : <Navigate to="/login" />} />
        <Route path="/tenants/:name" element={auth.isLoggedIn ? <TenantPage /> : <Navigate to="/login" />} />
        <Route path="/tenants/:name/billing" element={auth.isLoggedIn ? <BillingPage /> : <Navigate to="/login" />} />
        <Route path="/plans" element={<PlansPage />} />
      </Routes>
    </div>
  )
}
