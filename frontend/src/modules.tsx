import {createContext, useCallback, useContext, useEffect, useMemo, useRef, useState} from 'react';
import type {ReactNode} from 'react';
import {api} from './api';

export type ModuleKey = 'financial' | 'agenda' | 'plans' | 'academy' | 'school';
export type AccessScope = 'OWN' | 'DEPARTMENT' | 'MANAGED_DEPARTMENTS' | 'ALL';
type ModuleMap = Record<ModuleKey, boolean>;
export type AccessContext = {
  profile: {id: number; name: string; slug: string}; profile_id: number; profile_name: string;
  is_admin: boolean; permissions: string[]; scope: AccessScope; department_ids: number[];
  employee_id: number | null; job_position_id: number | null; job_position_name?: string;
  user?: {id: number; name: string; email: string}; modules: ModuleMap;
};
type State = {modules: ModuleMap; permissions: string[]; ready: boolean; context: AccessContext | null; revision: string;
  reload: () => Promise<void>; enabled: (key: ModuleKey) => boolean; allowed: (permission: string) => boolean};
const empty: ModuleMap = {financial: false, agenda: false, plans: false, academy: false, school: false};
const Context = createContext<State>({modules: empty, permissions: [], ready: false, context: null, revision: '', reload: async () => {}, enabled: () => false, allowed: () => false});
export const scopeLabels: Record<AccessScope, string> = {OWN: 'Somente meus registros', DEPARTMENT: 'Próprio setor', MANAGED_DEPARTMENTS: 'Setores gerenciados', ALL: 'Todos os setores'};
export function authorizationChanged() {
  window.dispatchEvent(new Event('authorization-changed'));
  localStorage.setItem('authorization-updated', String(Date.now()));
}

export function ModuleProvider({children}: {children: ReactNode}) {
  const [context, setContext] = useState<AccessContext | null>(null);
  const [ready, setReady] = useState(false), [error, setError] = useState('');
  const busy = useRef<Promise<void> | null>(null);
  const reload = useCallback((): Promise<void> => {
    if (busy.current) return busy.current;
    busy.current = api<AccessContext>('/auth/context').then(data => {
      setContext(current => JSON.stringify(current) === JSON.stringify(data) ? current : data);
      setReady(true); setError('');
    }).catch(err => {setContext(null); setReady(false); setError(err.message || 'Não foi possível atualizar seu acesso.');})
      .finally(() => {busy.current = null;});
    return busy.current;
  }, []);
  useEffect(() => {
    void reload();
    const refresh = () => {void reload();};
    const visibility = () => {if (document.visibilityState === 'visible') refresh();};
    const storage = (event: StorageEvent) => {if (event.key === 'authorization-updated') refresh();};
    const timer = window.setInterval(() => {if (document.visibilityState === 'visible') refresh();}, 15000);
    ['modules-changed', 'authorization-changed', 'authorization-stale', 'focus'].forEach(name => window.addEventListener(name, refresh));
    document.addEventListener('visibilitychange', visibility); window.addEventListener('storage', storage);
    return () => {clearInterval(timer); ['modules-changed', 'authorization-changed', 'authorization-stale', 'focus'].forEach(name => window.removeEventListener(name, refresh)); document.removeEventListener('visibilitychange', visibility); window.removeEventListener('storage', storage);};
  }, [reload]);
  const modules = useMemo(() => ({...empty, ...context?.modules}), [context]);
  const permissions = context?.permissions || [];
  const allowed = useCallback((permission: string) => !!context?.permissions.includes(permission), [context]);
  const enabled = useCallback((key: ModuleKey) => !!modules[key], [modules]);
  const revision = JSON.stringify(context);
  useEffect(() => {document.body.dataset.financial = modules.financial && allowed('financial.view') ? 'on' : 'off';}, [modules, allowed]);
  if (error) return <main className="access-retry"><h2>Não foi possível verificar seu acesso</h2><p>{error}</p><button className="primary" onClick={() => void reload()}>Tentar novamente</button></main>;
  return <Context.Provider value={{modules, permissions, ready, context, revision, reload, enabled, allowed}}>{children}</Context.Provider>;
}
export const useModules = () => useContext(Context);
export function ModuleGuard({module, permission, children, fallback = null}: {module: ModuleKey; permission?: string; children: ReactNode; fallback?: ReactNode}) {
  const {enabled, allowed} = useModules(); return enabled(module) && (!permission || allowed(permission)) ? <>{children}</> : <>{fallback}</>;
}
export function PermissionGuard({permission, children, fallback = null}: {permission: string; children: ReactNode; fallback?: ReactNode}) {
  return useModules().allowed(permission) ? <>{children}</> : <>{fallback}</>;
}
