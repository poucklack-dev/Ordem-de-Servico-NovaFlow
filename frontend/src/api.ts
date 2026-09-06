const API = import.meta.env.VITE_API_URL || `${location.protocol}//${location.hostname}:8000/api`;
export const token = () => localStorage.getItem('token');
export class APIError extends Error {constructor(public status: number, public detail: any) {super(typeof detail === 'string' ? detail : detail?.message || 'Não foi possível concluir a operação.');}}
export async function api<T>(path: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API}${path}`, {...options, headers: {...(options.body instanceof FormData ? {} : {'Content-Type': 'application/json'}), ...(token() ? {Authorization: `Bearer ${token()}`} : {}), ...options.headers}});
  if (response.status === 401) {localStorage.removeItem('token'); if (path !== '/auth/login') location.reload();}
  if (response.status === 403 && path !== '/auth/context') window.dispatchEvent(new Event('authorization-stale'));
  if (!response.ok) {const body = await response.json().catch(() => ({})); throw new APIError(response.status, body.detail || 'Não foi possível concluir a operação.');}
  if (response.status === 204) return undefined as T;
  return response.json();
}
export async function download(path: string, filename: string) {
  const response = await fetch(`${API}${path}`, {headers: {...(token() ? {Authorization: `Bearer ${token()}`} : {})}});
  if (response.status === 403) window.dispatchEvent(new Event('authorization-stale'));
  if (!response.ok) throw new Error('Não foi possível baixar o arquivo.');
  const blob = await response.blob(), url = URL.createObjectURL(blob), anchor = document.createElement('a'); anchor.href = url; anchor.download = filename; anchor.click(); URL.revokeObjectURL(url);
}
