# NovaFlow — Gestão de Ordens e Atendimentos

O **NovaFlow** é uma plataforma web multissegmento para organizar ordens de serviço, solicitações, atendimentos e rotinas operacionais. O sistema combina gestão de clientes, equipes, setores e serviços com módulos opcionais, controle granular de acesso e uma interface responsiva.

O modelo é flexível: uma ordem pode representar um reparo, atendimento ao aluno, solicitação administrativa, consulta, visita técnica, manutenção ou outro fluxo de trabalho. Recursos financeiros são independentes e podem permanecer desativados.

## Visão geral

- Gestão de clientes, funcionários, setores, serviços e ordens.
- Dashboard operacional adaptado aos módulos e às permissões do usuário.
- Autenticação JWT com access token e refresh token rotacionado.
- Controle de acesso por cargo, perfil, permissões explícitas e escopo de dados.
- Agenda, financeiro, planos e verticais para academia ou escola como módulos opcionais.
- Comentários, checklist, subtarefas, anexos e histórico nas ordens.
- Busca global, notificações, auditoria, relatórios e exportações CSV.
- Dados demonstrativos identificados e removíveis de forma seletiva.
- API REST documentada automaticamente com OpenAPI e Swagger UI.
- Execução local com SQLite ou ambiente completo com Docker e PostgreSQL.

## Segmentos atendidos

A nomenclatura exibida pela aplicação pode ser adaptada em **Configurações**. Assim, clientes, funcionários, ordens e serviços podem assumir termos adequados ao negócio.

| Segmento | Exemplos de uso |
| --- | --- |
| Assistência técnica | Diagnóstico, orçamento, reparo, testes e entrega |
| Oficinas | Inspeções, revisões, manutenção e previsão de entrega |
| Academias | Alunos, modalidades, matrículas e avaliações físicas |
| Escolas e cursos | Alunos, responsáveis, turmas, cursos e matrículas |
| Clínicas e consultórios | Atendimentos e solicitações internas |
| Condomínios | Manutenção, limpeza, segurança e demandas administrativas |
| Prestadores de serviço | Chamados, visitas técnicas, SLA e acompanhamento por equipe |
| Pequenas e médias empresas | Suporte interno, tarefas e rotinas operacionais |

## Funcionalidades implementadas

### Operação

- Ordens com numeração automática, cliente, serviço, responsável, setor, prioridade, status e prazo.
- Valor opcional na ordem, condicionado ao acesso ao módulo financeiro.
- Detalhamento com comentários, checklist, subtarefas, anexos e histórico.
- Clientes com busca, filtros, edição, situação e exclusão lógica.
- Funcionários vinculados a cargos, perfis e setores.
- Catálogo de serviços com categoria, preço opcional, tempo estimado e SLA.
- Agenda com compromissos, atendimentos, vínculos operacionais e cancelamento.
- Notificações com controle de leitura.

### Gestão e análise

- Dashboard com métricas operacionais e seções condicionadas aos módulos ativos.
- Relatórios por status, serviço e responsável.
- Exportação CSV de ordens e dados financeiros, conforme permissão.
- Busca global limitada aos recursos autorizados.
- Auditoria de operações administrativas e alterações de acesso.
- Configuração da empresa, segmento, identidade visual, nomenclaturas e módulos.

### Administração de acesso

- Perfis padrão: Administrador, Gerente, Supervisor, Analista, Atendente, Operacional, Financeiro e Visualizador.
- Cargos reutilizáveis associados a perfis e escopos padrão.
- Perfis personalizados com permissões explícitas, sem herança implícita.
- Exceções individuais de permissão com justificativa e auditoria.
- Confirmação para mudanças que afetem usuários vinculados.
- Proteção contra a desativação do último administrador válido.
- Reavaliação das permissões no banco a cada requisição, inclusive com o mesmo JWT.

## Módulos

Os módulos são feature flags persistidas no backend. Desativar um módulo oculta sua navegação e bloqueia seus endpoints sem excluir os registros; os dados voltam a aparecer quando ele é reativado.

| Módulo | Recursos principais |
| --- | --- |
| Financeiro | Pagamentos, cobranças, recebimentos, indicadores e exportação |
| Agenda | Compromissos, atendimentos, agenda individual e cancelamentos |
| Planos | Planos, contratos, assinaturas, renovações e cobranças recorrentes |
| Academia | Alunos, matrículas, modalidades e avaliações físicas |
| Escola | Alunos, responsáveis, matrículas, turmas, cursos, coordenação e documentos |

Academia e Escola são mutuamente exclusivas na configuração atual. O acesso a um recurso opcional exige simultaneamente o módulo ativo, autorização do usuário para o módulo e a permissão correspondente.

## Modelo de autorização

O acesso efetivo segue uma única cadeia:

```text
Funcionário → Cargo → Perfil → Permissões explícitas → Escopo → Módulos ativos
```

Contas técnicas sem funcionário podem receber um perfil diretamente. Uma conta vinculada a funcionário sempre deriva seu perfil do cargo associado ao funcionário.

| Escopo | Alcance |
| --- | --- |
| `OWN` | Registros próprios ou atribuídos ao funcionário |
| `DEPARTMENT` | Registros do setor associado |
| `MANAGED_DEPARTMENTS` | Registros dos setores explicitamente gerenciados |
| `ALL` | Todos os setores; padrão do perfil Administrador |

Os limites são aplicados a listas, detalhes, alterações, dashboard, busca, relatórios e exportações. Permissões administrativas de sistema são reservadas ao perfil Administrador.

Mais detalhes estão em [docs/ACCESS_CONTROL.md](docs/ACCESS_CONTROL.md).

## Stack tecnológica

### Backend

- Python 3.12
- FastAPI 0.116.1
- SQLAlchemy 2.0.43
- Pydantic Settings 2.10.1
- PostgreSQL com Psycopg 3.2.9
- SQLite para desenvolvimento e testes
- JWT com `python-jose`
- Hash de senhas com Passlib e bcrypt
- Pytest e HTTPX

### Frontend

- React
- TypeScript
- Vite
- Lucide React
- Recharts
- CSS responsivo com design system próprio

### Infraestrutura

- Docker e Docker Compose
- PostgreSQL 17 Alpine
- Nginx
- Imagens base Python 3.12 Slim e Node.js 22 Alpine

## Arquitetura

O projeto utiliza uma aplicação React de página única consumindo uma API REST FastAPI. O backend concentra persistência, autenticação, autorização, escopos, módulos e regras de negócio. No ambiente Docker, o frontend é compilado com Vite e servido pelo Nginx, enquanto o PostgreSQL mantém os dados.

```text
NovaFlow/
├── backend/
│   ├── app/
│   │   ├── access.py          # Cálculo do acesso efetivo
│   │   ├── access_api.py      # Perfis, cargos, usuários e permissões
│   │   ├── access_seed.py     # Catálogo e migração de acesso
│   │   ├── api.py             # Endpoints funcionais
│   │   ├── config.py          # Configuração por ambiente
│   │   ├── database.py        # Engine e sessões SQLAlchemy
│   │   ├── main.py            # Inicialização da API
│   │   ├── models.py          # Modelos persistidos
│   │   ├── modules.py         # Feature flags e permissões modulares
│   │   ├── schemas.py         # Contratos Pydantic
│   │   ├── scopes.py          # Filtros de escopo
│   │   ├── security.py        # JWT, senhas e usuário autenticado
│   │   └── seed.py            # Dados demonstrativos
│   ├── tests/                 # Testes de integração e acesso
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── components/        # Componentes reutilizáveis
│   │   ├── pages/             # Páginas por domínio
│   │   ├── api.ts             # Cliente HTTP autenticado
│   │   ├── App.tsx            # Rotas e navegação
│   │   ├── modules.tsx        # Contexto de módulos e permissões
│   │   └── main.tsx           # Entrada da aplicação
│   ├── nginx.conf
│   ├── npm-local.cmd
│   ├── Dockerfile
│   └── package.json
├── docs/
│   └── ACCESS_CONTROL.md
├── .env.example
├── docker-compose.yml
└── LICENSE
```

## Entidades principais

- `User`, `Profile`, `Permission` e `JobPosition`: identidade e autorização.
- `Customer`, `Employee`, `Department` e `Service`: cadastros operacionais.
- `Order`, `OrderHistory`, `OrderComment`, `ChecklistItem`, `OrderTask` e `OrderAttachment`: ciclo das ordens.
- `Appointment` e `Notification`: agenda e comunicação interna.
- `Payment`, `Plan` e `Subscription`: financeiro e recorrência.
- `ModuleRecord`: registros dos fluxos opcionais de Academia, Escola, Planos e Financeiro.
- `AuditLog` e `CompanySettings`: rastreabilidade e configuração da aplicação.

## API

Todos os endpoints funcionais usam o prefixo `/api`.

| Grupo | Rotas principais |
| --- | --- |
| Autenticação | `/api/auth/login`, `/api/auth/refresh`, `/api/auth/me`, `/api/auth/context` |
| Ordens | `/api/orders`, `/api/orders/{id}`, comentários, checklist, subtarefas e anexos |
| Cadastros | `/api/customers`, `/api/employees`, `/api/services`, `/api/departments` |
| Acesso | `/api/users`, `/api/profiles`, `/api/job-positions`, `/api/permissions` |
| Módulos | `/api/settings/modules`, `/api/module-data/{module}/{resource}` |
| Agenda e financeiro | `/api/appointments`, `/api/payments`, `/api/plans`, `/api/billing/generate` |
| Análise | `/api/dashboard`, `/api/search`, `/api/reports` e exportações CSV |
| Administração | `/api/settings`, `/api/audit`, `/api/admin/demo-data` |

Documentação disponível durante a execução:

- Swagger UI: `http://localhost:8000/docs`
- OpenAPI JSON: `http://localhost:8000/openapi.json`
- Health check: `http://localhost:8000/health`

## Pré-requisitos

Escolha uma das formas de execução:

- Docker Desktop com Docker Compose; ou
- Python 3.12 e Node.js para desenvolvimento local.

## Configuração de ambiente

Copie `.env.example` para `.env` na raiz do projeto:

```bash
cp .env.example .env
```

No PowerShell:

```powershell
Copy-Item .env.example .env
```

| Variável | Finalidade | Valor de desenvolvimento no exemplo |
| --- | --- | --- |
| `SECRET_KEY` | Assinatura dos tokens JWT | `change-this-in-production` |
| `DATABASE_URL` | Conexão SQLAlchemy | `sqlite:///./novaflow.db` |
| `CORS_ORIGINS` | Origens autorizadas, separadas por vírgula | `http://localhost:5173` |
| `DEMO_SEED` | Carrega os dados demonstrativos | `true` |
| `VITE_API_URL` | URL da API utilizada no build do frontend | `http://localhost:8000/api` |
| `ACCESS_TOKEN_MINUTES` | Duração do access token | opcional; padrão `480` |

Use uma chave aleatória forte em `SECRET_KEY` fora do desenvolvimento. O arquivo `.env` não deve ser versionado.

## Executando com Docker

Na raiz do repositório:

```bash
docker compose up --build
```

Serviços expostos:

- Frontend: `http://localhost:3000`
- API: `http://localhost:8000`
- Swagger UI: `http://localhost:8000/docs`

Para executar em segundo plano:

```bash
docker compose up --build -d
```

Para acompanhar os logs:

```bash
docker compose logs -f
```

Para encerrar:

```bash
docker compose down
```

O volume nomeado `postgres_data` preserva o banco PostgreSQL entre reinicializações dos contêineres.

## Desenvolvimento local

### Backend

No PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Se a ativação de scripts estiver bloqueada, o ambiente pode ser usado sem ativação:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Por padrão, a execução local utiliza SQLite e cria `backend/novaflow.db` quando o comando é executado dentro da pasta `backend`.

### Frontend

Em outro terminal:

```bash
cd frontend
npm install
npm run dev
```

O servidor de desenvolvimento do Vite fica disponível em `http://localhost:5173`.

No Windows, caso Node.js/npm não estejam no `PATH` ou o PowerShell bloqueie `npm.ps1`, utilize o lançador incluído:

```powershell
cd frontend
.\npm-local.cmd install
.\npm-local.cmd run dev
```

### Scripts do frontend

| Comando | Descrição |
| --- | --- |
| `npm run dev` | Inicia o Vite em modo de desenvolvimento |
| `npm run build` | Executa a verificação TypeScript e gera o build de produção |
| `npm run preview` | Serve localmente o build gerado |

## Migração e dados iniciais

Na inicialização, o backend cria as tabelas ausentes e executa atualizações compatíveis e idempotentes para instalações existentes. O projeto não utiliza uma ferramenta externa de migrations: as adaptações atuais ficam em `main.py` e `access_seed.py`.

Com `DEMO_SEED=true`, a aplicação cria dados demonstrativos identificados com `is_demo=true`. O administrador pode removê-los em **Configurações → Dados demonstrativos**, confirmando a frase `EXCLUIR DADOS DEMO`. A operação preserva dados reais e registra o resultado na auditoria.

## Credenciais demonstrativas

Estas contas existem somente quando o seed demonstrativo está habilitado e não devem ser utilizadas em produção.

| Perfil | E-mail | Senha |
| --- | --- | --- |
| Administrador | `admin@demo.com` | `Admin@123` |
| Gerente | `gerente@demo.com` | `Demo@123` |
| Atendente | `atendente@demo.com` | `Demo@123` |
| Operacional | `tecnico@demo.com` | `Demo@123` |

## Testes

Os testes usam Pytest, HTTPX e um banco SQLite temporário isolado. A partir da pasta `backend`:

```powershell
python -m pytest -q
```

A suíte cobre autenticação, operações principais, módulos, escopos, cargos, perfis, permissões, prevenção de escalada, atualização de acesso com o mesmo JWT, limpeza dos dados demonstrativos e migração idempotente.

## Segurança

- Senhas são armazenadas como hash bcrypt.
- Access tokens possuem expiração configurável.
- Refresh tokens são aleatórios, armazenados como hash e rotacionados.
- O JWT contém a identidade do usuário; o acesso efetivo é consultado no banco.
- Endpoints administrativos exigem permissões reservadas ao Administrador.
- Módulos desativados bloqueiam os respectivos endpoints.
- Consultas e mutações respeitam o escopo efetivo do usuário.
- Uploads aceitam tipos definidos pela API e são limitados a 10 MB.
- Registros operacionais importantes utilizam exclusão lógica.
- CORS é configurável por ambiente.

Antes de publicar, altere `SECRET_KEY`, configure HTTPS, restrinja `CORS_ORIGINS`, use credenciais próprias para o PostgreSQL e desative `DEMO_SEED` quando os dados de exemplo não forem necessários.

## Repositório

- GitHub: [poucklack-dev/Ordem-de-Servico-NovaFlow](https://github.com/poucklack-dev/Ordem-de-Servico-NovaFlow)

## Autor

**Emanuel Sousa Vasconcellos Lima**

Desenvolvedor com foco em aplicações web, análise de dados, automação e sistemas de gestão.

## Licença

Este projeto está licenciado sob a licença MIT. Consulte o arquivo [LICENSE](LICENSE) para mais informações.
