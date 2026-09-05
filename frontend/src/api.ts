const API = import.meta.env.VITE_API_URL || 'http://localhost:8000/api'
export const token = () => localStorage.getItem('token')
export async function api<T>(path:string, options:RequestInit={}):Promise<T>{
  const response=await fetch(`${API}${path}`,{...options,headers:{...(options.body instanceof FormData?{}:{'Content-Type':'application/json'}),...(token()?{Authorization:`Bearer ${token()}`}:{}) ,...options.headers}})
  if(response.status===401){localStorage.removeItem('token'); if(path!='/auth/login') location.reload()}
  if(!response.ok){const body=await response.json().catch(()=>({})); throw new Error(body.detail||'Não foi possível concluir a operação')}
  return response.json()
}
