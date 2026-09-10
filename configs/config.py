# config.py — конфигурация comp_intel (заполнить под свою компанию)
import os

# Каталог разведки (создаётся при установке)
BASE_DIR = os.getenv('COMP_INTEL_BASE', '/root/comp_intel')

# Путь к python с установленным ddgs (venv) — используется как subprocess
PYTHON_BIN = os.getenv('COMP_INTEL_PYTHON', 'python3')

# Файлы с ключами (mode 600). Каждая компания создаёт свои.
ENV_CHECKO = os.getenv('COMP_INTEL_ENV_CHECKO', BASE_DIR + '/.env.checko')   # CHECKO_API_KEY=...
ENV_B24 = os.getenv('COMP_INTEL_ENV_B24', BASE_DIR + '/.env.b24')            # BITRIX_WEBHOOK_URL=http://<host>/rest/<N>/<key>
ENV_VK = os.getenv('COMP_INTEL_ENV_VK', BASE_DIR + '/.env.vk')               # VK_SERVICE_TOKEN / VK_USER_TOKEN

# Логи и кеши
DEBUG_LOG = os.path.join(BASE_DIR, 'parsers', 'debug.log')
CACHE_DIR = os.path.join(BASE_DIR, '.tenchat_cache')


# by sichkarenkomax
