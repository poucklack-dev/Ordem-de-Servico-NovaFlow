import {useEffect, useState} from 'react';
import {api} from '../api';
import {CheckCircle2, Plus, RefreshCw, Trash2} from 'lucide-react';
import {Empty, Modal, Notice, Title, money} from '../components/UI';
import {useModules} from '../modules';

type OpenForm = 'payment' | 'charge' | null;

export default function FinancePage() {
  const [payments, setPayments] = useState<any[]>([]);
  const [charges, setCharges] = useState<any[]>([]);
  const [orders, setOrders] = useState<any[]>([]);
  const [meta, setMeta] = useState<any>();
  const [open, setOpen] = useState<OpenForm>(null);
  const [message, setMessage] = useState('');
  const {modules, allowed} = useModules();
  const canCreate = allowed('financial.create');
  const canUpdate = allowed('financial.update');
  const canDelete = allowed('financial.delete');

  const load = () => Promise.all([
    api<any[]>('/payments'),
    api<any[]>('/module-data/financial/charges'),
  ]).then(([paymentRows, chargeRows]) => {
    setPayments(paymentRows);
    setCharges(chargeRows);
  });

  useEffect(() => {
    void load();
    api<any[]>('/orders').then(setOrders);
    api('/meta').then(setMeta);
  }, []);

  async function save(e: React.FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const values: any = Object.fromEntries(new FormData(e.currentTarget));
    if (open === 'payment') {
      await api('/payments', {method: 'POST', body: JSON.stringify({
        ...values,
        order_id: Number(values.order_id),
        amount: Number(values.amount),
        due_date: values.due_date || null,
      })});
    } else {
      const {status, ...data} = values;
      data.amount = Number(data.amount);
      await api('/module-data/financial/charges', {method: 'POST', body: JSON.stringify({data, status})});
    }
    setOpen(null);
    setMessage('Lançamento criado com sucesso.');
    await load();
  }

  async function generateRecurring() {
    const result = await api<{message: string}>('/billing/generate', {method: 'POST'});
    setMessage(result.message);
    await load();
  }

  async function markPaid(row: any) {
    if (row.recordKind === 'payment') await api(`/payments/${row.id}?status=Pago`, {method: 'PATCH'});
    else await api(`/module-data/financial/charges/${row.id}`, {method: 'PUT', body: JSON.stringify({data: row.source.data, status: 'Pago'})});
    setMessage('Lançamento marcado como pago.');
    await load();
  }

  async function removeCharge(row: any) {
    if (!window.confirm('Deseja remover esta cobrança?')) return;
    await api(`/module-data/financial/charges/${row.id}`, {method: 'DELETE'});
    setMessage('Cobrança removida.');
    await load();
  }

  const rows = [
    ...payments.map(x => ({...x, key: `payment-${x.id}`, recordKind: 'payment', origin: 'Ordem', reference: x.order})),
    ...charges.map(x => ({...x.data, id: x.id, key: `charge-${x.id}`, recordKind: 'charge', source: x, origin: 'Cobrança', reference: x.data.customer, status: x.status, method: x.data.method || '—'})),
  ];
  const received = rows.filter(x => x.status === 'Pago').reduce((sum, x) => sum + Number(x.amount || 0), 0);
  const pending = rows.filter(x => ['Pendente', 'Parcial', 'Atrasado'].includes(x.status)).reduce((sum, x) => sum + Number(x.amount || 0), 0);
  const actions = canCreate ? <div className="title-actions">
    <button className="primary" onClick={() => setOpen('payment')}><Plus size={17}/>Novo recebimento</button>
    <button className="table-action" onClick={() => setOpen('charge')}><Plus size={17}/>Nova cobrança</button>
    {modules.plans && <button className="table-action" onClick={generateRecurring}><RefreshCw size={17}/>Gerar recorrências</button>}
  </div> : undefined;

  return <>
    <Title title="Financeiro" sub="Recebimentos, contas a receber e pagamentos" action={actions}/>
    <Notice message={message}/>
    <div className="cards">
      <article className="metric"><div><small>Receita recebida</small><strong>{money(received)}</strong></div></article>
      <article className="metric"><div><small>Valores pendentes</small><strong>{money(pending)}</strong></div></article>
      <article className="metric"><div><small>Pagamentos vencidos</small><strong>{rows.filter(x => x.status === 'Atrasado').length}</strong></div></article>
    </div>
    <div className="panel table-panel"><table><thead><tr><th>Origem</th><th>Referência</th><th>Valor</th><th>Forma</th><th>Vencimento</th><th>Status</th>{(canUpdate || canDelete) && <th/>}</tr></thead><tbody>
      {rows.map(x => <tr key={x.key}><td>{x.origin}</td><td><b>{x.reference || '—'}</b></td><td>{money(Number(x.amount))}</td><td>{x.method || '—'}</td><td>{x.due_date || '—'}</td><td><span className="status">{x.status}</span></td>{(canUpdate || canDelete) && <td>{canUpdate && x.status !== 'Pago' && <button className="table-action" onClick={() => markPaid(x)}><CheckCircle2 size={15}/>Pagar</button>}{canDelete && x.recordKind === 'charge' && <button className="table-action" onClick={() => removeCharge(x)}><Trash2 size={15}/></button>}</td>}</tr>)}
    </tbody></table>{!rows.length && <Empty text="Nenhum lançamento financeiro"/>}</div>
    {open && canCreate && <Modal title={open === 'payment' ? 'Novo recebimento' : 'Nova cobrança'} onClose={() => setOpen(null)}>
      <form className="form" onSubmit={save}>
        {open === 'payment' ? <label>Ordem<select name="order_id" required>{orders.map(x => <option value={x.id} key={x.id}>{x.number} — {x.customer}</option>)}</select></label> : <label>Cliente/Aluno<input name="customer" required/></label>}
        <label>Valor<input name="amount" type="number" step=".01" min="0" required/></label>
        <div className="form-row">
          {open === 'payment' && <label>Forma<select name="method">{meta?.payment_methods.map((x: string) => <option key={x}>{x}</option>)}</select></label>}
          <label>Status<select name="status">{meta?.payment_statuses.map((x: string) => <option key={x}>{x}</option>)}</select></label>
        </div>
        <label>Vencimento<input name="due_date" type="date" required={open === 'charge'}/></label>
        <button className="primary">Salvar</button>
      </form>
    </Modal>}
  </>;
}
