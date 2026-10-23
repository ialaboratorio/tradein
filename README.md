# TradeIn

Web para que los usuarios registados monten su **lista de venta de cartas** (Magic, Pokémon, One Piece y Gundam) y vean al momento el importe que la tienda les ofrece. El precio de referencia es el **precio más barato de venta en [Cardmarket](https://www.cardmarket.com)** (campo `LOW` de su guía de precios oficial y gratuita), al que se aplican dos coeficientes configurables:

- **Efectivo**: `LOW × 0,70`
- **Dinero de tienda**: `LOW × 0,805` (es decir, `0,70 × 1,15`)

El dueño de la tienda valida cada lista desde su panel cuando recibe las cartas en persona, pudiendo ajustar el precio por línea si alguna carta no está en buen estado, y marcándola como pagada en efectivo o en dinero de tienda.

## Características

- **Acceso con Google** (OAuth 2.0 / OIDC). Registro abierto: cualquier cuenta Google puede entrar.
- **Listas por TCG**: cada lista se crea para un juego concreto (Magic, Pokémon, One Piece o Gundam); la búsqueda de cartas se filtra automáticamente por ese juego.
- **Buscador de versiones**: escribes el nombre y ves las coincidencias; al pulsar **Enter** se abre un panel con todas las versiones en grande (miniatura, nombre y expansión) para elegir exactamente la tuya.
- **Panel de administración**: listas enviadas, detalle con precios, validación, rechazo con nota, ajuste por línea (override + motivo) y registro de pago (efectivo/tienda).
- **Catálogo local de Cardmarket**: la app descarga a diario los ficheros oficiales gratuitos de [Cardmarket Downloads](https://downloads.s3.cardmarket.com/) para buscar cartas por nombre y conocer su precio `LOW` (oferta activa más barata).
- **Solo cartas sueltas**: el catálogo descarta productos que no son singles jugables (Art Series, tokens, emblemas y oversized), tanto al sincronizar como en la búsqueda.
- **Elección de versión**: escribes el nombre y la búsqueda lista las distintas impresiones (expansión y variante) con una miniatura, sin precios ni referencias; eliges la que coincide con tu carta e indicas cantidad, foil e idioma.
- **Listas por fases**: borrador → enviada → validada/rechazada → pagada, con auditoría de cada cambio.
- **Snapshot de precios**: cada línea guarda el precio y la fecha en que se calculó; si al validar varía más de un umbral configurable, la línea se marca para revisión.
- **Estado y idioma**: todas las cartas se valoran como **NM** (casi perfecta). Cada línea permite elegir **foil** (usa `low-foil`) e **idioma** (español o inglés, informativo para la tienda).
- **Oferta mínima**: si el precio `LOW` es inferior a **5 céntimos**, la oferta es **0 €** (cartas bulk).
- **Precios visibles según rol**: el usuario ve su oferta por carta y los totales; el `LOW` de mercado solo aparece en el panel de administración durante la validación.
- **Imágenes de cartas**: la búsqueda y las listas muestran la imagen de cada carta (se descarga una vez y se cachea en disco); cuando una fuente no la tiene, se muestra un marcador de posición neutro.
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
| `SUPPORTED_GAMES` | IDs de juego de Cardmarket: 1=MTG, 6=Pokémon, 18=One Piece, 24=Gundam |
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

# Aplicar migraciones de la base de datos
python -m alembic upgrade head

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

### Publicarla sin abrir puertos (Raspberry Pi + Tailscale Funnel)

Ideal para una Pi o cualquier equipo Linux siempre encendido detrás de una conexión doméstica:

```bash
# 1. Docker
curl -fsSL https://get.docker.com | sh

# 2. Tailscale y Funnel (login con la misma cuenta del tailnet)
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
sudo tailscale funnel --bg 8002

# 3. La app
git clone https://github.com/ialaboratorio/tradein.git
cd tradein
cp .env.example .env      # rellenar SECRET_KEY, Google y PUBLIC_BASE_URL
docker compose pull && docker compose up -d
```

- En el panel de Tailscale activa **MagicDNS**, **HTTPS Certificates** y **Funnel**.
- La URL pública es `https://<nombre-del-equipo>.<tailnet>.ts.net`; ponla en `PUBLIC_BASE_URL` y añádela a las *Authorized redirect URIs* de Google: `https://<nombre>.<tailnet>.ts.net/auth/google/callback`.
- `SESSION_HTTPS_ONLY=true` para que la cookie viaje solo por HTTPS.

### Opción serverless (Cloud Run + Litestream)

La imagen incluye Litestream: si defines `LITESTREAM_REPLICA_URL` (ej. `gcs://bucket/tradein.db`), al arrancar restaura la base de datos de la réplica y la replica en caliente. `SEED_BUCKET` permite sembrar una base existente la primera vez. En Cloud Run usa `min-instances=0`, `max-instances=1` y monta un bucket GCS en `/app/data/images`.

## Estructura

```
app/
├── main.py          # FastAPI, sesiones, lifespan (sync diario), /health
├── config.py        # Configuración desde .env (pydantic-settings)
├── db.py            # SQLAlchemy 2 + SQLite WAL
├── models.py        # Usuarios, catálogo, precios, expansiones, imágenes, listas, eventos
├── routers/         # auth, cards (búsqueda), images (/img), lists, admin
├── services/        # cardmarket (S3), expansions, images (imágenes de cartas), sync (scheduler), pricing
└── templates/       # Jinja2
alembic/             # Migraciones (0001 users ... 0006 card_images)
tests/               # pytest (62 tests)
```

## Sincronización de precios

- Automática: al arrancar (solo se sincronizan los juegos con datos caducados) y cada día a las 03:30 CET.
- Manual: `python -m app.services.sync` (catálogo + expansiones + precios de los juegos en `SUPPORTED_GAMES`).
- Estado de las últimas sincronizaciones: panel **Admin**.

## Notas sobre los precios y las expansiones

- Fuente de precios: ficheros oficiales y gratuitos de Cardmarket (`price_guide` y `products_singles`), actualizados una vez al día (~02:45 CET). La app sincroniza a las 03:30.
- El campo `LOW` es la oferta activa más barata (mezcla idiomas y condiciones); el precio final de compra lo decide la tienda al validar en persona.
- Cada **impresión** (expansión/arte/variante) es un producto distinto en Cardmarket y tiene su propio `LOW`: por eso la búsqueda lista todas las versiones para que el usuario elija la suya.
- **Filtro de singles**: se descartan los productos que no son cartas sueltas: nombres que empiezan por `Art Series:`, tokens (nombres acabados en `Token` o `Token (...)`; se conservan cartas reales como *Token Collector*), oversized y emblemas (salvo cartas reales como *Leering Emblem*); también los de expansiones de tokens/art series.
- **Nombres de expansión**: Cardmarket no publica un listado de expansiones, así que los nombres se derivan de los ficheros oficiales `products_nonsingles` (productos tipo *booster* de cada set); en Magic se completa con [MTGJSON](https://mtgjson.com). Las expansiones sin nombre disponible se muestran como `Set #id` (cobertura actual de cartas con nombre: MTG 68%, Pokémon 72%, One Piece 86%, Gundam 60%).
- **Límite del idioma**: Cardmarket no publica precios por idioma dentro de una misma impresión (solo existe LOW por producto); para esos casos la tienda puede ajustar el precio manualmente desde el panel de administración.
- Este proyecto **no hace scraping** de cardmarket.com (prohibido por sus términos y bloqueado técnicamente), ni usa su API oficial (cerrada a nuevas aplicaciones). Solo descarga los ficheros públicos de `downloads.s3.cardmarket.com`.
- **Atribución**: "Precios: Cardmarket". Este proyecto no está afiliado a Cardmarket.

## Imágenes de las cartas

- Endpoint único `GET /img/{id_product}`; se usa en las listas, en el buscador y en el panel de versiones (miniaturas grandes al pulsar Enter).
- **Sincronizadas por lotes** (junto con el sync diario): Magic (índice de [Scryfall](https://scryfall.com) con `cardmarket_id` directo) y One Piece (índice de [DotGG](https://dotgg.gg)). Cobertura actual: MTG 98.713 y One Piece 3.583 productos.
- **Resueltas bajo demanda** en la primera petición de cada carta: Pokémon ([Pokémon TCG API](https://pokemontcg.io), nombre + expansión). Gundam aún no tiene fuente pública de imágenes: muestra el marcador de posición.
- **Caché en disco**: las imágenes se guardan en `data/images/` (volumen `./data` en Docker) y se sirven con caché HTTP de 7 días; los fallos se recuerdan 24 h para respetar los límites de las APIs (pokemontcg.io limita a 30 peticiones/min sin clave, y sus errores 500 son intermitentes — se reintenta automáticamente).
- **Fallback**: si una fuente no tiene la imagen o no responde, se sirve un marcador de posición neutro (SVG) sin caché. Cuando no quedan resoluciones libres (8 simultáneas), se sirve un marcador **transitorio** y la siguiente petición (del cliente o de otro visitante) aprovechará el primer hueco para resolverla; el cliente reintenta sola con backoff hasta ~100 s, así que la imagen acaba apareciendo sin recargar la página.
- **Atribución de imágenes**: Scryfall (fan content policy; enlazamos sus URLs de imagen), DotGG (One Piece DB) y Pokémon TCG API. Este proyecto no está afiliado a ninguno de ellos.

## Roadmap

- [x] Fase 1: scaffold, Docker, CI
- [x] Fase 2: acceso con Google + roles
- [x] Fase 3: sincronización Cardmarket + búsqueda de cartas
- [x] Fase 4: listas de venta con coeficientes
- [x] Fase 5: panel de administración
- [x] Fase 6: tests y despliegue
- [x] Fase 7: elección de impresión/expansión con precios por juego y sync completo de los 6 TCG
- [x] Fase 8: imágenes de cartas en búsquedas y listas (Scryfall/DotGG + resolución bajo demanda con fallback)
