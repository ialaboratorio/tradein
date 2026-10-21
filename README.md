# TradeIn

Web para que los usuarios registados monten su **lista de venta de cartas** (Magic, Pokémon, Yu-Gi-Oh!, One Piece, Lorcana, Riftbound...) y vean al momento el importe que la tienda les ofrece. El precio de referencia es el **precio más barato de venta en [Cardmarket](https://www.cardmarket.com)** (campo `LOW` de su guía de precios oficial y gratuita), al que se aplican dos coeficientes configurables:

- **Efectivo**: `LOW × 0,70`
- **Dinero de tienda**: `LOW × 0,805` (es decir, `0,70 × 1,15`)

El dueño de la tienda valida cada lista desde su panel cuando recibe las cartas en persona, pudiendo ajustar el precio por línea si alguna carta no está en buen estado, y marcándola como pagada en efectivo o en dinero de tienda.

## Características

- **Acceso con Google** (OAuth 2.0 / OIDC). Registro abierto: cualquier cuenta Google puede entrar.
- **Panel de administración**: listas enviadas, detalle con precios, validación, rechazo con nota, ajuste por línea (override + motivo) y registro de pago (efectivo/tienda).
- **Catálogo local de Cardmarket**: la app descarga a diario los ficheros oficiales gratuitos de [Cardmarket Downloads](https://downloads.s3.cardmarket.com/) para buscar cartas por nombre y conocer su precio `LOW` (oferta activa más barata).
- **Listas por fases**: borrador → enviada → validada/rechazada → pagada, con auditoría de cada cambio.
- **Snapshot de precios**: cada línea guarda el precio y la fecha en que se calculó; si al validar varía más de un umbral configurable, la línea se marca para revisión.
- **Foil**: se usa el campo `low-foil` de la guía.
- **100% server-rendered** (FastAPI + Jinja2), sin frameworks de frontend, responsive.

## Configuración (`.env`)

Crea el archivo a partir de la plantilla:

```bash
cp .env.example .env
```

| Variable | Descripción |
|----------|-------------|
| `SECRET_KEY` | Clave de la cookie de sesión (obligatoria en producción; la app arranca solo si no es un placeholder) |
| `GOOGLE_CLIENT_ID` | Client ID de Google Cloud Console |
| `GOOGLE_CLIENT_SECRET` | Client Secret de Google Cloud Console |
| `ADMIN_EMAILS` | Emails de administrador separados por coma. Vacío = nadie |
| `PUBLIC_BASE_URL` | URL pública de la app (ej. `https://tudominio.com`) para el redirect de Google en producción |
| `SESSION_HTTPS_ONLY` | `true` en producción con HTTPS: la cookie de sesión solo se envía por HTTPS |
| `ENABLE_SCHEDULER` | `false` desactiva el sync automático (usado en tests) |
| `DATABASE_URL` | Por defecto SQLite en `./data/tradein.db` (relativa al CWD; en Docker es `/app/data/tradein.db`) |
| `COEF_CASH` | Coeficiente de compra en efectivo (0,7) |
| `COEF_STORE` | Coeficiente de compra en dinero de tienda (0,805) |
| `SUPPORTED_GAMES` | IDs de juego de Cardmarket: 1=MTG, 6=Pokémon, 3=Yu-Gi-Oh!, 18=One Piece, 19=Lorcana, 22=Riftbound |
| `PRICE_ALERT_PCT` | % de variación desde el snapshot que marca una línea para revisión (10) |
| `PRICE_CACHE_TTL_HOURS` | Horas de vigencia de un precio cacheado (24) |
| `SYNC_TIME` | Hora de la sincronización diaria con Cardmarket, HH:MM (03:30) |
| `SYNC_TIMEZONE` | Zona horaria del scheduler (Europe/Madrid) |
| `PUID` / `PGID` | Usuario que escribe en `./data` (convención TrueNAS) |
| `BIND_IP` | IP de escucha del puerto (0.0.0.0) |

### Configuración de Google Cloud Console

1. Crea un proyecto en [console.cloud.google.com](https://console.cloud.google.com).
2. Ve a [Google Auth platform → Branding](https://console.cloud.google.com/auth/branding): nombre de la app, email de soporte y email de desarrollador.
3. En [Audience](https://console.cloud.google.com/auth/audience): tipo **External**, deja el estado en **Testing** y añade tu email como *Test user*.
4. En **Data Access** añade los scopes `openid`, `email` y `profile`.
5. En [Credentials → Create OAuth client ID](https://console.cloud.google.com/auth/clients/create) → **Web application**, añade las *Authorized redirect URIs*:
   - Desarrollo: `http://localhost:8000/auth/google/callback`
   - Producción: `https://tudominio.com/auth/google/callback`
6. Copia el **Client ID** y el **Client Secret** (solo se muestra una vez) a tu `.env`.

## Ejecución Local

```bash
# Crear entorno virtual
python -m venv .venv
.venv\Scripts\activate     # Windows
source .venv/bin/activate  # Linux/Mac

# Instalar dependencias
pip install -r requirements.txt

# Configurar variables de entorno
cp .env.example .env

# Arrancar
uvicorn app.main:app --reload --port 8000
```

Abre <http://localhost:8000>. El endpoint de salud es `GET /health`.

## Ejecución con Docker

```bash
cp .env.example .env      # y rellenar
docker compose up -d --build
```

- La app queda en el puerto **8002** (configurable con `BIND_IP`).
- Los datos (SQLite) se persisten en `./data`.
- `restart: unless-stopped`. Imagen publicada en `ghcr.io/ialaboratorio/tradein:latest`.

## Estructura

```
app/
├── main.py          # FastAPI, sesiones, lifespan (sync diario), /health
├── config.py        # Configuración desde .env (pydantic-settings)
├── db.py            # SQLAlchemy 2 + SQLite WAL
├── models.py        # Usuarios, catálogo, precios, listas, eventos
├── routers/         # auth, cards (búsqueda), lists, admin
├── services/        # cardmarket (S3), sync (scheduler), pricing (coeficientes)
└── templates/       # Jinja2
alembic/             # Migraciones (0001 users, 0002 catálogo, 0003 listas)
tests/               # pytest (22 tests)
```

## Sincronización de precios

- Automática: al arrancar (si los datos tienen más de 24 h) y cada día a las 03:30 CET.
- Manual: `python -m app.services.sync` (descarga catálogo + precios de los juegos en `SUPPORTED_GAMES`).
- Estado de las últimas sincronizaciones: panel **Admin**.

## Notas sobre los precios

- Fuente: ficheros oficiales y gratuitos de Cardmarket (`price_guide` y `products_singles`), actualizados una vez al día (~02:45 CET). La app sincroniza a las 03:30.
- El campo `LOW` es la oferta activa más barata (mezcla idiomas y condiciones); el precio final de compra lo decide la tienda al validar en persona.
- **Atribución**: "Precios: Cardmarket". Este proyecto no está afiliado a Cardmarket.

## Roadmap

- [x] Fase 1: scaffold, Docker, CI
- [x] Fase 2: acceso con Google + roles
- [x] Fase 3: sincronización Cardmarket + búsqueda de cartas
- [x] Fase 4: listas de venta con coeficientes
- [x] Fase 5: panel de administración
- [x] Fase 6: tests y despliegue
