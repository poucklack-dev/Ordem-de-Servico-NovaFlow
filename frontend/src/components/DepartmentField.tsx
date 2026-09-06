import {useEffect, useState} from 'react';
import {api} from '../api';
import {useModules} from '../modules';

export default function DepartmentField({initial, shared = false}: {initial?: number | null; shared?: boolean}) {
  const [departments, setDepartments] = useState<any[]>([]);
  const {context} = useModules();
  useEffect(() => {api<any>('/catalog').then(data => setDepartments(data.departments || [])).catch(() => setDepartments([]));}, []);
  const defaultValue = initial ?? (context?.scope === 'ALL' ? '' : context?.department_ids[0] ?? '');
  return <label>Setor<select name="department_id" defaultValue={defaultValue}><option value="">{shared ? 'Compartilhado entre setores' : 'Sem setor'}</option>{departments.map(department => <option key={department.id} value={department.id}>{department.name}</option>)}</select><small>{shared ? 'Sem setor, este item funciona como catálogo compartilhado.' : 'O setor determina quem pode acessar este registro.'}</small></label>;
}
