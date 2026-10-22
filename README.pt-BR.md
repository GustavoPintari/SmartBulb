# SmartBulb

[English](README.md) · **Português (BR)**

Controle auto-hospedado e sem nuvem de uma lâmpada Wi-Fi Tuya (testado com a Elgin "Smart lamp",
protocolo 3.5). Sem app proprietário: interface web responsiva (desktop e celular), cenas,
agendamentos, API REST e CLI, todos falando com a lâmpada direto pela rede local.

```
smartbulb/
├── app/
│   ├── bulb.py          # controle da lâmpada (tinytuya, protocolo local)
│   ├── schedules.py     # agendamentos salvos em /data/schedules.json
│   ├── main.py          # API FastAPI + autenticação
│   ├── cli.py           # cliente de linha de comando
│   └── static/          # interface web
├── Containerfile
└── deploy/smartbulb.container   # unidade Podman Quadlet (systemd)
```

## Funcionalidades

- Liga/desliga, cor (cores prontas e seletor de cor), temperatura do branco e brilho
- As 8 cenas de fábrica da Tuya: Noite, Leitura, Trabalho, Lazer, Suave, Arco-íris, Piscar e Deslumbrante
- Timer para desligar
- Agendamentos recorrentes por dia da semana, executados dentro do container (sem cron)
- Tema claro e escuro; pode ser adicionado à tela inicial do celular
- Login HTTP Basic, segredos via podman secrets, container rootless e somente leitura

## Requisitos

- Uma lâmpada Tuya que use os DPS padrão de iluminação (20–26). Outras lâmpadas Tuya devem
  funcionar, mas só a lâmpada acima foi testada.
- Um servidor **na mesma rede local** da lâmpada (ele conecta direto em `<ip-da-lampada>:6668`).
- IP da lâmpada reservado no DHCP do roteador e a `local_key` dela.

## Como obter a local key

1. Pareie a lâmpada no app **Smart Life**. Apps de marca, como o Elgin Smart, não podem ser
   vinculados à plataforma de desenvolvedor da Tuya.
2. Em [platform.tuya.com](https://platform.tuya.com): **Cloud → Development → Create Cloud Project**
   (Smart Home, escolha o data center), depois **Devices → Link App Account** e leia o QR code com o
   Smart Life.
3. Rode o wizard com o Access ID e o Secret do projeto:
   ```bash
   pip install tinytuya
   ```
   ```bash
   python -m tinytuya wizard
   ```
   Ele gera o `devices.json` com o `id`, o `ip` e a `key` do dispositivo.
4. Opcional, mas recomendado: bloqueie o acesso da lâmpada à internet no roteador. O controle local
   continua funcionando. Não pareie a lâmpada de novo depois disso, porque o pareamento gera uma
   chave nova.

## Implantação (Podman rootless + Quadlet)

No servidor, dentro da pasta do projeto:

```bash
podman build -t localhost/smartbulb:latest .
```

Crie os secrets (eles nunca ficam em arquivos nem no ambiente do container). A chave sai do
`devices.json` do wizard:

```bash
python3 -c "import json;print(json.load(open('devices.json'))[0]['key'],end='')" | podman secret create bulb_key -
```

```bash
read -rsp 'Senha da interface: ' PW && printf %s "$PW" | podman secret create app_password - && unset PW
```

Configuração e serviço:

```bash
mkdir -p ~/.config/smartbulb ~/.config/containers/systemd
cp .env.example ~/.config/smartbulb/smartbulb.env      # revise BULB_ID / BULB_IP / APP_USER
cp deploy/smartbulb.container ~/.config/containers/systemd/
systemctl --user daemon-reload
systemctl --user start smartbulb
loginctl enable-linger $USER                           # continua rodando sem sessão aberta
```

Acesse `http://<ip-do-servidor>:8080` e entre com o usuário `admin` e a senha criada. No Fedora,
libere a porta na rede local se precisar:

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

Os agendamentos ficam no volume `smartbulb-data` e continuam lá depois de reinícios e atualizações.

## Acesso remoto

**Não exponha a porta 8080 direto na internet.** Em HTTP puro, a senha trafega em texto puro.
Opções, da mais simples para a mais trabalhosa:

1. **Tailscale** (recomendado): instale no servidor e no celular e acesse
   `http://<nome-do-servidor>:8080` de qualquer lugar, sem abrir portas no roteador.
   `tailscale serve --bg 8080` acrescenta HTTPS em `https://<servidor>.<tailnet>.ts.net`.
2. **WireGuard** no roteador ou no servidor: mesma ideia, totalmente auto-hospedado.
3. **Proxy reverso com HTTPS** (Caddy, Traefik, Nginx) ou **Cloudflare Tunnel**, se quiser um
   domínio público. Use uma senha forte. O login é HTTP Basic, então só sirva a interface por HTTPS.

## API

Todas as rotas, exceto `/healthz`, exigem HTTP Basic.

| Método | Rota | Corpo |
|---|---|---|
| GET | `/api/info` | nenhum (nome e lista de cenas) |
| GET | `/api/status` | nenhum |
| POST | `/api/power` | `{"on": true}` |
| POST | `/api/toggle` | nenhum |
| POST | `/api/color` | `{"color": "#ff8800"}` ou `{"color": "vermelho"}` |
| POST | `/api/brightness` | `{"value": 1-100}` |
| POST | `/api/white` | `{"temperature": 0-100}` (0 = quente) |
| POST | `/api/scene` | `{"scene": "night"}` |
| POST | `/api/timer` | `{"minutes": 30}` (0 cancela) |
| GET | `/api/schedules` | nenhum |
| POST | `/api/schedules` | `{"time": "18:30", "days": [0,1,2,3,4], "action": "on"}` |
| PUT | `/api/schedules/{id}` | igual ao POST |
| DELETE | `/api/schedules/{id}` | nenhum |

`days`: 0 = segunda … 6 = domingo. `action`: `on`, `off`, `color`, `white`, `brightness`, `scene`
(com `value` quando se aplica).

Cenas: `night` (Noite), `read` (Leitura), `work` (Trabalho), `leisure` (Lazer), `soft` (Suave),
`rainbow` (Arco-íris), `shine` (Piscar), `gorgeous` (Deslumbrante). São as cenas de fábrica da
Tuya, gravadas no DPS 25.

Exemplo:

```bash
curl -u admin:SENHA -H 'Content-Type: application/json' -d '{"on":false}' http://servidor:8080/api/power
```

## CLI

Fora do container, a partir da pasta do projeto. Ele lê o `~/devices.json` ou as variáveis
`BULB_ID`/`BULB_IP`/`BULB_KEY`:

```bash
pip install -r requirements.txt
```

```bash
python3 -m app.cli status
```

Comandos: `status`, `on`, `off`, `toggle`, `color <nome|#rrggbb>`, `brightness <1-100>`,
`white [0-100]`, `scene <nome>`, `timer <minutos>`. Os atalhos em português também funcionam:
`cor`, `brilho`, `branco` e `cena` (por exemplo, `cena noite` ou `cor vermelho`).

Dentro do container:

```bash
podman exec smartbulb python -m app.cli cena noite
```

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `BULB_ID`, `BULB_IP`, `BULB_VERSION` | nenhum / `Auto` / `3.5` | dados da lâmpada |
| `BULB_KEY` ou `BULB_KEY_FILE` | nenhum | local key (prefira o arquivo/secret) |
| `BULB_LABEL` | `Smart lamp` | nome exibido na interface |
| `BULB_DEVICES_JSON`, `BULB_NAME` | `~/devices.json`, `Smart color` | alternativa às variáveis acima |
| `APP_USER` | `admin` | usuário da interface |
| `APP_PASSWORD` ou `APP_PASSWORD_FILE` | nenhum | senha (obrigatória) |
| `AUTH_DISABLED` | não definido | `1` desliga o login (só para rede confiável) |
| `TZ` | `America/Sao_Paulo` | fuso horário dos agendamentos |
| `DATA_DIR` | `/data` | onde o `schedules.json` é salvo |

## Observações

- Se a lâmpada for resetada ou pareada de novo, a `local_key` muda, e às vezes o ID também. Rode o
  wizard de novo e recrie o secret `bulb_key`.
- A lâmpada aceita uma conexão por vez, então a API executa os comandos em sequência.

## Licença

[MIT](LICENSE)
