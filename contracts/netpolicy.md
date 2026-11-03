# Контракт J-6: SafeHttpClient

Версия контракта: 1. Реализация: `shared/netpolicy.py`. Тесты: `tests/unit/test_netpolicy.py`.

Все запросы по адресам, на которые влияет пользователь, выполняются только через этот клиент: обход, robots.txt, sitemap, проверка ссылок, подключение CMS, независимая проверка публикации. Прямые `httpx.get`, `requests`, `urllib` и Playwright `page.goto` для таких адресов запрещены.

## Интерфейс

```python
from shared.netpolicy import NetPolicy, NetPolicyError, SafeHttpClient

client = SafeHttpClient()                      # политика по умолчанию
result = await client.fetch(url)               # GET
result = await client.fetch(url, method="HEAD")
result = await client.fetch(url, headers={"Accept": "text/html"},
                            policy=NetPolicy(max_body_bytes=5 * 1024 * 1024))
```

`FetchResult`: `url`, `final_url`, `status`, `headers` (ключи в нижнем регистре), `body` (bytes, уже распакован), `redirects` (список адресов), `peer_ip`, `elapsed`.

Допустимы только `GET` и `HEAD`. Заголовки `Host`, `Connection`, `Accept-Encoding`, `Content-Length` вызывающий задать не может.

## Политика по умолчанию

| Параметр | Значение |
|---|---|
| схемы | http, https |
| порты | 80, 443 |
| перенаправления | не более 5, каждое проверяется заново |
| таймаут соединения / чтения / всего запроса | 3 / 10 / 30 секунд |
| размер тела (после распаковки) | 2 MiB |
| длина адреса | до 2048 символов |

Лимиты меняются через `NetPolicy(...)` на один вызов. Значения для публичного аудита (HTML 2 MiB, sitemap 5 MiB, общий дедлайн 120 секунд) задаёт вызывающий код.

## Что считается отказом

Клиент выбрасывает `NetPolicyError` с полем `code`. Свойство `is_policy_block` истинно для отказов политики, `retryable` истинно для сбоев, которые имеет смысл повторить.

Отказы политики (`is_policy_block = True`): `invalid_url`, `scheme_not_allowed`, `port_not_allowed`, `userinfo_not_allowed`, `host_blocked`, `dns_failure`, `address_blocked`, `redirect_limit`, `redirect_loop`, `redirect_invalid`, `response_too_large`, `method_not_allowed`.

Сбои передачи: `timeout` и `connect_error` (повторяемые), `tls_error`, `protocol_error`, `unsupported_encoding`.

Правила, которые стоит знать вызывающему:

- Сбой DNS означает отказ (`dns_failure`), а не разрешение.
- Если хотя бы один адрес узла непубличный, отказ получает весь узел (`address_blocked`).
- Адреса IPv4-mapped, 6to4, Teredo и NAT64 проверяются как вложенный IPv4.
- Числовые формы вроде `2130706433` и `0x7f.1`, а также `localhost`, `*.local`, `*.internal`, `*.localhost` отклоняются (`host_blocked`).
- Имя разрешается один раз в момент соединения, соединение идёт на проверенный IP, TLS проверяет имя исходного узла.
- Сжатые ответы gzip и deflate распаковываются с ограничением по размеру. Другие кодировки дают `unsupported_encoding`.
- Ответ 3xx без заголовка Location возвращается как есть. С Location клиент идёт по цепочке сам.

## Тестовый профиль

Приватные адреса допускаются только для точных имён из `NetPolicy(allow_private_hosts=...)` и только при `NETPOLICY_PROFILE=test`. Иначе конструктор политики выбрасывает `ValueError`. Общего флага «разрешить приватные адреса» нет и не будет.

## Заглушка для работы до подключения клиента

Пока код, который вы пишете, не может импортировать `shared.netpolicy`, используйте тонкую обёртку с тем же интерфейсом и заменяйте её одной строкой импорта:

```python
class FetchResult(SimpleNamespace): ...
async def fetch(url, *, method="GET", headers=None, policy=None):
    async with httpx.AsyncClient(follow_redirects=False, timeout=10) as c:
        r = await c.request(method, url, headers=headers)
        return FetchResult(url=url, final_url=str(r.url), status=r.status_code,
                           headers={k.lower(): v for k, v in r.headers.items()},
                           body=r.content, redirects=[], peer_ip=None, elapsed=0.0)
```

Заглушка не защищает от SSRF и годится только для разработки.

## Установка в образах

`shared/` копируется в образ audit_service. Для остальных сервисов, которые будут вызывать клиент (client_api_gateway для проверки публикации), строку `COPY shared/ ./shared/` нужно добавить в их Dockerfile. Зависимость: `httpcore` (приходит вместе с `httpx`), проверено на `httpcore 1.0.2` и `1.0.9`.
