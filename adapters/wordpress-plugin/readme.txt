=== SEO Master Connector ===
Contributors: seo-master
Requires at least: 6.7
Requires PHP: 8.0
Stable tag: 0.3.0-dev
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Разрабатываемое ограниченное ядро seo-master/v2. Проверенные версии WordPress пока не заявляются.
Маршруты v1 с мутациями закрыты ответом upgrade_required (426).
Протокол, настройка, доверительная граница, команды тестов и незакрытые проверки описаны по-русски в docs/WORDPRESS.md репозитория.
Воспроизводимый ZIP собирается из выбранного Git commit с Ed25519 manifest всех файлов. Неподписанные пакеты не поддерживаются.
До распаковки требуется внешняя проверка ZIP, закреплённый публичный ключ и согласованный SHA-256. Инструкция — PUBLISHING.md.
Для запуска требуется sodium и внешний SEO_MASTER_RELEASE_KEYS_JSON в wp-config.php или окружении PHP; отсутствие доверия блокирует активацию и CMS-команды.
Статус — development. Совместимость WordPress/PHP/theme/Yoast/RankMath и staging E2E требуют отдельной матрицы проверок.
