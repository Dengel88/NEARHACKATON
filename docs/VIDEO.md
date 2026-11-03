# AI-Штат Pay — презентационное видео

Цель: 90 секунд для поста в X (@NEARProtocol, #NEARHackathon) и для судей. Судьи англоязычные,
поэтому озвучка и титры на английском; режиссёрские пометки по-русски.

- Формат: 16:9, 1920×1080, 30 fps. Отдельно вертикальный 9:16 нарезкой из тех же сцен (30 сек).
- Длина: 85–95 сек. В X лучше до 2:20, судьям — до 2 минут, укладываемся.
- Голос: уверенный мужской/женский EN, темп ~150 слов/мин. Музыка: электронный пульс, 110–120 BPM, дроп на оплате.
- Палитра: тёмный фон, акцент NEAR-зелёный (#00EC97) + белый. Шрифт титров: Inter / Space Grotesk.
- Главный «вау»: реальная оплата $1 «кофе» с другой сети и USDC, падающий на `wanderlust.near` (видно в NEAR-эксплорере).

---

## Сценарий и раскадровка

| # | Время | Кадр (что на экране) | Озвучка (EN) | Титры / графика | Источник |
|---|---|---|---|---|---|
| 1 | 0:00–0:05 | Хук. Чёрный экран, телефон с чатом: клиент пишет «Can I pay in SOL?». Ответ бота мгновенно, с кнопкой «Pay». | «Your customer wants to pay in SOL. Your business wants dollars.» | — | Запись экрана чата |
| 2 | 0:05–0:15 | Проблема. Сплит-скрин: слева хаос из логотипов (ETH, SOL, BTC, Tron, Base, Arbitrum), справа владелец малого бизнеса с калькулятором. Логотипы сыплются, как спам. | «Ten chains, a hundred tokens, bridges, swaps, slippage. Small businesses just give up and lose the sale.» | «Every chain is a different checkout» | Моушн (ИИ-генерация / Higgsfield) |
| 3 | 0:15–0:20 | Название. Логотипы втягиваются в одну точку, вспышка, логотип AI-Штат Pay. | «Meet AI-Shtat Pay.» | **AI-Shtat Pay** · AI sales assistant + NEAR Intents | Моушн |
| 4 | 0:20–0:35 | Демо чата. Сайт студии, клиент пишет по-русски/испански, ассистент отвечает на его языке, советует товар, выставляет счёт прямо в чате. Крупно: карточка счёта с итогом. | «An AI assistant sells on your site in the customer's language. When they're ready, it issues an invoice. Prices come from the merchant's catalog, never from the model.» | «Any language» · «Invoice in chat» · «Prices enforced in code» | Запись экрана (live, Gemini) |
| 5 | 0:35–0:50 | Оплата. Клиент выбирает токен (SOL на Solana), вводит адрес возврата, получает адрес депозита и точную сумму. Телефон с кошельком: отправка. | «The customer pays with whatever they hold: ETH, SOL, USDC on any L2, NEAR. One click, one deposit address.» | «10 tokens · 6 chains» | Запись экрана + кошелёк на телефоне |
| 6 | 0:50–1:00 | Магия. Анимированная схема: монета SOL летит в «NEAR Intents», солверы, на выходе USDC на `wanderlust.near`. Синхронно — статус в чате PENDING → PROCESSING → **PAID**. Дроп в музыке. | «NEAR Intents routes it. The merchant receives exactly the invoice amount, in USDC, on their NEAR account. No bridges, no swaps, no slippage for the merchant.» | «EXACT_OUTPUT: merchant gets exactly $1.00» | Моушн-схема + запись экрана |
| 7 | 1:00–1:08 | Доказательство. NearBlocks: входящий перевод USDC на `wanderlust.near`, крупно сумма и время. | «That's a real mainnet payment. One coffee, one dollar.» | «Real mainnet tx» + ссылка | Скрин эксплорера |
| 8 | 1:08–1:18 | Человек в петле. Панель менеджера: список диалогов, статусы оплат, менеджер перехватывает чат, клиент видит ответ живого человека. | «When the AI isn't enough, a manager takes over the same chat from the merchant desk.» | «Human handoff» | Запись экрана `/merchant` |
| 9 | 1:18–1:26 | Технология. Быстрая сетка плашек с иконками. | «Built on NEAR Intents 1Click, Gemini with tool calling, ready for NEAR AI Cloud private inference. Runs offline in demo mode with zero keys.» | NEAR Intents · 1Click API · Gemini · NEAR AI Cloud · FastAPI | Моушн |
| 10 | 1:26–1:32 | Призыв. Логотип, URL демо, GitHub, теги. | «AI-Shtat Pay. Sell in any language. Get paid from any chain.» | URL демо · github.com/Dengel88/NEARHACKATON · Built at NEAR HACKATHON · @NEARProtocol #NEARHackathon | Финальная плашка |

Итого озвучки ~170 слов, ≈ 70 сек речи + паузы на музыку.

---

## Что нужно снять

1. **Чат (сцены 1, 4, 5, 6)** — запись экрана живого режима (Gemini + 1Click mainnet) на задеплоенном домене. Браузер в чистом профиле, масштаб 125%, курсор крупный.
2. **Реальная оплата $1** — нужен кошелёк с ~$2 в SOL (Phantom) или USDC на Base. Записать телефон/экран кошелька во время отправки. **Требует согласия Сэма: это реальные деньги.**
3. **NearBlocks** — страница `wanderlust.near` с входящим USDC после оплаты.
4. **Панель менеджера** — `/merchant`, перехват чата.
5. **Моушн (сцены 2, 3, 6, 9, 10)** — ИИ-генерация (Higgsfield / Gemini-пайплайн) или код-анимация (HTML/GSAP → MP4). Схему «монета → Intents → USDC» лучше кодом: точнее и без артефактов.

## Пост в X (черновик)

> Built at NEAR HACKATHON: AI-Shtat Pay 🧾⚡
> An AI sales assistant that sells in chat and takes payment in any token on any chain.
> The merchant always gets exact USDC on NEAR, routed by NEAR Intents.
> Real $1 coffee, paid in SOL, settled on NEAR 👇
> @NEARProtocol #NEARHackathon
> [видео] [ссылка на демо] [github]

## Домен

Вариант по умолчанию: деплой на Vercel (`vercel.json` уже есть) и поддомен **pay.synndicate.ru** через CNAME на Vercel — бесплатно и связывает с брендом AI-Штат. Отдельный новый домен (например `aishtatpay.xyz`) стоит денег — только по согласию.
