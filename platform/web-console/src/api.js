const API_BASE = window.location.origin

// Simple TTL cache for rarely-changing data (channels, providers, plans)
const _cache = new Map()
function cachedRequest(key, fetcher, ttlMs = 5 * 60 * 1000) {
  const cached = _cache.get(key)
  if (cached && Date.now() - cached.ts < ttlMs) return Promise.resolve(cached.data)
  return fetcher().then(data => { _cache.set(key, { data, ts: Date.now() }); return data })
}

class ApiClient {
  constructor() {
    this.token = localStorage.getItem('token')
  }

  setToken(token) {
    this.token = token
    if (token) localStorage.setItem('token', token)
    else localStorage.removeItem('token')
  }

  setUser(user) {
    this._user = user
    if (user) localStorage.setItem('user', JSON.stringify(user))
    else localStorage.removeItem('user')
  }

  getUser() {
    if (this._user) return this._user
    const stored = localStorage.getItem('user')
    if (stored) { this._user = JSON.parse(stored); return this._user }
    return null
  }

  isPlatformAdmin() { return this.getUser()?.is_platform_admin === true }

  getToken() { return this.token }
  isLoggedIn() { return !!this.token }

  async request(path, options = {}) {
    const headers = { 'Content-Type': 'application/json', ...options.headers }
    if (this.token) headers['Authorization'] = `Bearer ${this.token}`

    const res = await fetch(`${API_BASE}${path}`, { ...options, headers })
    if (res.status === 401) {
      this.setToken(null)
      window.location.href = '/console/login'
      throw new Error('Unauthorized')
    }
    if (res.status === 204) return null
    let data
    try {
      data = await res.json()
    } catch {
      if (!res.ok) throw new Error(`Request failed (${res.status})`)
      throw new Error('Invalid response from server')
    }
    if (!res.ok) {
      let msg = 'Request failed'
      if (typeof data.detail === 'string') {
        msg = data.detail
      } else if (Array.isArray(data.detail)) {
        msg = data.detail.map(e => {
          const field = (e.loc || []).filter(l => l !== 'body').join('.')
          return field ? `${field}: ${e.msg}` : e.msg
        }).join('; ')
      }
      throw new Error(msg)
    }
    return data
  }

  // Dashboard (aggregated)
  getDashboard() { return this.request('/api/v1/dashboard') }
  getTenantDashboard(tenant) { return this.request(`/api/v1/tenants/${tenant}/dashboard`) }

  // Auth
  signup(email, password, displayName) {
    return this.request('/api/v1/auth/signup', {
      method: 'POST', body: JSON.stringify({ email, password, display_name: displayName })
    })
  }
  login(email, password) {
    return this.request('/api/v1/auth/login', {
      method: 'POST', body: JSON.stringify({ email, password })
    })
  }

  // Tenants
  listTenants() { return this.request('/api/v1/tenants') }
  getTenant(tenant) { return this.request(`/api/v1/tenants/${tenant}`) }
  createTenant(name, plan = 'free') {
    return this.request('/api/v1/tenants', { method: 'POST', body: JSON.stringify({ name, plan }) })
  }
  deleteTenant(name) { return this.request(`/api/v1/tenants/${name}`, { method: 'DELETE' }) }

  // Agents
  listAgents(tenant) { return this.request(`/api/v1/tenants/${tenant}/agents`) }
  createAgent(tenant, name, { llmProvider = 'openai-compatible', llmModel = null, llmApiKeys = null, enableChromium = false, customImage = null, customImageTag = null } = {}) {
    const body = { name, llm_provider: llmProvider, enable_chromium: enableChromium }
    if (llmModel) body.llm_model = llmModel
    if (llmApiKeys && Object.keys(llmApiKeys).length) body.llm_api_keys = llmApiKeys
    if (customImage) body.custom_image = customImage
    if (customImageTag) body.custom_image_tag = customImageTag
    return this.request(`/api/v1/tenants/${tenant}/agents`, {
      method: 'POST', body: JSON.stringify(body)
    })
  }
  deleteAgent(tenant, agentId) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}`, { method: 'DELETE' })
  }
  getAgentStatus(tenant, agentId) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/status`)
  }
  getAgentLogs(tenant, agentId, container = 'openclaw', tail = 200) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/logs?container=${container}&tail=${tail}`)
  }
  updateAgentConfig(tenant, agentId, config) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/config`, {
      method: 'PUT', body: JSON.stringify({ config })
    })
  }

  // Model pool (uncached — mutations must see fresh state)
  getAgentModels(tenant, agentId) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/models`)
  }
  addAgentModel(tenant, agentId, modelId, setDefault = false) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/models`, {
      method: 'POST',
      body: JSON.stringify({ model_id: modelId, set_default: setDefault }),
    })
  }
  setAgentModelDefault(tenant, agentId, modelId) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/models/default`, {
      method: 'PUT',
      body: JSON.stringify({ model_id: modelId }),
    })
  }
  removeAgentModel(tenant, agentId, modelId) {
    return this.request(
      `/api/v1/tenants/${tenant}/agents/${agentId}/models/${encodeURIComponent(modelId)}`,
      { method: 'DELETE' }
    )
  }

  // Channels (cached — rarely changes)
  getAvailableChannels() { return cachedRequest('channels', () => this.request('/api/v1/channels')) }
  bindChannel(tenant, agentId, channelType, credentials) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/channels`, {
      method: 'POST', body: JSON.stringify({ channel_type: channelType, credentials })
    })
  }
  unbindChannel(tenant, agentId, channelType) {
    return this.request(`/api/v1/tenants/${tenant}/agents/${agentId}/channels/${channelType}`, {
      method: 'DELETE'
    })
  }

  // Usage & Billing
  getUsage(tenant) { return this.request(`/api/v1/tenants/${tenant}/usage`) }
  getUsageTokens(tenant, days = 30) { return this.request(`/api/v1/tenants/${tenant}/usage/tokens?period_days=${days}`) }
  getAgentUsage(tenant, agent, hours = 72) { return this.request(`/api/v1/tenants/${tenant}/agents/${agent}/usage?hours=${hours}`) }
  getBilling(tenant) { return this.request(`/api/v1/tenants/${tenant}/billing`) }
  getQuota(tenant) { return this.request(`/api/v1/tenants/${tenant}/billing/quota`) }
  upgradePlan(tenant, plan) {
    return this.request(`/api/v1/tenants/${tenant}/billing/upgrade`, {
      method: 'POST', body: JSON.stringify({ plan })
    })
  }
  getPlans() { return cachedRequest('plans', () => this.request('/api/v1/plans')) }

  // LLM Providers (cached — rarely changes)
  getLlmProviders() { return cachedRequest('llm-providers', () => this.request('/api/v1/llm-providers')) }

  // Aggregated billing (replaces getBilling + getUsageTokens in one call)
  getBillingFull(tenant, periodDays = 30) {
    return this.request(`/api/v1/tenants/${tenant}/billing/full?period_days=${periodDays}`)
  }

  // Platform Admin
  getAdminOverview() { return this.request('/api/v1/tenants/admin/overview') }

  // Allowed Emails
  getAllowedEmails(tenant) { return this.request(`/api/v1/tenants/${tenant}/allowed-emails`) }
  addAllowedEmail(tenant, email, role = 'member') {
    return this.request(`/api/v1/tenants/${tenant}/allowed-emails`, {
      method: 'POST',
      body: JSON.stringify({ email, role }),
    })
  }
  removeAllowedEmail(tenant, emailId) {
    return this.request(`/api/v1/tenants/${tenant}/allowed-emails/${emailId}`, { method: 'DELETE' })
  }

  // Members
  getMembers(tenant) { return this.request(`/api/v1/tenants/${tenant}/members`) }
  removeMember(tenant, userId) {
    return this.request(`/api/v1/tenants/${tenant}/members/${userId}`, { method: 'DELETE' })
  }

  // Tenant management
  deleteTenant(tenant) {
    return this.request(`/api/v1/tenants/${tenant}`, { method: 'DELETE' })
  }
}

export const api = new ApiClient()
