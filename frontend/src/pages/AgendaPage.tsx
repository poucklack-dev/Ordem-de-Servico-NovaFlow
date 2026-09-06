import {useEffect, useState} from 'react';
import {api} from '../api';
import {Clock, Edit3, Plus, XCircle} from 'lucide-react';
import {Empty, Modal, Notice, Title} from '../components/UI';
import {useModules} from '../modules';
import DepartmentField from '../components/DepartmentField';

const statuses = ['Agendado', 'Confirmado', 'Em atendimento', 'Concluído', 'Cancelado', 'Não compareceu'];

export default function AgendaPage() {
  const [rows, setRows] = useState<any[]>([]);
  const [catalog, setCatalog] = useState<any>({services: [], employees: []});
  const [customers, setCustomers] = useState<any[]>([]);
  const [editing, setEditing] = useState<any>();
  const [message, setMessage] = useState('');
  const {allowed} = useModules();
  const canCreate = allowed('appointments.create');
  const canUpdate = allowed('appointments.update');
  const canCancel = allowed('appointments.cancel');
  const load = () => api<any[]>('/appointments').then(setRows);

  useEffect(() => {
    void load();
    api('/catalog').then(setCatalog);
    if (allowed('customers.view')) api<any[]>('/customers').then(setCustomers);
  }, []);

  async function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const values: any = Object.fromEntries(new FormData(e.currentTarget));
    const body = {
      ...values,
      customer_id: values.customer_id ? Number(values.customer_id) : null,
      employee_id: values.employee_id ? Number(values.employee_id) : null,
      service_id: values.service_id ? Number(values.service_id) : null,
      order_id: values.order_id ? Number(values.order_id) : null,
      department_id: values.department_id ? Number(values.department_id) : null,
    };
    await api(`/appointments${editing?.id ? `/${editing.id}` : ''}`, {method: editing?.id ? 'PUT' : 'POST', body: JSON.stringify(body)});
    setEditing(undefined);
    setMessage('Agendamento salvo com sucesso.');
    await load();
  }

  async function cancel(id: number) {
    await api(`/appointments/${id}/cancel`, {method: 'PATCH'});
    setMessage('Agendamento cancelado.');
    await load();
  }

  return <>
    <Title title="Agenda" sub="Calendário, compromissos e atendimentos" action={canCreate ? <button className="primary" onClick={() => setEditing({})}><Plus size={17}/>Novo agendamento</button> : undefined}/>
    <Notice message={message}/>
    <div className="agenda-grid">
      {rows.map(x => <article className="panel event" key={x.id}><Clock/><div><b>{x.title}</b><p>{new Date(x.starts_at).toLocaleString('pt-BR')} · {x.kind}</p><span className="status">{x.status}</span></div><div className="event-actions">
        {canUpdate && <button className="table-action" onClick={() => setEditing(x)}><Edit3 size={15}/></button>}
        {canCancel && x.status !== 'Cancelado' && <button className="table-action" onClick={() => cancel(x.id)}><XCircle size={15}/></button>}
      </div></article>)}
      {!rows.length && <Empty text="Nenhum compromisso agendado"/>}
    </div>
    {editing && ((editing.id && canUpdate) || (!editing.id && canCreate)) && <Modal title={editing.id ? 'Editar agendamento' : 'Novo agendamento'} onClose={() => setEditing(undefined)}><form className="form" onSubmit={save}>
      <label>Título<input name="title" defaultValue={editing.title} required/></label>
      <div className="form-row"><label>Tipo<select name="kind" defaultValue={editing.kind || 'Atendimento'}>{['Compromisso', 'Atendimento', 'Avaliação física', 'Reunião com responsável', 'Atendimento pedagógico', 'Atendimento administrativo', 'Evento escolar', 'Reunião de coordenação'].map(x => <option key={x}>{x}</option>)}</select></label><label>Status<select name="status" defaultValue={editing.status || 'Agendado'}>{statuses.map(x => <option key={x}>{x}</option>)}</select></label></div>
      <label>Cliente/Aluno<select name="customer_id" defaultValue={editing.customer_id || ''}><option value="">Não vinculado</option>{customers.map(x => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
      <div className="form-row"><label>Funcionário<select name="employee_id" defaultValue={editing.employee_id || ''}><option value="">Não vinculado</option>{catalog.employees.map((x: any) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label><label>Serviço<select name="service_id" defaultValue={editing.service_id || ''}><option value="">Não vinculado</option>{catalog.services.map((x: any) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label></div>
      <DepartmentField initial={editing.department_id}/>
      <label>Ordem vinculada<input name="order_id" type="number" defaultValue={editing.order_id || ''}/></label>
      <div className="form-row"><label>Início<input type="datetime-local" name="starts_at" defaultValue={editing.starts_at?.slice(0, 16)} required/></label><label>Término<input type="datetime-local" name="ends_at" defaultValue={editing.ends_at?.slice(0, 16)} required/></label></div>
      <label>Observações<textarea name="description" defaultValue={editing.description}/></label>
      <button className="primary">Salvar</button>
    </form></Modal>}
  </>;
}
