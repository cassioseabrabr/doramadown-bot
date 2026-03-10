# DoramaDown Bot — Deploy no Render

## Arquivos necessários
- dorama_bot.py
- requirements.txt

## Passo a passo

### 1. Criar o Bot no Telegram
1. Abra o Telegram e busque @BotFather
2. Mande /newbot
3. Escolha nome: DoramaDown
4. Escolha username: doramadown_seu_bot
5. Copie o TOKEN gerado

### 2. Pegar seu ID do Telegram
1. Busque @userinfobot no Telegram
2. Mande qualquer mensagem
3. Copie seu ID (número)

### 3. Criar conta no GitHub
1. Acesse github.com
2. Crie uma conta gratuita
3. Clique em "New repository"
4. Nome: doramadown-bot
5. Clique "Create repository"
6. Faça upload dos 2 arquivos (dorama_bot.py e requirements.txt)

### 4. Deploy no Render
1. Acesse render.com
2. Crie conta gratuita (pode usar o GitHub)
3. Clique "New +" → "Web Service"
4. Conecte seu repositório do GitHub
5. Configure:
   - Name: doramadown-bot
   - Runtime: Python 3
   - Build Command: pip install -r requirements.txt
   - Start Command: python dorama_bot.py

### 5. Variáveis de ambiente (IMPORTANTE!)
No Render, vá em "Environment" e adicione:

| Variável      | Valor                          |
|---------------|--------------------------------|
| BOT_TOKEN     | Token do BotFather             |
| API_ID        | 2040                           |
| API_HASH      | b18441a1ff607e10a989891a5462e627 |
| CHAVE_SECRETA | KWAIBOOST_DORAMA_2026_X9Z      |
| ADMIN_ID      | Seu ID do Telegram             |

### 6. Deploy!
Clique em "Create Web Service" e aguarde 2-3 minutos.

## Comandos do bot

### Para clientes:
- /start — Início e status
- /status — Ver downloads restantes
- /serial NOME | KWAI-XXXX-XXXX-XXXX — Ativar serial PRO

### Para você (admin):
- /admin — Ver estatísticas
- /gerar NOME DO CLIENTE — Gera serial e mensagem pronta

## Como vender
1. Cliente acessa o bot
2. Usa 5 downloads grátis
3. Bot bloqueia e mostra botão "Ativar PRO"
4. Cliente te contata
5. Você usa /gerar NOME DO CLIENTE no bot
6. Bot gera o serial e a mensagem pronta
7. Você copia e manda para o cliente
8. Cliente ativa com /serial
9. 30 dias de acesso ilimitado!
