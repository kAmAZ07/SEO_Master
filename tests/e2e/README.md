# Extended E2E

Полный extended E2E поднимает dev-стек, mock WordPress на `localhost:8086` и Tilda adapter в mock-mode.

Запуск из корня проекта:

```powershell
.\tests\e2e\run_extended_e2e.ps1
```

Если образы уже собраны:

```powershell
.\tests\e2e\run_extended_e2e.ps1 -NoBuild
```

Чтобы оставить контейнеры после теста для демонстрации логов и ручной проверки:

```powershell
.\tests\e2e\run_extended_e2e.ps1 -KeepStack
```

Что проверяет сценарий:

- WordPress mock healthcheck и наличие тестового поста `e2e-seo-post`.
- HMAC PATCH через Client API Gateway и фактическое появление meta description в HTML WordPress mock.
- Запись applied deployment log в Client API Gateway.
- Tilda PATCH через Tilda adapter в mock-mode.
- Management Service optimization run.
- HITL endpoint через API Gateway.
- Public quick audit flow до статуса `completed`.
