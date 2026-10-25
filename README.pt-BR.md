# SmartBulb

[English](README.md) · **Português (BR)**

Controle auto-hospedado e sem nuvem de lâmpadas Wi-Fi Tuya (testado com a Elgin "Smart lamp",
protocolo 3.5). Sem app proprietário: interface web responsiva (desktop e celular), cenas, rotinas,
agendamentos, vários usuários, API REST, CLI e integração com o Home Assistant, todos falando com a
lâmpada direto pela rede local.

```
smartbulb/
├── app/
│   ├── bulb.py          # controle da lâmpada (tinytuya, protocolo local) e cenas
│   ├── devices.py       # várias lâmpadas, grupos, cache de estado, desfazer e estatísticas
│   ├── effects.py       # rotinas: transições, pomodoro, alerta, ritmo circadiano, presença
│   ├── schedules.py     # agendamentos (horário fixo ou nascer/pôr do sol)
│   ├── library.py       # favoritos e cenas criadas pelos usuários
│   ├── auth.py          # usuários, sessões e tokens de API
│   ├── activity.py      # histórico de quem fez o quê
│   ├── sun.py           # nascer e pôr do sol a partir da latitude/longitude
│   ├── mqtt.py          # ponte MQTT com descoberta automática no Home Assistant
│   ├── events.py        # atualizações ao vivo (Server-Sent Events)
│   ├── main.py          # API FastAPI
│   ├── cli.py           # cliente de linha de comando
│   └── static/          # interface web e página de login
├── Containerfile
└── deploy/smartbulb.container   # unidade Podman Quadlet (systemd)
```

## Funcionalidades

**Controle**
- Liga/desliga, temperatura do branco e brilho
- Cor por cores prontas ou por um seletor próprio: quadro de saturação/intensidade, barra de matiz
  e campos HEX e RGB
- 26 cenas em três grupos: **Trabalho** (Foco profundo, Estudo, Leitura, Videochamada, Criatividade,
  Trabalho à noite…), **Relaxar** (Pôr do sol, Vela, Lareira, Oceano, Floresta, Aurora, Meditação,
  Spa, Luar, Cinema, Jantar…) e **Diversão**
- Crie suas próprias cenas (até 8 cores, transição fixa, em salto ou gradiente, velocidade e brilho)
  e teste na lâmpada antes de salvar
- Favoritos: salve a luz atual e aplique de novo com um toque
- Desfazer a última mudança
- Feedback em todos os botões: indicador de carregamento, confirmação e mensagem de erro

**Rotinas**
- **Ritmo circadiano**: tom e brilho acompanham o dia (frio e forte de manhã, quente e fraco à
  noite), calculado a partir do nascer e do pôr do sol
- **Despertar/Adormecer**: nascer do sol gradual ou escurecer até apagar, em 5 a 60 minutos
- **Pomodoro**: luz fria para focar e uma cor suave nas pausas, com piscadas na troca
- **Simulação de presença**: acende e apaga ao acaso do pôr do sol até 23:30
- **Alerta** (`/api/effects/flash`): pisca uma cor e volta ao que estava, útil para campainha,
  build que falhou etc.
- Timer para desligar

**Agendamentos**
- Por dia da semana, em horário fixo ou relativo ao nascer/pôr do sol (por exemplo, "20 min antes
  do pôr do sol")
- Ações: ligar, desligar, cor, branco, brilho, cena, favorito, despertar, adormecer e ritmo circadiano

**Casa e usuários**
- Página de login própria, com sessão por cookie
- Vários usuários com papéis: **administrador** (tudo, inclusive usuários e grupos), **usuário**
  (controla tudo) e **convidado** (só liga e desliga)
- O administrador cria usuários, troca papéis e redefine senhas; cada um troca a própria senha
- Tokens de API por usuário para a CLI, scripts, Atalhos do iPhone e Home Assistant
- Histórico de atividade (quem mudou o quê e quando)
- Várias lâmpadas e grupos (por exemplo, "Sala" com duas lâmpadas)
- Estatísticas de uso: horas acesas por dia e estimativa de consumo em kWh
- Atualização ao vivo: se alguém muda a luz, todas as telas abertas mostram na hora
- MQTT com descoberta automática no Home Assistant (e, por ele, Alexa e Google Assistente)
- Tema claro e escuro; pode ser adicionado à tela inicial do celular
- Segredos via podman secrets, container rootless e somente leitura

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

## Experimentar sem lâmpada

O modo demonstração simula lâmpadas em memória:

```bash
pip install -r requirements.txt
```

```bash
BULB_DEMO=2 DATA_DIR=data APP_PASSWORD=troque-esta-senha uvicorn app.main:app --port 8080
```

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
read -rsp 'Senha do primeiro administrador: ' PW && printf %s "$PW" | podman secret create app_password - && unset PW
```

Configuração e serviço:

```bash
mkdir -p ~/.config/smartbulb ~/.config/containers/systemd
cp .env.example ~/.config/smartbulb/smartbulb.env      # revise BULB_ID / BULB_IP / LATITUDE / LONGITUDE
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

Usuários, agendamentos, favoritos, grupos, histórico e estatísticas ficam no volume
`smartbulb-data` e continuam lá depois de reinícios e atualizações.

### Usuários

`APP_USER`/`APP_PASSWORD` só criam o **primeiro** administrador, quando ainda não existe nenhum
usuário. Depois disso, os usuários vivem em `/data/users.json` (senhas com hash scrypt) e são
gerenciados em **Administração** no menu do avatar. Trocar o secret `app_password` depois não muda
a senha de ninguém.

### Várias lâmpadas

Aponte `BULBS_FILE` para um JSON no formato do `devices.json` do wizard, só com as lâmpadas (o
arquivo tem as chaves, então monte-o como secret):

```json
[
  {"id": "...", "name": "Sala", "ip": "192.168.1.20", "key": "...", "version": "3.5", "watts": 9},
  {"id": "...", "name": "Quarto", "ip": "192.168.1.21", "key": "...", "version": "3.5"}
]
```

```bash
podman secret create bulbs ./bulbs.json
```

Descomente as linhas de `BULBS_FILE` em `deploy/smartbulb.container`. Os grupos são criados na
interface, em **Administração → Grupos**.

### Home Assistant (MQTT)

Defina `MQTT_HOST` (e `MQTT_USER`/`MQTT_PASSWORD` se o broker pedir). Cada lâmpada aparece sozinha
no Home Assistant como uma luz com brilho, temperatura, cor e as cenas como efeitos. A partir daí,
Alexa e Google Assistente funcionam pela integração do Home Assistant.

## Acesso remoto

**Não exponha a porta 8080 direto na internet.** Em HTTP puro, a senha trafega em texto puro.
Opções, da mais simples para a mais trabalhosa:

1. **Tailscale** (recomendado): instale no servidor e no celular e acesse
   `http://<nome-do-servidor>:8080` de qualquer lugar, sem abrir portas no roteador.
   `tailscale serve --bg 8080` acrescenta HTTPS em `https://<servidor>.<tailnet>.ts.net`.
2. **WireGuard** no roteador ou no servidor: mesma ideia, totalmente auto-hospedado.
3. **Proxy reverso com HTTPS** (Caddy, Traefik, Nginx) ou **Cloudflare Tunnel**, se quiser um
   domínio público. Use senhas fortes e só sirva a interface por HTTPS.

Atrás de HTTPS, o cookie de sessão é marcado como `Secure` automaticamente. "Usar minha
localização" em Administração também precisa de HTTPS.

## API

Todas as rotas, exceto `/healthz` e `/api/login`, exigem autenticação: o cookie de sessão da
interface, um token (`Authorization: Bearer sb_...`, criado em **Minha conta**) ou HTTP Basic com
usuário e senha.

As rotas de controle aceitam `?target=` com o id de uma lâmpada, de um grupo ou `all`. Sem ele, vale
a primeira lâmpada.

| Método | Rota | Corpo |
|---|---|---|
| GET | `/api/info` | nenhum (lâmpadas, grupos, cenas, favoritos, sol) |
| GET | `/api/status` | nenhum |
| GET | `/api/events` | nenhum (stream de eventos ao vivo) |
| POST | `/api/power` | `{"on": true}` |
| POST | `/api/toggle` | nenhum |
| POST | `/api/color` | `{"color": "#ff8800", "brightness": 60}` (`brightness` é opcional) |
| POST | `/api/brightness` | `{"value": 1-100}` |
| POST | `/api/white` | `{"temperature": 0-100, "brightness": 1-100}` (0 = quente; ambos opcionais) |
| POST | `/api/scene` | `{"scene": "focus"}` |
| POST | `/api/favorite` | `{"favorite": "<id>"}` |
| POST | `/api/timer` | `{"minutes": 30}` (0 cancela) |
| POST | `/api/undo` | nenhum |
| POST | `/api/effects/fade` | `{"direction": "wake" ou "sleep", "minutes": 15}` |
| POST | `/api/effects/pomodoro` | `{"work": 25, "rest": 5, "cycles": 4}` |
| POST | `/api/effects/flash` | `{"color": "#ff0000", "times": 3}` |
| DELETE | `/api/effects` | nenhum (para a rotina e restaura a luz) |
| PUT | `/api/circadian` | `{"enabled": true}` |
| PUT | `/api/presence` | `{"enabled": true, "target": "<id>"}` |
| POST | `/api/favorites` | `{"name": "Leitura"}` (salva a luz atual) |
| POST | `/api/scenes` | `{"name": "...", "colors": ["#ff0000"], "transition": "fade", "speed": 50, "brightness": 80}` |
| GET/POST | `/api/schedules` | `{"trigger": "sunset", "offset": -20, "days": [0,1,2,3,4], "action": "on", "target": null}` |
| PUT/DELETE | `/api/schedules/{id}` | igual ao POST / nenhum |
| GET | `/api/activity`, `/api/stats` | nenhum |
| GET/POST/PATCH/DELETE | `/api/users`, `/api/groups` | só administrador |

`trigger`: `time` (usa `time`, "HH:MM"), `sunrise` ou `sunset` (usa `offset` em minutos). `days`:
0 = segunda … 6 = domingo. `action`: `on`, `off`, `color`, `white`, `brightness`, `scene`,
`favorite`, `wake`, `sleep`, `circadian` (com `value` quando se aplica).

Exemplos:

```bash
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"on":false}' http://servidor:8080/api/power
```

```bash
curl -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"color":"#ff0000"}' http://servidor:8080/api/effects/flash
```

## CLI

Com `SMARTBULB_URL` e `SMARTBULB_TOKEN`, a CLI usa o servidor, então rotinas, histórico e
atualização ao vivo funcionam. Sem eles, fala direto com a lâmpada (lê o `~/devices.json` ou
`BULB_ID`/`BULB_IP`/`BULB_KEY`).

```bash
pip install -r requirements.txt
```

```bash
export SMARTBULB_URL=http://servidor:8080 SMARTBULB_TOKEN=sb_...
```

```bash
python3 -m app.cli --target sala cena oceano
```

Comandos: `status`, `on`, `off`, `toggle`, `color <nome|#rrggbb>`, `brightness <1-100>`,
`white [0-100]`, `scene <nome>`, `scenes`, `timer <minutos>` e, só pelo servidor, `favorite <id>`,
`undo`, `flash [#cor] [vezes]`, `wake [min]`, `sleep [min]`, `pomodoro [foco] [pausa] [ciclos]` e
`stop`. Os atalhos em português também funcionam: `ligar`, `desligar`, `cor`, `brilho`, `branco` e
`cena` (por exemplo, `cena vela` ou `cor vermelho`).

Dentro do container:

```bash
podman exec smartbulb python -m app.cli cena noite
```

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|---|---|---|
| `BULB_ID`, `BULB_IP`, `BULB_VERSION` | nenhum / `Auto` / `3.5` | dados da lâmpada |
| `BULB_KEY` ou `BULB_KEY_FILE` | nenhum | local key (prefira o arquivo/secret) |
| `BULB_LABEL` | `Smart lamp` | nome da lâmpada na interface |
| `BULB_WATTS` | `9` | potência, para estimar o consumo |
| `BULB_DEVICES_JSON`, `BULB_NAME` | `~/devices.json`, `Smart color` | alternativa às variáveis acima |
| `BULBS_FILE` | nenhum | JSON com várias lâmpadas (substitui as variáveis acima) |
| `BULB_DEMO` | não definido | `N` simula N lâmpadas, para testar sem hardware |
| `APP_USER` | `admin` | nome do primeiro administrador |
| `APP_PASSWORD` ou `APP_PASSWORD_FILE` | nenhum | senha do primeiro administrador |
| `AUTH_DISABLED` | não definido | `1` desliga o login (só para rede confiável) |
| `LATITUDE`, `LONGITUDE` | nenhum | localização para nascer/pôr do sol (também editável na interface) |
| `TZ` | `America/Sao_Paulo` | fuso horário |
| `POLL_SECONDS` | `5` | intervalo de leitura do estado da lâmpada |
| `MQTT_HOST`, `MQTT_PORT` | nenhum, `1883` | broker MQTT (ativa a integração) |
| `MQTT_USER`, `MQTT_PASSWORD` ou `MQTT_PASSWORD_FILE` | nenhum | credenciais do broker |
| `MQTT_PREFIX`, `MQTT_DISCOVERY_PREFIX` | `smartbulb`, `homeassistant` | tópicos |
| `DATA_DIR` | `/data` | onde os dados são salvos |

## Observações

- Se a lâmpada for resetada ou pareada de novo, a `local_key` muda, e às vezes o ID também. Rode o
  wizard de novo e recrie o secret `bulb_key`.
- A lâmpada aceita uma conexão por vez, então os comandos para cada lâmpada são executados em
  sequência.
- As cenas novas foram montadas com o formato de cena da Tuya (DPS 25). A velocidade das transições
  pode variar de um modelo para outro. Se as cenas animadas ficarem rápidas ou lentas demais, ajuste
  `SLOW`/`MEDIUM`/`FAST` em `app/bulb.py`.

## Licença

[MIT](LICENSE)
