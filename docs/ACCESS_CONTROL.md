# Cargos, perfis e acesso

Funcionários recebem seu perfil pelo cargo associado. Contas técnicas sem funcionário recebem um perfil diretamente. A API rejeita misturar as duas fontes ou enviar o antigo campo `role` como configuração de acesso.

```text
Funcionário → Cargo → Perfil → Permissões explícitas → Escopo
Conta sem funcionário → Perfil → Permissões explícitas → Escopo
```

O módulo precisa estar ativo e autorizado à conta para que suas permissões tenham efeito. Ter acesso ao Financeiro, por exemplo, não ativa o módulo no estabelecimento.

## Administração

Em Configurações, cadastre perfis, cargos e setores. No cargo, escolha o perfil associado, setor opcional e escopo padrão. No funcionário, selecione o cargo e setor; o perfil aparece automaticamente.

Os perfis iniciais são Administrador, Gerente, Supervisor, Analista, Atendente, Operacional, Financeiro e Visualizador. Os cargos Analista Financeiro e Assistente Financeiro utilizam o perfil Financeiro. É possível criar novos cargos que reutilizem um perfil e criar perfis personalizados com permissões explícitas.

Os nomes dos cargos e a posição aparente na hierarquia não concedem permissões. Perfis superiores não herdam automaticamente permissões de outros perfis.

Somente o perfil Administrador pode possuir permissões administrativas. Alterar o vínculo de um cargo exige também `roles.manage`. Alterações sensíveis em cargos e perfis mostram a quantidade de usuários afetados e exigem confirmação. O sistema impede remover o último administrador ativo capaz de gerenciar o acesso.

Permissões individuais são exceções: exigem justificativa e auditoria. A configuração normal deve permanecer no perfil.

## Escopos

| Escopo | Limite de acesso |
| --- | --- |
| `OWN` | Registros próprios ou atribuídos ao funcionário, conforme o tipo de registro. |
| `DEPARTMENT` | Registros do setor associado. |
| `MANAGED_DEPARTMENTS` | Registros dos setores explicitamente gerenciados. |
| `ALL` | Todos os registros; padrão exclusivo do Administrador. |

O cadastro exige um setor para `DEPARTMENT` e setores gerenciados para `MANAGED_DEPARTMENTS`. Ausência de atribuição não amplia o acesso. Uma autorização de leitura não autoriza editar, atribuir, excluir ou exportar.

Os mesmos limites se aplicam às listas, detalhes, alterações, busca, indicadores, relatórios e exportações. Clientes relacionados a ordens acessíveis podem ser consultados no contexto dessas ordens. Definições compartilhadas de catálogo sem setor, como serviços e planos, podem ser consultadas; alterá-las exige acesso global e a permissão de edição correspondente.

## Atualização de acesso e auditoria

A autorização é recalculada a partir do banco em cada requisição. O JWT identifica a conta; suas permissões não ficam congeladas no token. Alterar cargo, perfil, permissões ou escopo passa a valer na próxima requisição, inclusive com o mesmo JWT.

A auditoria registra autor, entidade, valores anteriores e novos e usuários afetados nas alterações de acesso. Registros fora do escopo retornam `404`; operações sem a permissão necessária retornam `403`. Módulos desativados retornam `404`.

## Atualização de instalações existentes

A inicialização adiciona as novas estruturas de forma idempotente e preserva os dados existentes. Os antigos perfis de usuários são convertidos em perfis diretos de contas ainda sem funcionário. Nenhum vínculo usuário–funcionário é inferido por nome ou e-mail.

Cargos antigos com correspondência exata são associados ao perfil padrão correspondente. Cargos desconhecidos preservam seu nome e recebem Visualizador com escopo próprio para revisão administrativa. O administrador deve revisar esses cargos e vincular as contas aos funcionários corretos em Usuários.

Permissões individuais antigas são migradas para exceções explícitas uma única vez. As colunas antigas de compatibilidade não são usadas para conceder acesso. Reiniciar o sistema não restaura permissões ou escopos que o administrador tenha editado.

## Verificação automatizada

Na pasta `backend`, execute:

```powershell
python -m pytest tests -q
```

Os testes utilizam um banco SQLite temporário isolado, restaurado antes de cada teste. Eles não alteram `novaflow.db`. A cobertura inclui os oito critérios de aceite, tentativas de escalada, mudanças usando o mesmo JWT, confirmação de impacto, quatro escopos, módulos desativados, permissões financeiras independentes e migração idempotente.
