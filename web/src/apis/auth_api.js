import { apiPost } from './base'

export const authApi = {
  register: (payload) => apiPost('/api/auth/register', payload, {}, false)
}

