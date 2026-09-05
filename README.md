# NovaFlow — Gestão de Ordens e Atendimentos

O **NovaFlow** é uma plataforma web multissegmento para organizar solicitações, atendimentos, tarefas e ordens de serviço. Em vez de limitar a aplicação a um único tipo de empresa, o sistema permite adaptar nomenclaturas e ativar módulos conforme o negócio.

Uma “ordem” não precisa representar uma venda ou serviço cobrado. Ela pode ser um atendimento ao aluno, solicitação administrativa, chamado de manutenção, consulta, visita técnica ou qualquer fluxo operacional. Valores e recursos financeiros são opcionais.

## Para quais negócios o NovaFlow funciona?

### Academias

- Clientes podem ser chamados de **Alunos**.
- Funcionários podem ser apresentados como **Professores**.
- Ordens podem representar avaliações físicas, manutenção de equipamentos ou atendimentos.
- Planos e financeiro podem ser habilitados somente quando necessários.

### Escolas e cursos

- Clientes podem ser apresentados como **Alunos** ou **Responsáveis**.
- Setores organizam secretaria, coordenação, professores e financeiro.
- Ordens podem representar matrículas, emissão de documentos, solicitações e atendimentos pedagógicos.
- Campos personalizados permitem registrar turma, curso ou número de matrícula.

### Assistências técnicas

- Ordens acompanham diagnóstico, orçamento, reparo, testes e entrega.
- Checklists e subtarefas registram cada etapa do atendimento.
- Comentários, anexos, responsáveis, prioridade e SLA centralizam o histórico técnico.
- O módulo financeiro pode controlar pagamentos relacionados aos reparos.

### Oficinas

- Ordens podem representar inspeções, revisões e reparos.
- Campos personalizados permitem adicionar placa, modelo, quilometragem ou chassi.
- A agenda organiza recebimentos, execução e previsão de entrega.

### Clínicas e consultórios

- Ordens podem ser utilizadas como atendimentos e solicitações internas.
- Setores, agenda, responsáveis e comentários apoiam o fluxo operacional.
- O financeiro permanece independente e opcional.

### Condomínios

- Moradores ou unidades podem ser tratados como clientes.
- Ordens registram manutenção, limpeza, segurança e solicitações administrativas.
- Prioridades, responsáveis, prazos e histórico facilitam a prestação de contas.

### Prestadores de serviço e empresas de manutenção

- Controle de chamados internos ou externos.
- Distribuição por equipe, funcionário e setor.
- Acompanhamento de prazo, SLA, checklist, comentários e conclusão.
- Indicadores de produtividade e relatórios operacionais.

### Pequenas e médias empresas

- Solicitações administrativas, suporte interno e rotinas operacionais.
- Cadastro de clientes, colaboradores, serviços e departamentos.
- Perfis de acesso e auditoria das principais alterações.

## Adaptação por segmento

Em **Configurações**, o administrador pode escolher o segmento e personalizar os termos exibidos na interface:

| Termo padrão | Academia | Escola | Assistência técnica |
|---|---|---|---|
| Cliente | Aluno | Aluno/Responsável | Cliente |
| Funcionário | Professor | Professor | Técnico |
| Ordem | Atendimento | Solicitação | Ordem de Serviço |
| Serviço | Avaliação | Atendimento | Manutenção |

Também é possível habilitar ou desabilitar módulos como Financeiro, Agenda, Planos, Academia e Escola. Campos personalizados complementam o cadastro sem exigir alterações manuais no banco de dados.

## Funcionalidades

- Autenticação JWT com refresh token e senhas protegidas por bcrypt.
- Perfis Administrador, Gerente, Atendente, Operacional, Financeiro e Visualizador.
- Dashboard com indicadores operacionais e financeiros.
- Clientes com filtros, edição, status e exclusão lógica.
- Funcionários, setores e serviços com filtros e edição.
- Ordens com numeração automática, prioridade, status, responsável, setor e prazo.
- Valor opcional nas ordens; financeiro tratado como módulo independente.
- Página detalhada da ordem com comentários, checklist, subtarefas e histórico.
- Anexos com validação de tipo e limite de tamanho.
- Agenda operacional para compromissos, atendimentos e visitas.
- Notificações com controle de leitura.
- Pagamentos com filtros por situação, forma e ordem.
- Planos e contratos recorrentes opcionais.
- Busca global por ordens e clientes.
- Relatórios operacionais e exportação CSV.
- Auditoria de cadastros, edições, mudanças de status e exportações.
- Configuração de empresa, segmento, identidade visual e nomenclaturas.
- Exclusão segura e seletiva dos dados demonstrativos.
- Interface escura e responsiva para computador, tablet e celular.
- API documentada automaticamente com Swagger/OpenAPI.

## Tecnologias

### Backend

- Python 3.12
- FastAPI
- SQLAlchemy
- Pydantic
- JWT e bcrypt
- PostgreSQL em produção e SQLite para desenvolvimento/testes

### Frontend

- React
- TypeScript
- Vite
- Lucide Icons
- CSS responsivo com design system próprio

### Infraestrutura

- Docker
- Docker Compose
- Nginx
- PostgreSQL

## Arquitetura

```text
NovaFlow/
├── backend/
│   ├── app/
│   │   ├── api.py          # Endpoints e fluxos da aplicação
│   │   ├── config.py       # Configurações e variáveis de ambiente
│   │   ├── database.py     # Engine e sessões SQLAlchemy
│   │   ├── main.py         # Inicialização do FastAPI
│   │   ├── models.py       # Entidades e relacionamentos
│   │   ├── schemas.py      # Validação Pydantic
│   │   ├── security.py     # JWT, senhas e autorização
│   │   └── seed.py         # Dados demonstrativos identificados
│   ├── tests/
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/     # Componentes reutilizáveis
│   │   ├── pages/          # Páginas por domínio
│   │   ├── api.ts          # Cliente HTTP autenticado
│   │   ├── App.tsx         # Navegação principal
│   │   └── main.tsx        # Entrada da aplicação
│   ├── Dockerfile
│   └── package.json
├── docker-compose.yml
└── .env.example
```

## Executando com Docker

Pré-requisitos:

- Docker Desktop
- Docker Compose

Na raiz do projeto, execute:

```bash
docker compose up --build
```

Serviços disponíveis:

- Aplicação: http://localhost:3000
- API: http://localhost:8000
- Swagger: http://localhost:8000/docs
- OpenAPI: http://localhost:8000/openapi.json

Para executar em segundo plano:

```bash
docker compose up --build -d
```

Para encerrar:

```bash
docker compose down
```

## Desenvolvimento local

### Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Copie `.env.example` para `.env` para personalizar o banco, segredo JWT, CORS e carregamento dos dados de demonstração.

## Credenciais demonstrativas

| Perfil | E-mail | Senha |
|---|---|---|
| Administrador | `admin@demo.com` | `Admin@123` |
| Gerente | `gerente@demo.com` | `Demo@123` |
| Atendente | `atendente@demo.com` | `Demo@123` |
| Operacional | `tecnico@demo.com` | `Demo@123` |

## Removendo os dados demonstrativos

O administrador pode acessar **Configurações → Dados demonstrativos** e excluir definitivamente os registros criados pelo seed.

A operação:

1. exige perfil Administrador;
2. exige a frase `EXCLUIR DADOS DEMO`;
3. seleciona somente registros marcados com `is_demo=true`;
4. executa a limpeza dentro de uma transação;
5. preserva registros reais;
6. registra o resultado na auditoria.

## Testes

```bash
cd backend
pytest
```

A suíte cobre autenticação, acesso à API, clientes, ordens, comentários, checklist, mudança de status, filtros e edição de registros.

## Segurança

- Senhas nunca são armazenadas em texto puro.
- Tokens de acesso possuem expiração.
- Refresh tokens são aleatórios, armazenados como hash e rotacionados.
- Endpoints administrativos exigem perfil adequado.
- Consultas usam SQLAlchemy com parâmetros vinculados.
- CORS é configurável por ambiente.
- Uploads validam formato e tamanho.
- Registros operacionais importantes utilizam exclusão lógica.

Antes de publicar em produção, altere `SECRET_KEY`, configure HTTPS, revise `CORS_ORIGINS` e desative `DEMO_SEED` após preparar o ambiente.

## Roadmap

- Armazenamento externo compatível com S3.
- Exportações adicionais em XLSX e PDF.
- Integrações com e-mail, WhatsApp e assinatura eletrônica.
- Aplicativos móveis e portal externo do cliente.

## Licença

Projeto desenvolvido para portfólio e evolução como produto SaaS. Defina uma licença antes da distribuição comercial.
