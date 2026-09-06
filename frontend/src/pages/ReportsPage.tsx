import {useEffect, useState} from 'react';
import {api, download} from '../api';
import {Download} from 'lucide-react';
import {Loading, Title, money} from '../components/UI';
import {useModules} from '../modules';

function Panel({title, rows}: {title: string; rows: any[]}) {
  return <article className="panel report"><h3>{title}</h3>{rows.map(([label, value]) => <div key={label}><span>{label}</span><b>{String(value)}</b></div>)}</article>;
}

const translated: Record<string, string> = {
  contracts: 'Contratos', subscriptions: 'Assinaturas', renewals: 'Renovações',
  enrollments: 'Matrículas', assessments: 'Avaliações', modalities: 'Modalidades',
  guardians: 'Responsáveis', classes: 'Turmas', courses: 'Cursos', coordination: 'Coordenação',
  documents: 'Documentos', students: 'Alunos', plans: 'Planos', occupancy: 'Ocupação',
};

export default function ReportsPage() {
  const [data, setData] = useState<any>();
  const {allowed} = useModules();
  useEffect(() => { api('/reports').then(setData); }, []);
  if (!data) return <Loading/>;
  const moduleRows = (value: any) => Object.entries(value).map(([key, count]) => [translated[key] || key, count]);
  const actions = <div className="title-actions">
    {allowed('reports.export') && <button className="table-action" onClick={() => download('/reports/orders.csv', 'ordens.csv')}><Download size={16}/>Ordens CSV</button>}
    {data.financial && allowed('financial.export') && <button className="table-action" onClick={() => download('/reports/financial.csv', 'financeiro.csv')}><Download size={16}/>Financeiro CSV</button>}
  </div>;
  return <>
    <Title title="Relatórios" sub="Indicadores exibidos conforme os módulos habilitados" action={actions}/>
    <div className="report-grid">
      <Panel title="Visão operacional" rows={[["Ordens", data.core.overview.orders], ["Clientes", data.core.overview.customers], ["SLA", `${data.core.overview.sla}%`]]}/>
      <Panel title="Ordens por status" rows={data.core.by_status.map((x: any) => [x.label, x.value])}/>
      <Panel title="Serviços e demanda" rows={data.core.by_service.map((x: any) => [x.label, x.value])}/>
      <Panel title="Produtividade" rows={data.core.by_employee.map((x: any) => [x.label, x.value])}/>
      {data.financial && <Panel title="Financeiro" rows={[["Receita", money(data.financial.received)], ["Inadimplência / pendente", money(data.financial.pending)], ["Pagamentos", data.financial.payments], ["Ticket médio", money(data.financial.average_ticket)]]}/>}
      {data.agenda && <Panel title="Agenda" rows={[["Agendamentos", data.agenda.appointments], ["Cancelamentos", data.agenda.canceled], ["Não comparecimentos", data.agenda.no_show]]}/>}
      {data.plans && <Panel title="Planos e contratos" rows={moduleRows(data.plans)}/>}
      {data.academy && <Panel title="Academia" rows={moduleRows(data.academy)}/>}
      {data.school && <Panel title="Escola" rows={moduleRows(data.school)}/>}
    </div>
  </>;
}
