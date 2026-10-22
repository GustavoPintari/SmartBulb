# smartbulb

Controle local de uma lâmpada Tuya (Elgin "Smart color") sem nuvem e sem app proprietário:
interface web (desktop e celular), cenas, API REST, agendamentos e CLI.

```
smartbulb/
├── app/
│   ├── bulb.py          # controle da lâmpada (tinytuya, protocolo local 3.5)
│   ├── schedules.py     # agendamentos persistidos em /data/schedules.json
│   ├── main.py          # API FastAPI + autenticação
│   └── static/          # interface web (index.html)
├── lampada.py           # CLI: status, on, off, toggle, cor, brilho, branco, cena, timer
├── Containerfile
└── deploy/smartbulb.container   # unidade Podman Quadlet (systemd)
```

## Requisitos

- O servidor precisa estar **na mesma rede local** que a lâmpada (ele fala direto com `192.168.1.105:6668`).
- IP da lâmpada reservado no DHCP e `local_key` obtida pelo `tinytuya wizard`.

## Implantação (Podman rootless + Quadlet)

No servidor, dentro da pasta do projeto:

```bash
podman build -t localhost/smartbulb:latest .
```

Secrets (não ficam em arquivos nem no ambiente do container). A chave sai do `devices.json` do wizard:

```bash
python3 -c "import json;print(json.load(open('devices.json'))[0]['key'],end='')" | podman secret create bulb_key -
```

```bash
read -rsp 'Senha da interface: ' PW && printf %s "$PW" | podman secret create app_password - && unset PW
```

Configuração e serviço:

```bash
mkdir -p ~/.config/smartbulb ~/.config/containers/systemd
cp .env.example ~/.config/smartbulb/smartbulb.env      # revise ID/IP/usuário
cp deploy/smartbulb.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start smartbulb
loginctl enable-linger $USER                           # roda sem sessão aberta
```

Acesse `http://<ip-do-servidor>:8080` (usuário `admin` e a senha criada). No Fedora, libere a porta na rede local se precisar:

```bash
sudo firewall-cmd --permanent --add-port=8080/tcp && sudo firewall-cmd --reload
```

Logs e atualização:

```bash
journalctl --user -u smartbulb -f
```

```bash
podman build -t localhost/smartbulb:latest . && systemctl --user restart smartbulb
```

Os agendamentos ficam no volume `smartbulb-data` e sobrevivem a reinícios e atualizações.

## Acesso de qualquer lugar

**Não exponha a porta 8080 direto na internet** (HTTP sem TLS = senha trafegando em texto puro). Opções, da mais simples/segura para a menos:

1. **Tailscale** (recomendado): instale no servidor e no celular; acesse `http://<nome-do-servidor>:8080` de qualquer lugar,
   sem abrir portas no roteador. Com `tailscale serve --bg 8080` você ganha HTTPS em `https://<servidor>.<tailnet>.ts.net`.
2. **WireGuard** no roteador/servidor: mesma ideia, auto-hospedado.
3. **Proxy reverso com HTTPS** (Caddy, Traefik, Nginx) ou **Cloudflare Tunnel** se quiser um domínio público.
   Mantenha a senha forte; o login é HTTP Basic, então só use atrás de HTTPS.

No celular, "Adicionar à tela inicial" abre a interface como um app.

## API

Todas as rotas (exceto `/healthz`) exigem HTTP Basic.

| Método | Rota | Corpo |
|---|---|---|
| GET | `/api/info` | — (nome e lista de cenas) |
| GET | `/api/status` | — |
| POST | `/api/power` | `{"on": true}` |
| POST | `/api/toggle` | — |
| POST | `/api/color` | `{"color": "#ff8800"}` ou `{"color": "vermelho"}` |
| POST | `/api/brightness` | `{"value": 1-100}` |
| POST | `/api/white` | `{"temperature": 0-100}` (0 = quente) |
| POST | `/api/scene` | `{"scene": "night"}` |
| POST | `/api/timer` | `{"minutes": 30}` (0 cancela) |
| GET | `/api/schedules` | — |
| POST | `/api/schedules` | `{"time": "18:30", "days": [0,1,2,3,4], "action": "on"}` |
| PUT | `/api/schedules/{id}` | igual ao POST |
| DELETE | `/api/schedules/{id}` | — |

`days`: 0 = segunda … 6 = domingo. `action`: `on`, `off`, `color`, `white`, `brightness`, `scene` (com `value` quando aplicável).

Cenas (`scene`): `night` (Noite), `read` (Leitura), `work` (Trabalho), `leisure` (Lazer),
`soft` (Suave), `rainbow` (Arco-íris), `shine` (Piscar), `gorgeous` (Deslumbrante).
São as cenas de fábrica da Tuya, gravadas no DPS 25.

Exemplo:

```bash
curl -u admin:SENHA -H 'Content-Type: application/json' -d '{"on":false}' http://servidor:8080/api/power
```

## CLI

Fora do container (lê `~/devices.json` ou `BULB_ID`/`BULB_IP`/`BULB_KEY`):

```bash
pip install tinytuya
python3 lampada.py status
```

Dentro do container:

```bash
podman exec smartbulb python lampada.py cena noite
```

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `BULB_ID`, `BULB_IP`, `BULB_VERSION` | — / `Auto` / `3.5` | dados da lâmpada |
| `BULB_KEY` ou `BULB_KEY_FILE` | — | local key (prefira o arquivo/secret) |
| `BULB_LABEL` | `Smart lamp` | nome exibido na interface |
| `BULB_DEVICES_JSON`, `BULB_NAME` | `~/devices.json`, `Smart color` | alternativa às variáveis acima |
| `APP_USER` | `admin` | usuário da interface |
| `APP_PASSWORD` ou `APP_PASSWORD_FILE` | — | senha (obrigatória) |
| `AUTH_DISABLED` | — | `1` desliga o login (só para rede confiável) |
| `TZ` | `America/Sao_Paulo` | fuso dos agendamentos |
| `DATA_DIR` | `/data` | onde salvar `schedules.json` |

## Observações

- Se a lâmpada for resetada/pareada de novo, a `local_key` (e às vezes o ID) muda: rode o wizard e recrie o secret `bulb_key`.
- A lâmpada aceita uma conexão por vez; a API serializa os comandos.
