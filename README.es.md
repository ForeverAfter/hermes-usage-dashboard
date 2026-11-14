# AI Usage para Hermes

[![CI](https://github.com/ForeverAfter/hermes-usage-dashboard/actions/workflows/ci.yml/badge.svg)](https://github.com/ForeverAfter/hermes-usage-dashboard/actions/workflows/ci.yml)
[![Licencia: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

Mira cuánto te queda de tus suscripciones de **Codex, Claude Code, Kimi, Z.ai y Nous** desde la barra de estado de Hermes Desktop, y deja que tus agentes pregunten lo mismo. English version: [README.md](README.md).

<!-- captura: docs/images/status-chip.png (el chip "AI 64%" de la barra de estado y el popover abierto) -->

- **Sigue tu sesión.** El chip muestra la cuota del proveedor que la sesión enfocada está usando de verdad, o un proveedor que fijes (una sola elección para toda la app). Los topes semanales de Opus/Sonnet nunca se hacen pasar por la cuota de tu cuenta.
- **No miente cuando un proveedor falla.** Un valor viejo lleva la insignia `STALE`, uno local `LOCAL` y uno ausente `NO DATA`; una caché muere en cuanto se reinicia su ventana. La sesión de Claude al 1 % se ve como 1 %, no como 100 %.
- **Los agentes pueden preguntar.** La herramienta `usage_availability` y el comando `/usage-availability` dicen qué suscripción aún tiene cuota y cuándo vuelve una agotada. Alertas al 80 % y al 95 % de uso.

## Instalación

```bash
hermes plugins install ForeverAfter/hermes-usage-dashboard --enable
```

Eso es todo: Hermes Desktop carga el chip desde el plugin instalado (si no aparece, ejecuta **Reload desktop plugins** en la paleta de comandos). Requiere Hermes 0.22.5 o superior. Verifica con `hermes plugins doctor usage-dashboard`.

Kimi y Z.ai usan las llaves que Hermes ya tiene (`KIMI_CODING_API_KEY` o `KIMI_API_KEY`, primero la de Kimi Coding; `GLM_API_KEY` o `ZAI_API_KEY`). Codex, Claude y Nous usan la sesión que ya iniciaste en Hermes (`hermes auth`).

## Qué lee / qué envía

Todo corre en tu equipo. No hay telemetría, analítica ni registro remoto, ni autoactualización.

| Proveedor | Qué lee el plugin | Adónde sale una petición |
| --- | --- | --- |
| OpenAI Codex | El lector de uso del propio Hermes (solo lectura; el plugin nunca ve tu sesión de ChatGPT) | La hace Hermes al servicio de ChatGPT |
| Claude Code | El token OAuth que Hermes resuelve para Anthropic, en memoria, para una sola petición | `api.anthropic.com/api/oauth/usage` (token Bearer, `User-Agent: hermes-usage-dashboard/<versión>`) |
| Kimi | `KIMI_API_KEY` / `KIMI_CODING_API_KEY` del ámbito de secretos del perfil | `api.kimi.com/coding/v1/usages` |
| Z.ai | `GLM_API_KEY` / `ZAI_API_KEY` / `Z_AI_API_KEY` del ámbito de secretos del perfil | `api.z.ai/api/monitor/usage/quota/limit` y `api.z.ai/api/biz/subscription/list` |
| Nous | El ayudante de cuenta del Portal de Nous de Hermes | La hace Hermes al Portal de Nous |
| Registro de Kimi Desktop | Solo si activas `kimi_desktop_log_fallback` (apagado por omisión): hasta 2 MB del registro propio de Kimi Desktop | Nada |

Las credenciales nunca llegan a la interfaz, a los registros, a `availability.json` ni a ninguna caché. **No** lee el archivo de cuenta del CLI de Claude, ni `.env`, ni `auth.json`, ni credenciales de otra herramienta.

Solo escribe en sus propios datos (`plugin-data/usage-dashboard`: `availability.json` y una caché de uso de Claude) y en el almacenamiento de plugins de Desktop (los últimos valores buenos y la memoria de alertas por perfil y conexión, más el proveedor fijado, que es para toda la app). Detalles: [PRIVACY.md](PRIVACY.md), [SECURITY.md](SECURITY.md).

## Para agentes y otros plugins

`availability.json` se publica tras cada recolección, solo con hechos:

```json
{"schema": 1, "generated_at": "2026-11-06T07:52:04Z",
 "providers": {"anthropic": {"state": "ok", "remaining_percent": 77, "window": "session",
                             "reset_at": "2026-11-06T11:50:00Z", "stale": false}}}
```

Los estados son `ok`, `low` (15 % o menos restante por omisión), `exhausted` (1 % o menos) y `unknown` (sin dato o con más de 15 minutos). Las claves de proveedor son ids de proveedor de Hermes. Léelo en solo lectura y trata un archivo ausente o viejo como `unknown`, nunca como un bloqueo. Se actualiza mientras Hermes Desktop está abierto.

## Ajustes

**Settings → Plugins → AI Usage**: umbrales de alerta de aviso y crítica, umbral de cuota baja y el registro opcional de Kimi Desktop.

## Uso

1. Haz clic en el chip **AI** de la barra de estado para ver cada proveedor, ventana, plan y hora de reinicio.
2. Déjalo en **Automatic** para seguir la sesión enfocada, o haz clic en cualquier porcentaje para fijar su proveedor (el chip muestra entonces su ventana agotada o la menor entre sesión y semana; la elección es para toda la app). **Follow session** regresa al modo automático.
3. **Refresh** está protegido por caché para no provocar límites de tasa de los proveedores.

Consulta la [Guía de usuario](docs/USER_GUIDE.md) (en inglés) y las [Notas de proveedores](docs/PROVIDER_NOTES.md).

> [!IMPORTANT]
> Plugin de la comunidad, sin afiliación ni respaldo de OpenAI, Anthropic, Moonshot AI, Z.ai, Nous Research ni del proyecto Hermes Agent. El endpoint de uso de Anthropic se lee con un analizador propio solo porque el núcleo de Hermes aún interpreta mal los valores de 1 % o menos (upstream #58226); pasará al lector del núcleo cuando se corrija.

## Desarrollo

```bash
python -m pip install -r requirements-dev.txt
python scripts/security_scan.py
python -m pytest
python -m ruff check .
node --check desktop/plugin.js
```

Arquitectura: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Contribuciones: [CONTRIBUTING.md](CONTRIBUTING.md). Cambios: [CHANGELOG.md](CHANGELOG.md).

## Desinstalación

```bash
hermes plugins remove usage-dashboard
```

`scripts/legacy/` conserva el antiguo copiador de la interfaz para Hermes anterior a 0.22; no lo uses en versiones compatibles.

## Licencia

MIT — ver [LICENSE](LICENSE).
