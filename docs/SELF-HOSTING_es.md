# Guía de Autoalojamiento de Suna: Edición Simplificada

Esta guía te ayudará a instalar y poner en marcha tu propia copia (instancia) de Suna, un agente de IA de código abierto que puede realizar diversas tareas. ¡Vamos a empezar!

## Tabla de Contenidos

- [¿Qué es Suna? (Descripción General)](#qué-es-suna-descripción-general)
- [Antes de Empezar (Prerrequisitos)](#antes-de-empezar-prerrequisitos)
- [Instalando Suna (Pasos de Instalación)](#instalando-suna-pasos-de-instalación)
- [Si Prefieres Ajustes Manuales (Configuración Manual)](#si-prefieres-ajustes-manuales-configuración-manual)
- [Después de Instalar (Pasos Posteriores a la Instalación)](#después-de-instalar-pasos-posteriores-a-la-instalación)
- [¿Algo no Funciona? (Solución de Problemas)](#algo-no-funciona-solución-de-problemas)

## ¿Qué es Suna? (Descripción General)

Suna es como un asistente digital inteligente. Para funcionar, necesita varias partes que trabajan juntas:

1.  **API Backend**: Es el cerebro de Suna. Se encarga de recibir órdenes, comunicarse con los modelos de lenguaje (IA que procesa texto) y gestionar las tareas. Está hecho con Python y FastAPI.
2.  **Trabajador Backend**: Ayuda al cerebro (API Backend) a realizar tareas más largas o complejas en segundo plano. Usa Python y Dramatiq.
3.  **Frontend**: Es la cara de Suna, la interfaz que tú ves y con la que interactúas en tu navegador web. Hecho con Next.js y React.
4.  **Docker del Agente**: Es un espacio aislado y seguro donde Suna ejecuta las tareas, como si fuera una mini-computadora dentro de tu computadora.
5.  **Base de Datos Supabase**: Aquí es donde Suna guarda toda la información importante, como tus datos de usuario y las tareas pendientes. También maneja el inicio de sesión.

## Antes de Empezar (Prerrequisitos)

Antes de instalar Suna, necesitas preparar algunas cosas:

### 1. Un Lugar para tus Datos: Proyecto Supabase

Supabase es un servicio que usaremos para guardar los datos de Suna.

1.  **Regístrate**: Ve a [Supabase](https://supabase.com/) y crea una cuenta gratuita.
2.  **Nuevo Proyecto**: Dentro de Supabase, crea un "New project" (Nuevo Proyecto). Dale un nombre que recuerdes.
3.  **Guarda esta Información Importante**: Después de crear el proyecto, ve a la configuración del proyecto (busca "Project Settings", luego "API"). Necesitarás:
    *   **URL del Proyecto**: Es una dirección web que se parece a `https://abcdefg.supabase.co`. ¡Cópiala y guárdala!
    *   **Claves API**: Son como contraseñas para que Suna pueda hablar con Supabase.
        *   `anon key` (clave anónima pública): Esta es segura para usar en el frontend.
        *   `service_role key` (clave de rol de servicio): Esta es secreta y muy potente. ¡Guárdala bien!

### 2. Permisos para Servicios Externos: Claves API

Suna necesita conectarse a otros servicios online para funcionar a pleno rendimiento. Para ello, usa "Claves API" (API Keys), que son como pases especiales.

#### Requeridas (Necesitas al menos una de estas para la IA)

-   **Proveedor de Modelo de Lenguaje (LLM)**: Esto es lo que permite a Suna entender y generar texto. Elige uno:
    *   [Anthropic](https://console.anthropic.com/): Recomendado para el mejor rendimiento. Tendrás que registrarte y obtener una clave API.
    *   [OpenAI](https://platform.openai.com/): El creador de ChatGPT. También requiere registro y una clave API.
    *   [Groq](https://console.groq.com/): Conocido por su velocidad.
    *   [OpenRouter](https://openrouter.ai/): Permite acceder a varios modelos a través de una sola clave.
    *   [AWS Bedrock](https://aws.amazon.com/bedrock/): Si ya usas Amazon Web Services.

-   **Búsqueda y Navegación Web**: Para que Suna pueda buscar información en internet y leer páginas web.
    *   [Tavily](https://tavily.com/): Para una búsqueda web mejorada. Consigue una clave API en su web.
    *   [Firecrawl](https://firecrawl.dev/): Para extraer contenido de páginas web. Regístrate para obtener una clave API.

-   **Ejecución Segura de Tareas (Agente)**:
    *   [Daytona](https://app.daytona.io/): Proporciona un entorno seguro para que Suna ejecute código. Necesitarás una cuenta y una clave API.

#### Opcional

-   **RapidAPI**: Si quieres que Suna acceda a aún más herramientas y datos de diferentes APIs (opcional).

### 3. Programas Necesarios en tu Computadora (Software Requerido)

Asegúrate de tener estos programas. No te preocupes, el instalador de Suna (`setup.py`) intentará ayudarte a instalarlos si usas Windows.

*Nota para usuarios de Windows: Es una buena idea hacer clic derecho en la terminal o consola y elegir "Ejecutar como administrador" antes de correr `python setup.py`. Esto ayuda a que la instalación automática de programas funcione mejor.*

-   **[Git](https://git-scm.com/downloads)**: Es una herramienta para descargar y gestionar versiones de código, como el de Suna.
    *   *Instalación*: Ve al enlace y descarga la versión para tu sistema operativo. El asistente `setup.py` puede intentar instalarlo con `winget` en Windows.
-   **[Docker](https://docs.docker.com/get-docker/)**: Permite empaquetar Suna y sus partes en "contenedores" para que funcionen en cualquier lugar.
    *   *Instalación*: Para Windows, necesitas instalar Docker Desktop manualmente desde su web. El script `setup.py` te dará instrucciones, incluyendo cómo activar WSL2 y la virtualización, que son tecnologías necesarias para Docker en Windows.
-   **[Python 3.11](https://www.python.org/downloads/)**: Es el lenguaje de programación en el que está escrito el "cerebro" de Suna.
    *   *Instalación*: Descárgalo desde el enlace. Asegúrate de marcar la casilla que dice "Add Python to PATH" durante la instalación. `setup.py` puede intentar instalarlo con `winget` en Windows.
-   **[Poetry](https://python-poetry.org/docs/#installation)**: Ayuda a gestionar las "librerías" (código extra) que Python necesita para Suna.
    *   *Instalación*: Sigue las instrucciones en su web. `setup.py` puede intentar instalarlo en Windows.
-   **[Node.js y npm](https://nodejs.org/en/download/)**: Se usan para la parte "Frontend" (la interfaz de usuario) de Suna. npm viene con Node.js.
    *   *Instalación*: Descárgalo desde el enlace. `setup.py` puede intentar instalarlo con `winget` en Windows.
-   **[Supabase CLI](https://supabase.com/docs/guides/local-development/cli/getting-started)**: Es una herramienta de línea de comandos para trabajar con tu base de datos Supabase.
    *   *Instalación*: Se instala con npm (que viene con Node.js). Abre una terminal y escribe: `npm install -g supabase`. `setup.py` también puede intentar esto.
-   **[Tesseract OCR](https://github.com/UB-Mannheim/tesseract/wiki)**: Permite a Suna "leer" texto de imágenes (Reconocimiento Óptico de Caracteres).
    *   *Instalación*: `setup.py` intentará instalarlo en Windows con `winget`. Si no, sigue la guía en su wiki. **Importante**: Después de instalar Tesseract, necesitas añadir la carpeta donde se instaló a la variable de entorno PATH de tu sistema para que Suna lo encuentre. El instalador te guiará en esto.

## Instalando Suna (Pasos de Instalación)

¡Ahora vamos a instalar Suna!

### 1. Descargar el Código de Suna (Clonar el Repositorio)

Abre tu terminal o línea de comandos y escribe estos dos comandos, uno después del otro:

```bash
git clone https://github.com/kortix-ai/suna.git
```
Este comando descarga el código fuente de Suna en una carpeta llamada `suna`.

```bash
cd suna
```
Este comando te mueve dentro de la carpeta `suna` que acabas de descargar.

### 2. Usar el Mago de Instalación (Asistente de Configuración)

Hemos creado un script que te ayuda con la instalación. En tu terminal, dentro de la carpeta `suna`, escribe:

```bash
python setup.py
```
Este "mago" o asistente hará varias cosas:
-   Verificará si tienes todos los programas necesarios.
-   Te pedirá las claves API que conseguiste antes y otra información.
-   Preparará tu base de datos Supabase.
-   Creará archivos de configuración especiales.
-   Instalará todas las librerías de código que Suna necesita.
-   Te dará opciones para iniciar Suna.

*Un pequeño consejo*: Si el asistente instala algún programa nuevo (como Poetry o Supabase CLI) y luego parece que la computadora no lo encuentra (ves errores como "comando no reconocido"), cierra la terminal, ábrela de nuevo, y vuelve a la carpeta `suna` para ejecutar `python setup.py` otra vez. A veces, la terminal necesita reiniciarse para "ver" los programas nuevos.

### 3. Preparar Supabase (Configuración de Supabase)

Durante el asistente `setup.py`, o si lo haces manualmente, tendrás que hacer esto con Supabase:

1.  **Iniciar Sesión en Supabase CLI**: El asistente te podría pedir que ejecutes `supabase login`. Esto conecta tu computadora a tu cuenta de Supabase. Necesitarás un "Access Token" (Token de Acceso) que generas en la web de Supabase (en `Account > Access Tokens`).
2.  **Conectar tu Proyecto**: El comando `supabase link --project-ref TU_ID_DE_PROYECTO` (reemplaza `TU_ID_DE_PROYECTO` con el ID de tu proyecto Supabase, que está en la URL del panel de Supabase o en `Project Settings > General`) conecta la carpeta local con tu proyecto en la nube de Supabase.
3.  **Actualizar la Base de Datos**: El comando `supabase db push` aplica la estructura necesaria (tablas, etc.) a tu base de datos Supabase.
4.  **Permitir Acceso al Esquema 'basejump'**:
    *   Ve a tu panel de control de Supabase en la web.
    *   Entra a tu proyecto.
    *   Ve a "Project Settings" (Configuración del Proyecto) → "API".
    *   Busca la sección "Exposed schemas" (Esquemas Expuestos).
    *   Asegúrate de que `basejump` esté listado ahí. Si no, añádelo. Un "esquema" es como una carpeta dentro de tu base de datos, y `basejump` contiene elementos importantes para la gestión de usuarios.

### 4. Preparar Daytona (Configuración de Daytona)

Daytona se usa para ejecutar las tareas del agente de forma segura.

1.  **Crea tu Cuenta**: Ve a [Daytona](https://app.daytona.io/) y regístrate.
2.  **Consigue tu Clave API**: En tu panel de Daytona, genera una clave API.
3.  **Prepara una Plantilla de Agente (Imagen Docker)**: Suna necesita una "receta" para decirle a Daytona cómo crear el entorno seguro para el agente.
    *   **Nombre de la Imagen**: `kortix/suna:0.1.2.8` (este es un nombre específico que Suna buscará).
    *   **Comando de Inicio (Entrypoint)**: `/usr/bin/supervisord -n -c /etc/supervisor/conf.d/supervisord.conf` (este comando inicia los servicios necesarios dentro del entorno seguro).
    El asistente `setup.py` podría ayudarte con esto o darte más instrucciones.

## Si Prefieres Ajustes Manuales (Configuración Manual)

Si el asistente `setup.py` no te funciona o quieres tener más control, puedes configurar Suna manualmente. Esto implica editar unos archivos de texto especiales llamados archivos `.env`. Estos archivos guardan configuraciones importantes para que no tengas que escribirlas cada vez.

### Configuración del Cerebro (Backend .env)

El archivo se encuentra en `backend/.env`. Si no existe, puedes copiar `backend/.env.example` y renombrarlo a `.env`. Contiene:

```sh
# Modo de Entorno: 'local' para tu computadora, 'production' para un servidor real.
ENV_MODE=local

# --- Base de Datos Supabase ---
# Pega aquí la URL y las claves que guardaste de Supabase.
SUPABASE_URL=https://TU_PROYECTO.supabase.co 
SUPABASE_ANON_KEY=TU_CLAVE_ANONIMA_PUBLICA
SUPABASE_SERVICE_ROLE_KEY=TU_CLAVE_SECRETA_DE_SERVICIO

# --- Redis (cache interna) ---
# Usualmente no necesitas cambiar esto si usas Docker.
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_PASSWORD= # Déjalo vacío si no tiene contraseña
REDIS_SSL=false

# --- RabbitMQ (sistema de mensajería interna) ---
# Usualmente no necesitas cambiar esto si usas Docker.
RABBITMQ_HOST=rabbitmq
RABBITMQ_PORT=5672

# --- Proveedores de IA (LLM) ---
# Pon tus claves API aquí. Solo necesitas una, pero puedes poner varias.
ANTHROPIC_API_KEY=TU_CLAVE_DE_ANTHROPIC
OPENAI_API_KEY=TU_CLAVE_DE_OPENAI
# Elige qué modelo usar por defecto. Ejemplo: anthropic/claude-3-opus-20240229
MODEL_TO_USE=anthropic/claude-3-haiku-20240307 # Un modelo rápido para empezar

# --- Búsqueda Web ---
TAVILY_API_KEY=TU_CLAVE_DE_TAVILY

# --- Extracción de Contenido Web ---
FIRECRAWL_API_KEY=TU_CLAVE_DE_FIRECRAWL
FIRECRAWL_URL=https://api.firecrawl.dev # Usualmente no se cambia

# --- Ejecución Segura de Tareas (Daytona) ---
DAYTONA_API_KEY=TU_CLAVE_DE_DAYTONA
DAYTONA_SERVER_URL=https://app.daytona.io/api # Usualmente no se cambia
DAYTONA_TARGET=us # Puede cambiar según tu región o configuración de Daytona

# Dirección web pública de tu Suna (si la accedes desde otra máquina o la publicas)
# Para pruebas locales, http://localhost:3000 está bien.
NEXT_PUBLIC_URL=http://localhost:3000 
```

### Configuración de la Interfaz de Usuario (Frontend .env.local)

El archivo está en `frontend/.env.local`. Si no existe, copia `frontend/.env.example` y renómbralo a `.env.local`.

```sh
# Pega aquí la URL y la clave anónima pública de Supabase.
NEXT_PUBLIC_SUPABASE_URL=https://TU_PROYECTO.supabase.co
NEXT_PUBLIC_SUPABASE_ANON_KEY=TU_CLAVE_ANONIMA_PUBLICA

# Dirección donde el frontend puede encontrar el backend.
# Si usas Docker, 'http://backend:8000/api' es común.
# Si ejecutas el backend manualmente en tu PC, podría ser 'http://localhost:8000/api'.
NEXT_PUBLIC_BACKEND_URL=http://backend:8000/api 

# Dirección pública de la interfaz. Para pruebas locales, http://localhost:3000 está bien.
NEXT_PUBLIC_URL=http://localhost:3000
```

## Después de Instalar (Pasos Posteriores a la Instalación)

¡Felicidades si llegaste hasta aquí! Ya casi lo tienes:

1.  **Crea tu Cuenta de Usuario**: Abre Suna en tu navegador (normalmente `http://localhost:3000`). Deberías ver una opción para registrarte o iniciar sesión. Usa el sistema de Supabase para crear tu primera cuenta.
2.  **Revisa que Todo Esté en Marcha**: Asegúrate de que Suna funcione, puedas interactuar con el agente y no veas errores obvios.

## Opciones de Inicio: Cómo Encender Suna

Hay dos formas principales de iniciar todos los componentes de Suna:

### 1. Con Docker Compose (Recomendado y Más Fácil)

Docker Compose lee un archivo especial (`docker-compose.yml`) que define todos los servicios de Suna (backend, frontend, Redis, etc.) y los inicia juntos. Es la forma más sencilla.

En tu terminal, desde la carpeta `suna`, ejecuta:
```bash
docker compose up -d
```
*Explicación del comando*:
* `docker compose`: La herramienta para manejar aplicaciones con múltiples contenedores Docker.
* `up`: Significa "iniciar los servicios".
* `-d`: Significa "detached mode" o modo desconectado. Esto hace que Suna se ejecute en segundo plano para que puedas seguir usando la terminal.

Para detener Suna más tarde, usa:
```bash
docker compose down
```
Alternativamente, el script `python start.py` (si lo usaste para configurar) también puede iniciar y detener Suna con Docker Compose.

### 2. Inicio Manual (Para Entender las Partes o si Docker Compose Falla)

Esto te da más control pero requiere abrir varias terminales. Asegúrate de estar en la carpeta raíz `suna` en cada terminal antes de moverte a las subcarpetas.

1.  **Inicia los Servicios de Soporte (Redis y RabbitMQ)**:
    Estos son necesarios para el backend. Puedes iniciarlos con Docker si quieres:
    ```bash
    docker compose up redis rabbitmq -d 
    ```
    (Esto inicia solo los contenedores `redis` y `rabbitmq` definidos en el `docker-compose.yml`).

2.  **Inicia el Frontend (Interfaz de Usuario)**:
    Abre una **nueva terminal**.
    ```bash
    cd frontend
    npm run dev
    ```
    Esto iniciará un servidor de desarrollo para la interfaz, usualmente en `http://localhost:3000`.

3.  **Inicia el Backend (API - El Cerebro)**:
    Abre **otra nueva terminal**.
    ```bash
    cd backend
    poetry run python3.11 api.py
    ```
    Esto inicia el servidor principal de Suna, usualmente en el puerto 8000.

4.  **Inicia el Trabajador del Backend (Procesos en Segundo Plano)**:
    Abre **una terminal más**.
    ```bash
    cd backend
    poetry run python3.11 -m dramatiq run_agent_background
    ```
    Esto inicia el sistema que maneja las tareas largas del agente.

## ¿Algo no Funciona? (Solución de Problemas)

No te preocupes, los problemas ocurren. Aquí algunas ideas:

### Problemas Comunes

1.  **Los servicios de Docker no arrancan**:
    *   **Mira los mensajes (logs) de Docker**: En la terminal, en la carpeta `suna`, escribe `docker compose logs`. Esto te mostrará los últimos mensajes de todos los servicios. Busca errores. Si quieres ver los mensajes en tiempo real de un servicio específico, usa `docker compose logs -f nombre_del_servicio` (ej: `docker compose logs -f backend`).
    *   **¿Está Docker funcionando?**: Asegúrate de que la aplicación Docker Desktop esté iniciada y corriendo.
    *   **Puertos ocupados**: Suna usa puertos como el 3000 (frontend) y 8000 (backend). Si otro programa ya los usa, Suna no podrá iniciar. El mensaje de error te dirá si un puerto ya está en uso ("port already allocated" o similar). Intenta cerrar el otro programa o cambiar los puertos en los archivos `.env` y `docker-compose.yml` (esto es más avanzado).

2.  **No puedo conectar a la base de datos (Supabase)**:
    *   **Revisa las credenciales**: Asegúrate de que `SUPABASE_URL`, `SUPABASE_ANON_KEY`, y `SUPABASE_SERVICE_ROLE_KEY` en `backend/.env` (y las equivalentes en `frontend/.env.local`) son correctas. Un error al copiar y pegar es común.
    *   **¿Expusiste el esquema 'basejump'?**: Revisa el Paso 3 de la "Configuración de Supabase". Este es un error frecuente.

3.  **Problemas con las claves API de los LLM (Anthropic, OpenAI, etc.)**:
    *   **Claves correctas**: Verifica que las claves API estén bien escritas en `backend/.env`.
    *   **Límites de tu cuenta**: Algunos servicios tienen límites de uso (cuotas) en sus planes gratuitos o de prueba. Revisa tu panel en el servicio del LLM para ver si has alcanzado tu límite.
    *   **Modelo correcto**: Asegúrate que `MODEL_TO_USE` en `backend/.env` sea un modelo al que tu clave API tiene acceso.

4.  **Problemas con Daytona**:
    *   **Clave API de Daytona**: Confirma que `DAYTONA_API_KEY` en `backend/.env` es correcta.
    *   **Configuración de la imagen**: Asegúrate de que la imagen Docker (`kortix/suna:0.1.2.8`) y el entrypoint estén bien configurados en tu plataforma Daytona si se te pidió hacerlo manualmente.

### Dónde Encontrar Más Ayuda (Logs)

Los "logs" son registros de lo que hacen los programas. Son muy útiles para entender qué va mal.

```bash
# Para ver todos los logs si usas Docker Compose (recomendado)
# (ejecuta desde la carpeta 'suna')
docker compose logs -f

# Si iniciaste el Frontend manualmente:
# Verás los logs en la terminal donde ejecutaste 'npm run dev'.

# Si iniciaste el Backend (API) manualmente:
# Verás los logs en la terminal donde ejecutaste 'poetry run python3.11 api.py'.

# Si iniciaste el Trabajador del Backend manualmente:
# Verás los logs en la terminal donde ejecutaste 'poetry run python3.11 -m dramatiq run_agent_background'.
```

---

Si sigues teniendo problemas, ¡no estás solo! Puedes:
-   Unirte a la [Comunidad de Discord de Suna](https://discord.gg/Py6pCBUUPw) y preguntar.
-   Consultar la sección de "Issues" (Problemas) en el [repositorio de GitHub de Suna](https://github.com/kortix-ai/suna) por si alguien más tuvo tu problema, o para reportar uno nuevo.

¡Mucha suerte con tu autoalojamiento de Suna!
